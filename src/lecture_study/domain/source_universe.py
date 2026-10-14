"""Immutable contracts for the authoritative multimodal lecture source universe."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.versioning import (
    PersistedSequenceNumber,
    SchemaVersionOne,
)

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_NonNegativeFiniteFloat = Annotated[
    float,
    Field(ge=0, allow_inf_nan=False),
]
_PositiveFiniteFloat = Annotated[
    float,
    Field(gt=0, allow_inf_nan=False),
]


class EvidenceModality(StrEnum):
    """Closed registry of source modalities preserved independently."""

    TRANSCRIPT = "transcript"
    OCR = "ocr"
    VISUAL = "visual"


class SourceSpanClassification(StrEnum):
    """Disposition assigned to every source evidence span."""

    INSTRUCTIONAL = "instructional"
    NON_INSTRUCTIONAL = "non-instructional"
    DUPLICATE = "duplicate"
    AMBIGUOUS = "ambiguous"
    INAUDIBLE = "inaudible"


class TimelineClassification(StrEnum):
    """Disposition assigned to every interval in the complete media timeline."""

    INSTRUCTIONAL = "instructional"
    NON_INSTRUCTIONAL = "non-instructional"
    AMBIGUOUS = "ambiguous"
    INAUDIBLE = "inaudible"


class SourceEvidenceSpan(BaseModel):
    """Content-addressed transcript, OCR, or visual evidence on the media timeline."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    span_id: SafeIdentifier
    lecture_id: SafeIdentifier
    source_artifact_id: SafeIdentifier
    source_record_id: SafeIdentifier
    modality: EvidenceModality
    start_time: _NonNegativeFiniteFloat
    end_time: _NonNegativeFiniteFloat
    classification: SourceSpanClassification
    content_sha256: _Digest
    duplicate_of_span_id: SafeIdentifier | None = None

    @model_validator(mode="after")
    def _validate_span_contract(self) -> SourceEvidenceSpan:
        if self.end_time < self.start_time:
            raise ValueError("end_time must not be before start_time")
        if (
            self.modality is EvidenceModality.TRANSCRIPT
            and self.end_time == self.start_time
        ):
            raise ValueError("transcript evidence must have positive duration")
        is_duplicate = self.classification is SourceSpanClassification.DUPLICATE
        if is_duplicate != (self.duplicate_of_span_id is not None):
            raise ValueError(
                "duplicate_of_span_id is required exactly when classification is "
                "duplicate"
            )
        if self.duplicate_of_span_id == self.span_id:
            raise ValueError("a source span cannot duplicate itself")
        return self


class TimelineInterval(BaseModel):
    """One classified interval in an exact partition of the source media."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    interval_id: SafeIdentifier
    lecture_id: SafeIdentifier
    sequence: PersistedSequenceNumber
    start_time: _NonNegativeFiniteFloat
    end_time: _PositiveFiniteFloat
    classification: TimelineClassification
    evidence_span_ids: tuple[SafeIdentifier, ...] = ()

    @model_validator(mode="after")
    def _validate_interval_contract(self) -> TimelineInterval:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        if len(self.evidence_span_ids) != len(set(self.evidence_span_ids)):
            raise ValueError("evidence_span_ids must not contain duplicates")
        return self


class ConceptualLearningUnit(BaseModel):
    """Conceptual unit with exact primary ownership and optional boundary context."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    unit_id: SafeIdentifier
    lecture_id: SafeIdentifier
    sequence: PersistedSequenceNumber
    start_time: _NonNegativeFiniteFloat
    end_time: _PositiveFiniteFloat
    primary_source_span_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    context_source_span_ids: tuple[SafeIdentifier, ...] = ()
    duration_exception_reason: _NonBlankText | None = None

    @model_validator(mode="after")
    def _validate_unit_contract(self) -> ConceptualLearningUnit:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        if len(self.primary_source_span_ids) != len(set(self.primary_source_span_ids)):
            raise ValueError("primary_source_span_ids must not contain duplicates")
        if len(self.context_source_span_ids) != len(set(self.context_source_span_ids)):
            raise ValueError("context_source_span_ids must not contain duplicates")
        if set(self.primary_source_span_ids) & set(self.context_source_span_ids):
            raise ValueError(
                "a source span cannot be both primary and context in the same unit"
            )
        return self


class SourceUniverse(BaseModel):
    """Authoritative source partition consumed by semantic content extraction."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    lecture_id: SafeIdentifier
    media_duration_seconds: _PositiveFiniteFloat
    timeline_intervals: tuple[TimelineInterval, ...] = Field(min_length=1)
    evidence_spans: tuple[SourceEvidenceSpan, ...] = Field(min_length=1)
    learning_units: tuple[ConceptualLearningUnit, ...] = Field(min_length=1)
