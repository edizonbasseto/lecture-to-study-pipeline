"""Immutable evidence contracts for publication-quality gate issuers."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.versioning import SchemaVersionOne
from lecture_study.quality.certification import QualityGateName

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_PositiveFiniteFloat = Annotated[
    float,
    Field(gt=0, allow_inf_nan=False),
]
_NonNegativeFiniteFloat = Annotated[
    float,
    Field(ge=0, allow_inf_nan=False),
]
_MAX_PERSISTED_INTEGER = (1 << 63) - 1
_NonNegativePersistedInteger = Annotated[
    int,
    Field(ge=0, le=_MAX_PERSISTED_INTEGER),
]
_PositivePersistedInteger = Annotated[
    int,
    Field(gt=0, le=_MAX_PERSISTED_INTEGER),
]
_CONCRETE_GATE_NAMES = frozenset(
    {
        QualityGateName.SOURCE_MEDIA_INTEGRITY,
        QualityGateName.PROMPT_BUDGET,
        QualityGateName.ARTIFACT_LINEAGE,
        QualityGateName.TIMELINE_CLASSIFICATION,
        QualityGateName.LEARNING_UNIT_COMPLETENESS,
    }
)


class EvidenceArtifact(BaseModel):
    """Content-addressed artifact participating in a gate evaluation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    artifact_id: SafeIdentifier
    relative_path: _NonBlankText
    sha256: _Digest
    byte_size: _NonNegativePersistedInteger
    media_type: _NonBlankText

    @field_validator("relative_path")
    @classmethod
    def _require_normalized_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("relative_path must use POSIX separators")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or not path.parts
            or value != path.as_posix()
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise ValueError(
                "relative_path must be normalized and remain below its artifact root"
            )
        return value


class FindingSeverity(StrEnum):
    """Closed severity vocabulary used by all gate evaluators."""

    BLOCKING = "blocking"
    WARNING = "warning"
    INFORMATIONAL = "informational"


class QualityFindingEvidence(BaseModel):
    """Typed evaluator finding linked to the artifacts that support it."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    finding_id: SafeIdentifier
    severity: FindingSeverity
    code: SafeIdentifier
    message: _NonBlankText
    artifact_ids: tuple[SafeIdentifier, ...] = ()

    @model_validator(mode="after")
    def _require_unique_artifact_references(self) -> QualityFindingEvidence:
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("finding contains duplicate artifact references")
        return self


class SourceMediaIntegrityEvidence(BaseModel):
    """Observed media properties compared with the declared source snapshot."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    evidence_type: Literal["source-media-integrity"]
    source_artifact_id: SafeIdentifier
    expected_sha256: _Digest
    observed_sha256: _Digest
    expected_byte_count: _PositivePersistedInteger
    observed_byte_count: _PositivePersistedInteger
    expected_duration_seconds: _PositiveFiniteFloat
    observed_duration_seconds: _PositiveFiniteFloat
    duration_tolerance_seconds: _NonNegativeFiniteFloat
    decoded_sample_count: _PositivePersistedInteger
    failed_sample_count: _NonNegativePersistedInteger


class PromptBudgetEvidence(BaseModel):
    """Exact token accounting for one prompt and its model context window."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    evidence_type: Literal["prompt-budget"]
    prompt_artifact_id: SafeIdentifier
    input_tokens: _NonNegativePersistedInteger
    output_reserve_tokens: _PositivePersistedInteger
    context_window_tokens: _PositivePersistedInteger
    safety_margin_tokens: _NonNegativePersistedInteger

    @property
    def required_tokens(self) -> int:
        return (
            self.input_tokens + self.output_reserve_tokens + self.safety_margin_tokens
        )


class TimelineClassificationEvidence(BaseModel):
    """Exact timeline partition and source-span accounting metrics."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    evidence_type: Literal["timeline-classification"]
    timeline_artifact_id: SafeIdentifier
    expected_duration_seconds: _PositiveFiniteFloat
    classified_duration_seconds: _PositiveFiniteFloat
    duration_tolerance_seconds: _NonNegativeFiniteFloat
    interval_count: _PositivePersistedInteger
    unresolved_interval_count: _NonNegativePersistedInteger
    unresolved_source_span_count: _NonNegativePersistedInteger
    classification_mismatch_count: _NonNegativePersistedInteger
    gap_duration_seconds: _NonNegativeFiniteFloat
    overlap_duration_seconds: _NonNegativeFiniteFloat
    outside_media_duration_seconds: _NonNegativeFiniteFloat
    unreferenced_source_span_count: _NonNegativePersistedInteger
    multiply_referenced_source_span_count: _NonNegativePersistedInteger
    source_span_boundary_violation_count: _NonNegativePersistedInteger


class LearningUnitCompletenessEvidence(BaseModel):
    """Exact conceptual-unit and instructional-span ownership metrics."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    evidence_type: Literal["learning-unit-completeness"]
    source_universe_artifact_id: SafeIdentifier
    expected_unit_count: _PositivePersistedInteger
    observed_unit_count: _PositivePersistedInteger
    instructional_source_span_count: _PositivePersistedInteger
    owned_instructional_source_span_count: _NonNegativePersistedInteger
    missing_primary_ownership_count: _NonNegativePersistedInteger
    duplicate_primary_ownership_count: _NonNegativePersistedInteger
    invalid_primary_reference_count: _NonNegativePersistedInteger
    out_of_window_primary_span_count: _NonNegativePersistedInteger
    out_of_window_context_span_count: _NonNegativePersistedInteger
    unit_timeline_gap_seconds: _NonNegativeFiniteFloat
    unit_timeline_overlap_seconds: _NonNegativeFiniteFloat
    unit_outside_media_duration_seconds: _NonNegativeFiniteFloat
    unjustified_duration_unit_count: _NonNegativePersistedInteger


class ArtifactLineageEvidence(BaseModel):
    """Declared and observed artifact lineage for a completed stage."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    evidence_type: Literal["artifact-lineage"]
    declared_input_artifact_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    consumed_input_artifact_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    output_artifacts: tuple[EvidenceArtifact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_unique_lineage_members(self) -> ArtifactLineageEvidence:
        _require_unique_values(
            self.declared_input_artifact_ids,
            "duplicate declared lineage input",
        )
        _require_unique_values(
            self.consumed_input_artifact_ids,
            "duplicate consumed lineage input",
        )
        _require_unique_artifacts(
            self.output_artifacts,
            "duplicate lineage output artifact",
        )
        return self


class UnavailableGateEvidence(BaseModel):
    """Explicit fail-closed evidence for a gate without an evaluator."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    evidence_type: Literal["unavailable"]
    gate_name: QualityGateName
    reason_code: SafeIdentifier
    detail: _NonBlankText

    @model_validator(mode="after")
    def _reject_concrete_gate_placeholders(self) -> UnavailableGateEvidence:
        if self.gate_name in _CONCRETE_GATE_NAMES:
            raise ValueError(
                "unavailable evidence is reserved for gates without concrete payloads"
            )
        return self


GateEvidencePayload = Annotated[
    SourceMediaIntegrityEvidence
    | PromptBudgetEvidence
    | TimelineClassificationEvidence
    | LearningUnitCompletenessEvidence
    | ArtifactLineageEvidence
    | UnavailableGateEvidence,
    Field(discriminator="evidence_type"),
]


class GateEvidenceEnvelope(BaseModel):
    """Unauthenticated evidence whose self-declared metrics can be checked."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    lecture_id: SafeIdentifier
    gate_name: QualityGateName
    source_snapshot_sha256: _Digest
    policy_artifact: EvidenceArtifact
    calibration_artifact: EvidenceArtifact | None = None
    evaluator_artifact: EvidenceArtifact
    input_artifacts: tuple[EvidenceArtifact, ...] = Field(min_length=1)
    payload: GateEvidencePayload
    findings: tuple[QualityFindingEvidence, ...] = ()

    @model_validator(mode="after")
    def _reject_duplicate_evidence(self) -> GateEvidenceEnvelope:
        _require_unique_artifacts(
            self.input_artifacts,
            "duplicate input artifact",
        )
        all_artifacts = self._bound_artifacts()
        _require_unique_artifacts(all_artifacts, "duplicate bound artifact")
        _require_unique_values(
            tuple(finding.finding_id for finding in self.findings),
            "duplicate finding",
        )
        return self

    @property
    def declared_metrics_are_internally_consistent(self) -> bool:
        """Check only self-declared metrics; this is not publication authority."""

        try:
            validated = GateEvidenceEnvelope.model_validate(
                self.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError):
            return False
        return validated._declared_metrics_are_internally_consistent()

    def _declared_metrics_are_internally_consistent(self) -> bool:
        if self.calibration_artifact is None:
            return False
        if isinstance(self.payload, UnavailableGateEvidence):
            return False
        if not self._gate_matches_payload():
            return False
        if self._contains_blocking_finding():
            return False
        if not self._has_safe_unique_artifacts():
            return False
        if not self._has_unique_findings():
            return False
        if not self._findings_reference_bound_artifacts():
            return False
        return self._payload_metrics_pass()

    def _bound_artifacts(self) -> tuple[EvidenceArtifact, ...]:
        artifacts = [self.policy_artifact, self.evaluator_artifact]
        if self.calibration_artifact is not None:
            artifacts.append(self.calibration_artifact)
        artifacts.extend(self.input_artifacts)
        if isinstance(self.payload, ArtifactLineageEvidence):
            artifacts.extend(self.payload.output_artifacts)
        return tuple(artifacts)

    def _gate_matches_payload(self) -> bool:
        expected_gate = {
            SourceMediaIntegrityEvidence: QualityGateName.SOURCE_MEDIA_INTEGRITY,
            PromptBudgetEvidence: QualityGateName.PROMPT_BUDGET,
            TimelineClassificationEvidence: QualityGateName.TIMELINE_CLASSIFICATION,
            LearningUnitCompletenessEvidence: (
                QualityGateName.LEARNING_UNIT_COMPLETENESS
            ),
            ArtifactLineageEvidence: QualityGateName.ARTIFACT_LINEAGE,
        }.get(type(self.payload))
        if isinstance(self.payload, UnavailableGateEvidence):
            return self.gate_name is self.payload.gate_name
        return expected_gate is self.gate_name

    def _contains_blocking_finding(self) -> bool:
        return any(
            finding.severity is FindingSeverity.BLOCKING for finding in self.findings
        )

    def _has_unique_findings(self) -> bool:
        finding_ids = tuple(finding.finding_id for finding in self.findings)
        return len(finding_ids) == len(set(finding_ids))

    def _has_safe_unique_artifacts(self) -> bool:
        try:
            artifacts = self._bound_artifacts()
            _require_unique_artifacts(artifacts, "duplicate bound artifact")
        except (AttributeError, TypeError, ValueError):
            return False
        return all(
            _is_safe_relative_path(artifact.relative_path)
            and _is_non_negative_integer(artifact.byte_size)
            for artifact in artifacts
        )

    def _findings_reference_bound_artifacts(self) -> bool:
        bound_ids = {artifact.artifact_id for artifact in self._bound_artifacts()}
        return all(
            set(finding.artifact_ids).issubset(bound_ids)
            and len(finding.artifact_ids) == len(set(finding.artifact_ids))
            for finding in self.findings
        )

    def _payload_metrics_pass(self) -> bool:
        if isinstance(self.payload, SourceMediaIntegrityEvidence):
            return self._source_media_metrics_pass(self.payload)
        if isinstance(self.payload, PromptBudgetEvidence):
            return self._prompt_budget_metrics_pass(self.payload)
        if isinstance(self.payload, TimelineClassificationEvidence):
            return self._timeline_classification_metrics_pass(self.payload)
        if isinstance(self.payload, LearningUnitCompletenessEvidence):
            return self._learning_unit_completeness_metrics_pass(self.payload)
        if isinstance(self.payload, ArtifactLineageEvidence):
            return self._artifact_lineage_metrics_pass(self.payload)
        return False

    def _source_media_metrics_pass(
        self,
        payload: SourceMediaIntegrityEvidence,
    ) -> bool:
        artifacts = {
            artifact.artifact_id: artifact for artifact in self.input_artifacts
        }
        source_artifact = artifacts.get(payload.source_artifact_id)
        if source_artifact is None:
            return False
        return (
            _is_positive_integer(payload.expected_byte_count)
            and _is_positive_integer(payload.observed_byte_count)
            and _is_positive_integer(payload.decoded_sample_count)
            and _is_non_negative_integer(payload.failed_sample_count)
            and _is_positive_finite_number(payload.expected_duration_seconds)
            and _is_positive_finite_number(payload.observed_duration_seconds)
            and _is_non_negative_finite_number(payload.duration_tolerance_seconds)
            and payload.expected_sha256 == self.source_snapshot_sha256
            and payload.observed_sha256 == payload.expected_sha256
            and source_artifact.sha256 == payload.observed_sha256
            and source_artifact.byte_size == payload.observed_byte_count
            and payload.observed_byte_count == payload.expected_byte_count
            and abs(
                payload.observed_duration_seconds - payload.expected_duration_seconds
            )
            <= payload.duration_tolerance_seconds
            and payload.failed_sample_count == 0
        )

    def _prompt_budget_metrics_pass(self, payload: PromptBudgetEvidence) -> bool:
        input_ids = {artifact.artifact_id for artifact in self.input_artifacts}
        return (
            _is_non_negative_integer(payload.input_tokens)
            and _is_positive_integer(payload.output_reserve_tokens)
            and _is_positive_integer(payload.context_window_tokens)
            and _is_non_negative_integer(payload.safety_margin_tokens)
            and payload.prompt_artifact_id in input_ids
            and payload.required_tokens <= payload.context_window_tokens
        )

    def _timeline_classification_metrics_pass(
        self,
        payload: TimelineClassificationEvidence,
    ) -> bool:
        input_ids = {artifact.artifact_id for artifact in self.input_artifacts}
        calculated_classified_duration = (
            payload.expected_duration_seconds
            - payload.gap_duration_seconds
            + payload.overlap_duration_seconds
            + payload.outside_media_duration_seconds
        )
        return (
            payload.timeline_artifact_id in input_ids
            and _is_positive_finite_number(payload.expected_duration_seconds)
            and _is_positive_finite_number(payload.classified_duration_seconds)
            and _is_non_negative_finite_number(payload.duration_tolerance_seconds)
            and _is_positive_integer(payload.interval_count)
            and _is_non_negative_integer(payload.unresolved_interval_count)
            and _is_non_negative_integer(payload.unresolved_source_span_count)
            and _is_non_negative_integer(payload.classification_mismatch_count)
            and _is_non_negative_finite_number(payload.gap_duration_seconds)
            and _is_non_negative_finite_number(payload.overlap_duration_seconds)
            and _is_non_negative_finite_number(payload.outside_media_duration_seconds)
            and self._timeline_partition_metrics_are_feasible(payload)
            and _is_non_negative_integer(payload.unreferenced_source_span_count)
            and _is_non_negative_integer(payload.multiply_referenced_source_span_count)
            and _is_non_negative_integer(payload.source_span_boundary_violation_count)
            and abs(
                calculated_classified_duration - payload.classified_duration_seconds
            )
            <= payload.duration_tolerance_seconds
            and payload.unresolved_interval_count == 0
            and payload.unresolved_source_span_count == 0
            and payload.classification_mismatch_count == 0
            and payload.gap_duration_seconds <= payload.duration_tolerance_seconds
            and payload.overlap_duration_seconds <= payload.duration_tolerance_seconds
            and payload.outside_media_duration_seconds == 0.0
            and payload.unreferenced_source_span_count == 0
            and payload.multiply_referenced_source_span_count == 0
            and payload.source_span_boundary_violation_count == 0
        )

    @staticmethod
    def _timeline_partition_metrics_are_feasible(
        payload: TimelineClassificationEvidence,
    ) -> bool:
        unique_coverage_duration = (
            payload.expected_duration_seconds - payload.gap_duration_seconds
        )
        if unique_coverage_duration <= 0.0:
            return False
        overlap_to_coverage_ratio = (
            payload.overlap_duration_seconds / unique_coverage_duration
        )
        return overlap_to_coverage_ratio <= payload.interval_count - 1

    def _learning_unit_completeness_metrics_pass(
        self,
        payload: LearningUnitCompletenessEvidence,
    ) -> bool:
        input_ids = {artifact.artifact_id for artifact in self.input_artifacts}
        return (
            payload.source_universe_artifact_id in input_ids
            and _is_positive_integer(payload.expected_unit_count)
            and _is_positive_integer(payload.observed_unit_count)
            and _is_positive_integer(payload.instructional_source_span_count)
            and _is_non_negative_integer(payload.owned_instructional_source_span_count)
            and _is_non_negative_integer(payload.missing_primary_ownership_count)
            and _is_non_negative_integer(payload.duplicate_primary_ownership_count)
            and _is_non_negative_integer(payload.invalid_primary_reference_count)
            and _is_non_negative_integer(payload.out_of_window_primary_span_count)
            and _is_non_negative_integer(payload.out_of_window_context_span_count)
            and _is_non_negative_finite_number(payload.unit_timeline_gap_seconds)
            and _is_non_negative_finite_number(payload.unit_timeline_overlap_seconds)
            and _is_non_negative_finite_number(
                payload.unit_outside_media_duration_seconds
            )
            and _is_non_negative_integer(payload.unjustified_duration_unit_count)
            and payload.expected_unit_count == payload.observed_unit_count
            and payload.instructional_source_span_count
            == payload.owned_instructional_source_span_count
            and payload.missing_primary_ownership_count == 0
            and payload.duplicate_primary_ownership_count == 0
            and payload.invalid_primary_reference_count == 0
            and payload.out_of_window_primary_span_count == 0
            and payload.out_of_window_context_span_count == 0
            and payload.unit_timeline_gap_seconds == 0.0
            and payload.unit_timeline_overlap_seconds == 0.0
            and payload.unit_outside_media_duration_seconds == 0.0
            and payload.unjustified_duration_unit_count == 0
        )

    def _artifact_lineage_metrics_pass(
        self,
        payload: ArtifactLineageEvidence,
    ) -> bool:
        declared_ids = set(payload.declared_input_artifact_ids)
        consumed_ids = set(payload.consumed_input_artifact_ids)
        bound_input_ids = {artifact.artifact_id for artifact in self.input_artifacts}
        return (
            len(payload.declared_input_artifact_ids) == len(declared_ids)
            and len(payload.consumed_input_artifact_ids) == len(consumed_ids)
            and declared_ids == consumed_ids == bound_input_ids
        )


def canonical_evidence_bytes(envelope: GateEvidenceEnvelope) -> bytes:
    """Serialize an envelope to deterministic UTF-8 JSON bytes."""

    return json.dumps(
        envelope.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_evidence_sha256(envelope: GateEvidenceEnvelope) -> str:
    """Hash the deterministic representation of an evidence envelope."""

    return hashlib.sha256(canonical_evidence_bytes(envelope)).hexdigest()


def _require_unique_artifacts(
    artifacts: tuple[EvidenceArtifact, ...],
    message: str,
) -> None:
    artifact_ids = tuple(artifact.artifact_id for artifact in artifacts)
    artifact_paths = tuple(artifact.relative_path for artifact in artifacts)
    _require_unique_values(artifact_ids, message)
    _require_unique_values(artifact_paths, message)


def _require_unique_values(values: tuple[str, ...], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _is_safe_relative_path(value: str) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and bool(path.parts)
        and value == path.as_posix()
        and all(part not in {".", ".."} for part in path.parts)
    )


def _is_positive_integer(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_integer(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_finite_number(value: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _is_non_negative_finite_number(value: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )
