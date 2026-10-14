"""Inward-facing ports and use case for authenticated source-content resolution."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from lecture_study.domain.source_content import (
    ResolvedSourceContent,
    SourceRecordProvenance,
)
from lecture_study.domain.source_universe import EvidenceModality, SourceEvidenceSpan


class SourceContentResolutionError(RuntimeError):
    """Raised when exact authenticated source content cannot be resolved."""


@dataclass(frozen=True, slots=True)
class SourceContentLimits:
    """Caller-supplied resource limits with no production defaults."""

    maximum_manifest_bytes: int
    maximum_artifact_bytes: int
    maximum_record_bytes: int
    maximum_artifacts: int
    maximum_records_per_artifact: int

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")


class SourceRecordCatalog(Protocol):
    """Resolve an authenticated manifest record for one authoritative span."""

    def locate(self, span: SourceEvidenceSpan) -> SourceRecordProvenance: ...


class SourceArtifactReader(Protocol):
    """Read one exact artifact below an explicitly configured root."""

    def read_file(self, relative_path: str, *, maximum_bytes: int) -> bytes: ...


class SourceContentResolver(Protocol):
    """Resolve exact source bytes without exposing storage or trust details."""

    def resolve(self, span: SourceEvidenceSpan) -> ResolvedSourceContent: ...


class ResolveSourceContent:
    """Verify authenticated provenance against current whole-file and record bytes."""

    def __init__(
        self,
        *,
        catalog: SourceRecordCatalog,
        artifact_reader: SourceArtifactReader,
        limits: SourceContentLimits,
    ) -> None:
        self._catalog = catalog
        self._artifact_reader = artifact_reader
        self._limits = limits

    def resolve(self, span: SourceEvidenceSpan) -> ResolvedSourceContent:
        validated_span = self._validate_span(span)
        provenance = self._validate_provenance(
            self._catalog.locate(validated_span),
            validated_span,
        )
        artifact_bytes = self._artifact_reader.read_file(
            provenance.artifact_relative_path,
            maximum_bytes=self._limits.maximum_artifact_bytes,
        )
        self._verify_whole_artifact(artifact_bytes, provenance)
        record_bytes = self._extract_record(artifact_bytes, provenance)
        self._verify_record(record_bytes, provenance)
        text = self._decode_text(record_bytes, provenance.modality)
        try:
            return ResolvedSourceContent(
                provenance=provenance,
                raw_bytes=record_bytes,
                text=text,
            )
        except ValidationError as error:
            raise SourceContentResolutionError(
                "resolved source content violates its authenticated contract"
            ) from error

    @staticmethod
    def _validate_span(span: SourceEvidenceSpan) -> SourceEvidenceSpan:
        if not isinstance(span, SourceEvidenceSpan):
            raise SourceContentResolutionError("source span must be typed")
        try:
            return SourceEvidenceSpan.model_validate(
                span.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            raise SourceContentResolutionError("source span is invalid") from error

    def _validate_provenance(
        self,
        provenance: SourceRecordProvenance,
        span: SourceEvidenceSpan,
    ) -> SourceRecordProvenance:
        try:
            validated = SourceRecordProvenance.model_validate(
                provenance.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            raise SourceContentResolutionError(
                "source record provenance is invalid"
            ) from error
        expected_identity = (
            span.lecture_id,
            span.source_artifact_id,
            span.source_record_id,
        )
        observed_identity = (
            validated.lecture_id,
            validated.source_artifact_id,
            validated.source_record_id,
        )
        if observed_identity != expected_identity:
            raise SourceContentResolutionError(
                "source record provenance does not match the requested identity"
            )
        if validated.modality is not span.modality:
            raise SourceContentResolutionError(
                "source record modality does not match the requested modality"
            )
        if not hmac.compare_digest(validated.content_sha256, span.content_sha256):
            raise SourceContentResolutionError(
                "source record content hash does not match the authoritative span"
            )
        if validated.artifact_byte_size > self._limits.maximum_artifact_bytes:
            raise SourceContentResolutionError(
                "source artifact exceeds the injected maximum size limit"
            )
        if validated.byte_length > self._limits.maximum_record_bytes:
            raise SourceContentResolutionError(
                "source record exceeds the injected maximum size limit"
            )
        return validated

    @staticmethod
    def _verify_whole_artifact(
        artifact_bytes: bytes,
        provenance: SourceRecordProvenance,
    ) -> None:
        if not isinstance(artifact_bytes, bytes):
            raise SourceContentResolutionError(
                "source artifact reader returned non-bytes"
            )
        if len(artifact_bytes) != provenance.artifact_byte_size:
            raise SourceContentResolutionError(
                "source artifact size differs from authenticated provenance"
            )
        observed_digest = hashlib.sha256(artifact_bytes).hexdigest()
        if not hmac.compare_digest(observed_digest, provenance.artifact_sha256):
            raise SourceContentResolutionError(
                "source artifact hash differs from authenticated provenance"
            )

    @staticmethod
    def _extract_record(
        artifact_bytes: bytes,
        provenance: SourceRecordProvenance,
    ) -> bytes:
        record_end = provenance.byte_offset + provenance.byte_length
        if record_end > len(artifact_bytes):
            raise SourceContentResolutionError(
                "source record byte range exceeds the current artifact"
            )
        return artifact_bytes[provenance.byte_offset : record_end]

    @staticmethod
    def _verify_record(
        record_bytes: bytes,
        provenance: SourceRecordProvenance,
    ) -> None:
        observed_digest = hashlib.sha256(record_bytes).hexdigest()
        if not hmac.compare_digest(observed_digest, provenance.content_sha256):
            raise SourceContentResolutionError(
                "source record hash differs from authenticated provenance"
            )

    @staticmethod
    def _decode_text(
        record_bytes: bytes,
        modality: EvidenceModality,
    ) -> str | None:
        if modality is EvidenceModality.VISUAL:
            return None
        try:
            return record_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise SourceContentResolutionError(
                "transcript and OCR records must contain strict UTF-8"
            ) from error
