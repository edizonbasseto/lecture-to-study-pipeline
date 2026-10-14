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
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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


class GatewayUsage(BaseModel):
    """Identity and call counters of one gateway used during a run."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    stage: Stage
    adapter_name: AdapterName
    model_identifier: str
    model_sha256: str | None = Field(
        default=None,
        description=(
            "SHA-256 hex digest of the configured model_path file only. For a "
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

    def usages(self) -> tuple[GatewayUsage, ...]:
        digests: dict[Path, str | None] = {}
        return tuple(
            gateway.usage(model_sha256=_digest_once(gateway.describe(), digests))
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
) -> Iterator[LlmRunRecorder]:
    """Write the run record when the block ends, whether or not it failed."""

    safe_lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    recorder = LlmRunRecorder()
    started_at = datetime.now(UTC)
    started = time.monotonic()
    status: RunStatus = "failed"
    try:
        yield recorder
        status = "succeeded"
    finally:
        finished_at = datetime.now(UTC)
        wall_seconds = time.monotonic() - started
        record = LlmRunRecord(
            lecture_id=safe_lecture_id,
            script_stage=script_stage,
            schema_name=schema_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            wall_seconds=wall_seconds,
            gateways=recorder.usages(),
        )
        _write_atomically(run_record_path(root, safe_lecture_id, script_stage), record)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        temporary_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
