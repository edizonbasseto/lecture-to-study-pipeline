"""Provenance run record: which adapter and model produced a script's output.

The record is a metadata-only sidecar (D-13 as amended by D-18). It lives at
``workspace/data/llm_runs/<lecture_id>/<script_stage>.json``, outside every
stage output directory: the progress dashboard counts the entries of its fixed
stage directories and ``quality/delivery.py`` fingerprints only its own
explicit output roots, so neither ever sees this tree.

Prompt and response bytes are never stored. ``RecordingGateway`` keeps
counters only, and the record holds identifiers, digests, timestamps and
durations.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from lecture_study.domain.models import SafeIdentifier
from lecture_study.llm.gateway import (
    AdapterName,
    GatewayIdentity,
    LlmGateway,
    LlmRequest,
    LlmResponse,
    Stage,
)

LLM_RUNS_ROOT = Path("workspace/data/llm_runs")

_HASH_CHUNK_BYTES = 1024 * 1024
_LECTURE_ID_ADAPTER = TypeAdapter(SafeIdentifier)
_logger = logging.getLogger(__name__)

RunStatus = Literal["succeeded", "failed"]
RunScope = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.=-]{1,64}$")]
_SCOPE_ADAPTER: TypeAdapter[RunScope | None] = TypeAdapter(RunScope | None)


class GatewayUsage(BaseModel):
    """Identity and call counters of one gateway used during a run."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    stage: Stage
    adapter_name: AdapterName
    model_identifier: str
    model_sha256: str | None = Field(
        default=None,
        description=(
            "SHA-256 hex digest of the configured model_path file only; null "
            "when the run failed (the digest is not computed on failure paths) "
            "or the file is unreadable. For a "
            "split GGUF model, whose model_path names the first shard, it "
            "identifies the first shard; every shard is integrity-checked at "
            "provisioning by its pinned per-shard digest, not by this record."
        ),
    )
    engine_version: str | None = None
    calls: int = Field(ge=0)
    retry_calls: int = Field(ge=0)
    call_seconds: float = Field(ge=0)


class LlmRunRecord(BaseModel):
    """Metadata-only record of one script run (no prompt or response text)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    record_version: Literal[1] = 1
    lecture_id: SafeIdentifier
    script_stage: Stage
    schema_name: str
    status: RunStatus
    scope: RunScope | None = Field(
        default=None,
        description=(
            "Which part of the stage outputs this run covered, for example "
            "pack_sequence=3; null means the whole stage."
        ),
    )
    started_at: datetime
    finished_at: datetime
    wall_seconds: float = Field(ge=0)
    gateways: tuple[GatewayUsage, ...]


class RecordingGateway:
    """Gateway decorator that counts calls and time, never content."""

    def __init__(self, inner: LlmGateway, *, stage: Stage) -> None:
        self._inner = inner
        self._stage = stage
        self._calls = 0
        self._retry_calls = 0
        self._call_seconds = 0.0

    def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if request.is_retry:
            self._retry_calls += 1
        started = time.monotonic()
        try:
            return self._inner.complete(request)
        finally:
            self._call_seconds += time.monotonic() - started

    def describe(self) -> GatewayIdentity:
        return self._inner.describe()

    def usage(self, *, model_sha256: str | None) -> GatewayUsage:
        identity = self.describe()
        return GatewayUsage(
            stage=self._stage,
            adapter_name=identity.adapter_name,
            model_identifier=identity.model_identifier,
            model_sha256=model_sha256,
            engine_version=identity.engine_version,
            calls=self._calls,
            retry_calls=self._retry_calls,
            call_seconds=self._call_seconds,
        )


class LlmRunRecorder:
    """Collects the gateways used by one run so their usage can be recorded."""

    def __init__(self) -> None:
        self._gateways: list[RecordingGateway] = []

    def track(self, gateway: LlmGateway, *, stage: Stage) -> RecordingGateway:
        recording_gateway = RecordingGateway(gateway, stage=stage)
        self._gateways.append(recording_gateway)
        return recording_gateway

    def usages(self, *, hash_models: bool = True) -> tuple[GatewayUsage, ...]:
        digests: dict[Path, str | None] = {}
        return tuple(
            gateway.usage(
                model_sha256=(
                    _digest_once(gateway.describe(), digests) if hash_models else None
                )
            )
            for gateway in self._gateways
        )


def run_record_path(root: Path, lecture_id: str, script_stage: Stage) -> Path:
    return root / lecture_id / f"{script_stage.value}.json"


@contextmanager
def record_llm_run(
    *,
    lecture_id: str,
    script_stage: Stage,
    schema_name: str,
    root: Path = LLM_RUNS_ROOT,
    scope: str | None = None,
) -> Iterator[LlmRunRecorder]:
    """Write the run record when the block ends, whether or not it failed.

    A failure while recording a failed run is logged and never replaces the
    exception that ended the block.
    """

    safe_lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    run = _RunClock(
        lecture_id=safe_lecture_id,
        script_stage=script_stage,
        schema_name=schema_name,
        path=run_record_path(root, safe_lecture_id, script_stage),
        scope=_SCOPE_ADAPTER.validate_python(scope),
    )
    recorder = LlmRunRecorder()
    try:
        yield recorder
    except BaseException:
        _write_failed_record_without_masking(run, recorder)
        raise
    _write_atomically(run.path, run.record("succeeded", recorder.usages()))


@dataclass(frozen=True)
class _RunClock:
    """Identity and start time of one run, used to build its closing record."""

    lecture_id: str
    script_stage: Stage
    schema_name: str
    path: Path
    scope: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started: float = field(default_factory=time.monotonic)

    def record(
        self, status: RunStatus, gateways: tuple[GatewayUsage, ...]
    ) -> LlmRunRecord:
        return LlmRunRecord(
            lecture_id=self.lecture_id,
            script_stage=self.script_stage,
            schema_name=self.schema_name,
            status=status,
            scope=self.scope,
            started_at=self.started_at,
            finished_at=datetime.now(UTC),
            wall_seconds=time.monotonic() - self.started,
            gateways=gateways,
        )


def _write_failed_record_without_masking(
    run: _RunClock, recorder: LlmRunRecorder
) -> None:
    """Record a failed run; a sidecar problem is logged, never raised.

    An earlier succeeded record stays in place: its outputs are still on disk
    and a failed rerun must not misattribute them (WR-05). The model digest is skipped (``model_sha256`` stays null): hashing a
    multi-GB model must not delay an interrupt or a refusal that never touched
    the model, and the failure being propagated stays the one the operator sees.
    """

    try:
        if _holds_successful_record(run.path):
            _logger.warning(
                "failed run kept the earlier successful record: %s", run.path
            )
            return
        record = run.record("failed", recorder.usages(hash_models=False))
        _write_atomically(run.path, record)
    except Exception:
        _logger.warning("failed run record not written: %s", run.path)


def _holds_successful_record(path: Path) -> bool:
    """True when ``path`` already holds a succeeded record a failure must not erase."""

    try:
        existing = LlmRunRecord.model_validate_json(
            path.read_text(encoding="utf-8"), strict=False
        )
    except (OSError, ValueError):
        return False
    return existing.status == "succeeded"


def _digest_once(
    identity: GatewayIdentity, digests: dict[Path, str | None]
) -> str | None:
    model_path = identity.model_path
    if model_path is None:
        return None
    if model_path not in digests:
        digests[model_path] = _file_sha256(model_path)
    return digests[model_path]


def _file_sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        _logger.warning("model digest skipped: cannot read %s", path)
        return None
    return digest.hexdigest()


def _write_atomically(path: Path, record: LlmRunRecord) -> None:
    """Publish ``record`` durably: unique temp file, fsync, replace, fsync dir."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(record.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        _logger.warning("run record directory not synced: %s", directory)
    finally:
        os.close(descriptor)
