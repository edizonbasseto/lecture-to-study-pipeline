"""Digest attestation for local GGUF model files used as inference actors.

Every model file that may act as a bake-off candidate or a figure-captioning
worker is recorded here by its exact SHA-256, its byte size, and the source
repository the operator confirmed for it, before any inference runs against
it (see Task 1 of this plan). Split-shard suffixes are rejected: an
attested actor must be one whole file, not a slice of a sharded download.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from lecture_study.quality.trust import canonical_json_bytes

_READ_CHUNK_BYTES = 1024 * 1024
_GGUF_SUFFIX = ".gguf"

_SPLIT_GGUF_PATTERN = re.compile(
    r"-[0-9]{1,6}-of-[0-9]{1,6}\.gguf\Z",
    re.IGNORECASE,
)

_Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_ConfirmedSourceRepository = Annotated[str, StringConstraints(min_length=1)]


class SplitGgufRejectedError(RuntimeError):
    """Raised when a shard-suffixed GGUF file is presented for attestation."""


class ModelAttestation(BaseModel):
    """One local GGUF file, attested by digest against a confirmed source."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    path: str
    sha256: _Sha256Digest
    size_bytes: int
    source_repository: _ConfirmedSourceRepository
    attested_at: str


def attest_model_file(path: Path, source_repository: str) -> ModelAttestation:
    """Attest one local GGUF file against its confirmed source repository.

    Raises `SplitGgufRejectedError` for a shard-suffixed filename, and
    `ValueError` for a non-GGUF suffix, a symbolic link, or a missing
    confirmed source repository.
    """
    if not source_repository:
        raise ValueError(
            "source_repository must be the operator-confirmed repository identifier"
        )
    _validate_attestable_path(path)
    file_status = path.lstat()
    if not stat.S_ISREG(file_status.st_mode):
        raise ValueError(f"model path must identify a regular file: {path}")
    return ModelAttestation(
        path=str(path),
        sha256=_sha256_file(path),
        size_bytes=file_status.st_size,
        source_repository=source_repository,
        attested_at=datetime.now(timezone.utc).isoformat(),
    )


def _validate_attestable_path(path: Path) -> None:
    if path.suffix.lower() != _GGUF_SUFFIX:
        raise ValueError(f"model path must identify one GGUF file: {path}")
    if _SPLIT_GGUF_PATTERN.search(path.name):
        raise SplitGgufRejectedError(
            f"split GGUF models require merging before attestation: {path}"
        )
    if path.is_symlink():
        raise ValueError(f"model path must not be a symbolic link: {path}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def render_actor_inventory_json(attestations: Sequence[ModelAttestation]) -> bytes:
    """Render attestations as canonical, sorted, deterministic JSON bytes.

    Sorting by `path` (rather than trusting caller order) is what makes the
    output deterministic: calling this twice with the same attestations in
    a different order produces byte-identical output.
    """
    sorted_entries = sorted(attestations, key=lambda attestation: attestation.path)
    payload = {
        "schema_version": 1,
        "actors": [entry.model_dump(mode="json") for entry in sorted_entries],
    }
    return canonical_json_bytes(payload)


def write_actor_inventory(path: Path, attestations: Sequence[ModelAttestation]) -> None:
    """Write the actor inventory atomically (temp file, fsync, then replace)."""
    payload = render_actor_inventory_json(attestations)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_actor_inventory(path: Path) -> tuple[ModelAttestation, ...]:
    """Load a previously written actor inventory."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        ModelAttestation.model_validate(entry) for entry in raw.get("actors", [])
    )


def recompute_and_compare(attestation: ModelAttestation) -> str | None:
    """Recompute an attested file's digest and return a mismatch reason, if any."""
    path = Path(attestation.path)
    if not path.is_file() or path.is_symlink():
        return f"{attestation.path}: file is missing or is a symbolic link"
    current_digest = _sha256_file(path)
    if current_digest != attestation.sha256:
        return (
            f"{attestation.path}: recorded digest {attestation.sha256} differs "
            f"from recomputed digest {current_digest}"
        )
    if not attestation.source_repository:
        return f"{attestation.path}: source repository is empty"
    return None


def merge_digest_differs_from_shards(
    merged: ModelAttestation,
    shards: Sequence[ModelAttestation],
) -> bool:
    """Return True when the merged file's digest differs from every shard's."""
    shard_digests = {shard.sha256 for shard in shards}
    return merged.sha256 not in shard_digests
