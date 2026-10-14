"""Immutable results and provenance for authenticated source-content resolution."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.source_universe import EvidenceModality
from lecture_study.domain.versioning import SchemaVersionOne

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_NonNegativeInteger = Annotated[int, Field(ge=0)]


def require_canonical_relative_posix_path(value: str) -> str:
    """Return one normalized path that cannot escape its injected artifact root."""

    if not value or "\\" in value or "\x00" in value:
        raise ValueError("value must be a canonical relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("value must be a canonical relative POSIX path")
    return value


def relative_posix_parts(value: str) -> tuple[str, ...]:
    """Split a validated relative POSIX path for descriptor-relative access."""

    return PurePosixPath(require_canonical_relative_posix_path(value)).parts


class SourceRecordProvenance(BaseModel):
    """Authenticated manifest and byte-range identity for one source record."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    manifest_relative_path: str
    manifest_sha256: _Digest
    manifest_signature_sha256: _Digest
    trust_key_id: _Digest
    lecture_id: SafeIdentifier
    source_artifact_id: SafeIdentifier
    artifact_relative_path: str
    artifact_sha256: _Digest
    artifact_byte_size: _NonNegativeInteger
    source_record_id: SafeIdentifier
    modality: EvidenceModality
    byte_offset: _NonNegativeInteger
    byte_length: _NonNegativeInteger
    content_sha256: _Digest
    media_type: _NonBlankText

    @field_validator("manifest_relative_path", "artifact_relative_path")
    @classmethod
    def _require_relative_paths(cls, value: str) -> str:
        return require_canonical_relative_posix_path(value)

    @model_validator(mode="after")
    def _require_record_inside_artifact(self) -> SourceRecordProvenance:
        if self.byte_offset + self.byte_length > self.artifact_byte_size:
            raise ValueError("source record byte range exceeds its artifact")
        return self


class ResolvedSourceContent(BaseModel):
    """Exact authenticated record bytes plus lossless text when applicable."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    provenance: SourceRecordProvenance
    raw_bytes: bytes
    text: str | None

    @model_validator(mode="after")
    def _require_exact_content(self) -> ResolvedSourceContent:
        if len(self.raw_bytes) != self.provenance.byte_length:
            raise ValueError(
                "resolved byte length differs from authenticated provenance"
            )
        observed_digest = hashlib.sha256(self.raw_bytes).hexdigest()
        if observed_digest != self.provenance.content_sha256:
            raise ValueError("resolved bytes differ from authenticated provenance")
        if self.provenance.modality is EvidenceModality.VISUAL:
            if self.text is not None:
                raise ValueError("visual source content must not declare decoded text")
            return self
        try:
            decoded = self.raw_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("text source content must contain strict UTF-8") from error
        if self.text != decoded:
            raise ValueError("decoded text must preserve the exact source bytes")
        return self
