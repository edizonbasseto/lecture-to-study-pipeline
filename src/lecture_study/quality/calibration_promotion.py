"""Authenticated semantic calibration and short-material promotion contracts."""

from __future__ import annotations

import hashlib
import hmac
import json
import weakref
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, localcontext
from enum import StrEnum
from collections.abc import Callable
from typing import (
    Annotated,
    Literal,
    NoReturn,
    Protocol,
    Self,
    cast,
    runtime_checkable,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from lecture_study.domain.calibration import ReferenceCorpusManifest
from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.versioning import (
    PersistedSequenceNumber,
    SchemaVersionOne,
)
from lecture_study.quality.calibration import (
    canonical_reference_corpus_case_sha256,
    canonical_reference_corpus_manifest_sha256,
)
from lecture_study.quality.expert_corpus import (
    VerifiedVeterinaryExpertCorpus,
    VeterinaryCorpusPromotionHmacAuthority,
    canonical_expert_corpus_evidence_sha256,
)
from lecture_study.quality.trust import canonical_json_bytes

SEMANTIC_CALIBRATION_CRYPTOGRAPHIC_DOMAIN = "lecture-study.semantic-calibration.v1"
SEMANTIC_CALIBRATION_POLICY_SIGNATURE_PURPOSE = "semantic-calibration-policy"
SHORT_MATERIAL_THRESHOLD_SIGNATURE_PURPOSE = "short-material-threshold-policy"

_MINIMUM_KEY_BYTES = 32
_KEY_ID_CONTEXT = b"lecture-study:semantic-calibration:key-id:v1\0"
_IDENTITY_CONTEXT = b"lecture-study:semantic-calibration:identity:v1\0"
_SPLIT_CONTEXT = b"lecture-study:semantic-calibration:split:v1\0"
_SOURCE_SCALE_FORMULA_SHA256 = hashlib.sha256(
    b"lecture-study:short-material:calibration-reference-feature-ratios:maximum:v2"
).hexdigest()
_SAFE_IDENTIFIER_ADAPTER = TypeAdapter(SafeIdentifier)
_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_Signature = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_PositiveInteger = Annotated[int, Field(ge=1)]
_NonNegativeInteger = Annotated[int, Field(ge=0)]
_UnitInterval = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"))]
_NonNegativeDecimal = Annotated[Decimal, Field(ge=Decimal("0"))]


class CalibrationDimension(StrEnum):
    """Closed independently blocking semantic dimensions."""

    SOURCE_CLAUSE_COVERAGE = "source-clause-coverage"
    SAFETY_CRITICAL_CLAUSE_COVERAGE = "safety-critical-clause-coverage"
    NEGATION = "negation"
    UNCERTAINTY_AND_CERTAINTY = "uncertainty-and-certainty"
    COMPARISON_DIRECTION = "comparison-direction"
    CAUSAL_DIRECTION = "causal-direction"
    CONDITIONS = "conditions"
    EXCEPTIONS = "exceptions"
    CONTRAINDICATIONS = "contraindications"
    RED_FLAGS = "red-flags"
    NUMERIC_VALUES = "numeric-values"
    UNITS = "units"
    DOSES = "doses"
    ROUTES = "routes"
    INTERVALS = "intervals"
    FREQUENCIES = "frequencies"
    DURATIONS = "durations"
    DENOMINATORS = "denominators"
    UNSUPPORTED_CLINICAL_CLAIMS = "unsupported-clinical-claims"


class CalibrationPartition(StrEnum):
    """Leakage-isolated corpus partitions."""

    TRAIN = "train"
    DEVELOPMENT = "development"
    CALIBRATION = "calibration"
    FINAL_HELD_OUT = "final-held-out"


class ThresholdComparator(StrEnum):
    """Deterministic caller-selected metric comparison."""

    GREATER_THAN_OR_EQUAL = "greater-than-or-equal"
    LESS_THAN_OR_EQUAL = "less-than-or-equal"


class CalibrationMetricFormula(StrEnum):
    """Authenticated deterministic formula supported by case outcomes."""

    CASE_DIMENSION_PASS_RATE = "case-dimension-pass-rate-v1"


class CalibrationIntervalKind(StrEnum):
    """Authenticated uncertainty interval semantics."""

    CONFIDENCE = "confidence"
    CREDIBLE = "credible"


class CalibrationArtifactKind(StrEnum):
    """State-registry namespaces that cannot authorize one another."""

    POLICY = "semantic-calibration-policy"
    EVIDENCE = "semantic-calibration-evidence"
    SHORT_MATERIAL_POLICY = "short-material-threshold-policy"


class CalibrationDisposition(StrEnum):
    """Fail-closed local state without publication authority."""

    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    QUARANTINE = "quarantine"


class CalibrationFailureKind(StrEnum):
    """Closed calibration and short-material failure vocabulary."""

    MISSING = "missing"
    UNVERIFIABLE = "unverifiable"
    INVALID = "invalid"
    SUBSTITUTED = "substituted"
    FALSIFIED = "falsified"
    REVOKED = "revoked"
    CONFLICTING = "conflicting"
    UNAVAILABLE = "unavailable"


class CalibrationPromotionError(RuntimeError):
    """Fail-closed calibration failure with local scope disposition."""

    def __init__(
        self,
        message: str,
        *,
        kind: CalibrationFailureKind,
        reason_code: str,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.reason_code = reason_code
        self.disposition = (
            CalibrationDisposition.NEEDS_REVIEW
            if kind
            in {
                CalibrationFailureKind.MISSING,
                CalibrationFailureKind.UNVERIFIABLE,
                CalibrationFailureKind.UNAVAILABLE,
            }
            else CalibrationDisposition.QUARANTINE
        )

    @property
    def independent_work_may_continue(self) -> bool:
        """A local calibration failure never aborts unrelated DAG work."""

        return True

    @property
    def publication_ready(self) -> bool:
        return False


class CalibrationSampleRequirement(BaseModel):
    """Authenticated minimum sample for one exact task and specialty."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    task_identity_sha256: _Digest
    specialty_id: SafeIdentifier
    minimum_case_count: _PositiveInteger


class CalibrationStatisticalPolicy(BaseModel):
    """Versioned statistical governance without inferred acceptance thresholds."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    sample_size_method_sha256: _Digest
    minimum_cases_per_task_and_specialty: tuple[CalibrationSampleRequirement, ...] = (
        Field(min_length=1)
    )
    uncertainty_method_sha256: _Digest
    interval_kind: CalibrationIntervalKind
    interval_level: Annotated[
        Decimal,
        Field(gt=Decimal("0"), lt=Decimal("1")),
    ]
    exclusion_policy_sha256: _Digest
    stopping_rule_sha256: _Digest
    failure_policy_sha256: _Digest

    @model_validator(mode="after")
    def _require_canonical_requirements(self) -> Self:
        identities = tuple(
            (
                requirement.task_identity_sha256,
                str(requirement.specialty_id),
            )
            for requirement in self.minimum_cases_per_task_and_specialty
        )
        if len(identities) != len(set(identities)):
            raise ValueError("calibration sample requirements must be unique")
        if identities != tuple(sorted(identities)):
            raise ValueError("calibration sample requirements must use canonical order")
        return self


class CalibrationCaseLineage(BaseModel):
    """Complete lineage used to reject partition leakage."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    case_id: SafeIdentifier
    partition: CalibrationPartition
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    source_clause_id: SafeIdentifier
    reference_case_sha256: _Digest
    source_manifest_sha256: _Digest
    source_artifact_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    source_record_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    source_content_aggregation_sha256: _Digest
    source_universe_sha256: _Digest
    clause_inventory_sha256: _Digest
    candidate_batches_sha256: _Digest
    atomic_ledger_sha256: _Digest
    reconciliations_sha256: _Digest
    near_duplicate_cluster_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    derivative_root_sha256: _Digest
    specialty_id: SafeIdentifier
    task_taxonomy_sha256: _Digest
    split_policy_sha256: _Digest
    deduplication_policy_sha256: _Digest

    @model_validator(mode="after")
    def _require_canonical_lineage(self) -> Self:
        _require_unique_canonical(
            tuple(str(value) for value in self.source_artifact_ids),
            "calibration source artifact IDs",
        )
        _require_unique_canonical(
            tuple(str(value) for value in self.source_record_ids),
            "calibration source record IDs",
        )
        _require_unique_canonical(
            tuple(str(value) for value in self.near_duplicate_cluster_ids),
            "calibration near-duplicate cluster IDs",
        )
        return self


class CalibrationSplitManifest(BaseModel):
    """Exact lineage-based train, development, calibration, and held-out split."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    split_id: SafeIdentifier
    split_version: SafeIdentifier
    corpus_id: SafeIdentifier
    corpus_version: SafeIdentifier
    corpus_manifest_sha256: _Digest
    cases: tuple[CalibrationCaseLineage, ...] = Field(min_length=4)

    @model_validator(mode="after")
    def _require_complete_leakage_free_split(self) -> Self:
        _require_unique_canonical(
            tuple(str(case.case_id) for case in self.cases),
            "calibration split case IDs",
        )
        partitions = {case.partition for case in self.cases}
        if partitions != set(CalibrationPartition):
            raise ValueError(
                "calibration split must contain train, development, calibration, "
                "and final held-out cases"
            )
        _require_no_cross_partition_overlap(
            self.cases,
            values=lambda case: (str(case.lecture_id),),
            label="lecture lineage",
        )
        _require_no_cross_partition_overlap(
            self.cases,
            values=lambda case: tuple(str(value) for value in case.source_artifact_ids),
            label="source artifact lineage",
        )
        _require_no_cross_partition_overlap(
            self.cases,
            values=lambda case: tuple(str(value) for value in case.source_record_ids),
            label="source record lineage",
        )
        _require_no_cross_partition_overlap(
            self.cases,
            values=lambda case: (case.source_content_aggregation_sha256,),
            label="source aggregation lineage",
        )
        _require_no_cross_partition_overlap(
            self.cases,
            values=lambda case: tuple(
                str(value) for value in case.near_duplicate_cluster_ids
            ),
            label="near-duplicate lineage",
        )
        _require_no_cross_partition_overlap(
            self.cases,
            values=lambda case: (case.derivative_root_sha256,),
            label="derivative lineage",
        )
        _require_one_split_identity(
            self.cases,
            values=lambda case: str(case.specialty_id),
            label="specialty",
        )
        _require_one_split_identity(
            self.cases,
            values=lambda case: case.task_taxonomy_sha256,
            label="task taxonomy",
        )
        _require_one_split_identity(
            self.cases,
            values=lambda case: case.split_policy_sha256,
            label="split policy",
        )
        _require_one_split_identity(
            self.cases,
            values=lambda case: case.deduplication_policy_sha256,
            label="deduplication policy",
        )
        return self


class CalibrationThreshold(BaseModel):
    """One authenticated caller-selected threshold for one blocking dimension."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    dimension: CalibrationDimension
    metric_id: SafeIdentifier
    metric_formula: CalibrationMetricFormula
    comparator: ThresholdComparator
    threshold: _UnitInterval
    minimum_sample_count: _PositiveInteger
    maximum_uncertainty_width: _UnitInterval


class ShortMaterialCalibrationRule(BaseModel):
    """Authenticated non-universal rule for one specialty and material type."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    specialty_id: SafeIdentifier
    material_type: SafeIdentifier
    final_material_resolver_identity_sha256: _Digest
    word_counting_policy_sha256: _Digest
    threshold_formula: Literal["minimum-calibrated-word-count-maximum-feature-ratio-v2"]
    minimum_case_count: _PositiveInteger


class SemanticCalibrationPolicyRequest(BaseModel):
    """Unsigned complete calibration policy with no inferred thresholds."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    policy_id: SafeIdentifier
    policy_version: SafeIdentifier
    corpus_payload_sha256: _Digest
    split_manifest: CalibrationSplitManifest
    producer_identity_sha256s: tuple[_Digest, ...] = Field(min_length=2)
    producer_model_identity_sha256s: tuple[_Digest, ...] = Field(min_length=2)
    evaluator_identity_sha256: _Digest
    evaluator_model_identity_sha256: _Digest
    runtime_profile_sha256: _Digest
    tokenizer_identity_sha256: _Digest
    task_identity_sha256: _Digest
    statistical_policy: CalibrationStatisticalPolicy
    blocking_dimensions: tuple[CalibrationDimension, ...] = Field(min_length=1)
    thresholds: tuple[CalibrationThreshold, ...] = Field(min_length=1)
    short_material_rule: ShortMaterialCalibrationRule
    permitted_exclusion_reason_sha256s: tuple[_Digest, ...] = ()
    authority_sequence: PersistedSequenceNumber
    issued_at_epoch_seconds: PersistedSequenceNumber
    expires_at_epoch_seconds: PersistedSequenceNumber

    @model_validator(mode="after")
    def _require_complete_independent_policy(self) -> Self:
        if self.expires_at_epoch_seconds <= self.issued_at_epoch_seconds:
            raise ValueError("calibration policy must expire after issuance")
        _require_unique_canonical(
            self.producer_identity_sha256s,
            "calibration producer identities",
        )
        _require_unique_canonical(
            self.producer_model_identity_sha256s,
            "calibration producer model identities",
        )
        if self.evaluator_identity_sha256 in self.producer_identity_sha256s:
            raise ValueError("calibration evaluator must be independent of producers")
        if self.evaluator_model_identity_sha256 in self.producer_model_identity_sha256s:
            raise ValueError(
                "calibration evaluator model must be independent of producer models"
            )
        expected_dimensions = tuple(sorted(CalibrationDimension, key=str))
        if self.blocking_dimensions != expected_dimensions:
            raise ValueError(
                "calibration policy must preserve every blocking dimension "
                "in canonical order"
            )
        threshold_dimensions = tuple(item.dimension for item in self.thresholds)
        if threshold_dimensions != expected_dimensions:
            raise ValueError(
                "calibration policy requires one caller threshold for every "
                "blocking dimension in canonical order"
            )
        if any(
            threshold.metric_formula
            is not CalibrationMetricFormula.CASE_DIMENSION_PASS_RATE
            for threshold in self.thresholds
        ):
            raise ValueError("calibration metric formula is unsupported")
        if any(
            case.task_taxonomy_sha256 != self.task_identity_sha256
            for case in self.split_manifest.cases
        ):
            raise ValueError("calibration task taxonomy identity is substituted")
        if any(
            str(case.specialty_id) != str(self.short_material_rule.specialty_id)
            for case in self.split_manifest.cases
        ):
            raise ValueError("calibration specialty identity is substituted")
        expected_requirement = CalibrationSampleRequirement(
            task_identity_sha256=self.task_identity_sha256,
            specialty_id=self.short_material_rule.specialty_id,
            minimum_case_count=self.short_material_rule.minimum_case_count,
        )
        if self.statistical_policy.minimum_cases_per_task_and_specialty != (
            expected_requirement,
        ):
            raise ValueError(
                "calibration statistical policy must bind the exact task, "
                "specialty, and minimum case count"
            )
        if any(
            threshold.minimum_sample_count != expected_requirement.minimum_case_count
            for threshold in self.thresholds
        ):
            raise ValueError(
                "calibration thresholds must use the authenticated sample requirement"
            )
        _require_unique_canonical(
            self.permitted_exclusion_reason_sha256s,
            "calibration permitted exclusion reasons",
        )
        return self


class AuthenticatedSemanticCalibrationPolicy(BaseModel):
    """Signed calibration policy without certification or publication authority."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    request: SemanticCalibrationPolicyRequest
    cryptographic_domain: Literal["lecture-study.semantic-calibration.v1"]
    signature_purpose: Literal["semantic-calibration-policy"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    detached_signature: _Signature

    def authentication_payload(self) -> bytes:
        return _authentication_payload(self)


class CalibrationDimensionMetric(BaseModel):
    """Observed metric, uncertainty, sample, exclusion, and failure counts."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    dimension: CalibrationDimension
    metric_id: SafeIdentifier
    numerator: _NonNegativeInteger
    denominator: _PositiveInteger
    point_estimate: _UnitInterval
    uncertainty_lower: _UnitInterval
    uncertainty_upper: _UnitInterval
    sample_count: _PositiveInteger
    exclusion_count: _NonNegativeInteger
    failure_count: _NonNegativeInteger

    @model_validator(mode="after")
    def _require_consistent_metric(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("calibration metric numerator exceeds denominator")
        expected = Decimal(self.numerator) / Decimal(self.denominator)
        if self.point_estimate != expected:
            raise ValueError("calibration point estimate does not match its counts")
        if self.uncertainty_lower > self.point_estimate:
            raise ValueError("calibration uncertainty lower bound exceeds estimate")
        if self.uncertainty_upper < self.point_estimate:
            raise ValueError("calibration uncertainty upper bound is below estimate")
        if self.failure_count > self.sample_count:
            raise ValueError("calibration failures exceed the sample count")
        return self


class CalibrationCaseOutcome(BaseModel):
    """Exact calibration-case outcome retained even when it fails."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    case_id: SafeIdentifier
    reference_case_sha256: _Digest
    outcome_sha256: _Digest
    passed_dimensions: tuple[CalibrationDimension, ...] = ()
    failed_dimensions: tuple[CalibrationDimension, ...] = ()
    exclusion_reason_sha256: _Digest | None
    short_material_observation: ShortMaterialCalibrationObservation | None = None

    @model_validator(mode="after")
    def _require_complete_outcome(self) -> Self:
        passed = tuple(sorted(self.passed_dimensions, key=str))
        failed = tuple(sorted(self.failed_dimensions, key=str))
        if self.passed_dimensions != passed or self.failed_dimensions != failed:
            raise ValueError("calibration case dimensions must use canonical order")
        if set(passed) & set(failed):
            raise ValueError("calibration case dimension cannot pass and fail")
        if self.exclusion_reason_sha256 is None:
            if set(passed) | set(failed) != set(CalibrationDimension):
                raise ValueError(
                    "non-excluded calibration case must account for every dimension"
                )
        elif passed or failed:
            raise ValueError("excluded calibration case cannot declare outcomes")
        return self


class CalibrationDeterministicGateCounts(BaseModel):
    """Non-negotiable exact safety gates that caller policy cannot weaken."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    total_source_clause_count: _PositiveInteger
    terminal_source_clause_disposition_count: _NonNegativeInteger
    safety_critical_source_clause_count: _NonNegativeInteger
    recalled_safety_critical_clause_count: _NonNegativeInteger
    accepted_semantic_reversal_count: _NonNegativeInteger
    accepted_unsupported_safety_critical_claim_count: _NonNegativeInteger
    accepted_unauthenticated_numeric_mutation_count: _NonNegativeInteger

    @model_validator(mode="after")
    def _require_possible_counts(self) -> Self:
        if (
            self.terminal_source_clause_disposition_count
            > self.total_source_clause_count
        ):
            raise ValueError("terminal clause count exceeds total clauses")
        if (
            self.recalled_safety_critical_clause_count
            > self.safety_critical_source_clause_count
        ):
            raise ValueError("recalled safety-critical count exceeds total")
        return self


class SemanticCalibrationEvidenceRequest(BaseModel):
    """Unsigned exact calibration evidence bound to policy, actors, and cases."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    evidence_id: SafeIdentifier
    run_id: SafeIdentifier
    corpus_payload_sha256: _Digest
    calibration_policy_payload_sha256: _Digest
    split_manifest_sha256: _Digest
    producer_identity_sha256s: tuple[_Digest, ...] = Field(min_length=2)
    producer_model_identity_sha256s: tuple[_Digest, ...] = Field(min_length=2)
    evaluator_identity_sha256: _Digest
    evaluator_model_identity_sha256: _Digest
    runtime_profile_sha256: _Digest
    tokenizer_identity_sha256: _Digest
    task_identity_sha256: _Digest
    short_material_rule: ShortMaterialCalibrationRule
    case_outcomes: tuple[CalibrationCaseOutcome, ...] = Field(min_length=1)
    dimension_metrics: tuple[CalibrationDimensionMetric, ...] = Field(min_length=1)
    deterministic_gates: CalibrationDeterministicGateCounts
    sample_count: _PositiveInteger
    exclusion_count: _NonNegativeInteger
    failure_count: _NonNegativeInteger
    run_started_at_epoch_seconds: PersistedSequenceNumber
    run_completed_at_epoch_seconds: PersistedSequenceNumber
    evaluation_artifact_sha256: _Digest
    authority_sequence: PersistedSequenceNumber
    issued_at_epoch_seconds: PersistedSequenceNumber
    expires_at_epoch_seconds: PersistedSequenceNumber

    @model_validator(mode="after")
    def _require_canonical_complete_evidence(self) -> Self:
        if self.run_completed_at_epoch_seconds < self.run_started_at_epoch_seconds:
            raise ValueError("calibration run completion predates its start")
        if self.expires_at_epoch_seconds <= self.issued_at_epoch_seconds:
            raise ValueError("calibration evidence must expire after issuance")
        _require_unique_canonical(
            self.producer_identity_sha256s,
            "calibration evidence producer identities",
        )
        _require_unique_canonical(
            self.producer_model_identity_sha256s,
            "calibration evidence producer model identities",
        )
        if self.evaluator_identity_sha256 in self.producer_identity_sha256s:
            raise ValueError("calibration evidence evaluator must be independent")
        if self.evaluator_model_identity_sha256 in self.producer_model_identity_sha256s:
            raise ValueError("calibration evidence evaluator model must be independent")
        _require_unique_canonical(
            tuple(str(outcome.case_id) for outcome in self.case_outcomes),
            "calibration outcome case IDs",
        )
        metric_dimensions = tuple(metric.dimension for metric in self.dimension_metrics)
        if metric_dimensions != tuple(sorted(CalibrationDimension, key=str)):
            raise ValueError(
                "calibration evidence requires one metric per dimension "
                "in canonical order"
            )
        return self


class AuthenticatedSemanticCalibrationEvidence(BaseModel):
    """Signed passing calibration evidence without publication authority."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    request: SemanticCalibrationEvidenceRequest
    cryptographic_domain: Literal["lecture-study.semantic-calibration.v1"]
    signature_purpose: Literal["semantic-calibration-policy"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    detached_signature: _Signature

    def authentication_payload(self) -> bytes:
        return _authentication_payload(self)


class FinalStudyMaterialIdentity(BaseModel):
    """Full immutable identity of the exact final material under assessment."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    material_id: SafeIdentifier
    material_version: SafeIdentifier
    final_artifact_sha256: _Digest
    material_byte_length: _PositiveInteger
    material_text_sha256: _Digest
    clause_inventory_sha256: _Digest
    atomic_ledger_sha256: _Digest
    reconciliation_sha256: _Digest
    source_content_aggregation_sha256: _Digest
    runtime_profile_sha256: _Digest
    producer_identity_sha256s: tuple[_Digest, ...] = Field(min_length=2)
    producer_model_identity_sha256s: tuple[_Digest, ...] = Field(min_length=2)
    evaluator_identity_sha256: _Digest
    evaluator_model_identity_sha256: _Digest
    tokenizer_identity_sha256: _Digest
    expert_corpus_payload_sha256: _Digest
    calibration_policy_payload_sha256: _Digest

    @model_validator(mode="after")
    def _require_independent_canonical_actors(self) -> Self:
        _require_unique_canonical(
            self.producer_identity_sha256s,
            "final material producer identities",
        )
        _require_unique_canonical(
            self.producer_model_identity_sha256s,
            "final material producer model identities",
        )
        if self.evaluator_identity_sha256 in self.producer_identity_sha256s:
            raise ValueError("final material evaluator must be independent")
        if self.evaluator_model_identity_sha256 in self.producer_model_identity_sha256s:
            raise ValueError("final material evaluator model must be independent")
        return self


class ShortMaterialSourceProfile(BaseModel):
    """Authenticated source and semantic inventory counts for one material."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    source_duration_milliseconds: _PositiveInteger
    authenticated_transcript_byte_count: _NonNegativeInteger
    authenticated_ocr_byte_count: _NonNegativeInteger
    source_content_aggregation_sha256: _Digest
    source_universe_sha256: _Digest
    instructional_primary_visual_span_count: _NonNegativeInteger
    visual_inventory_sha256: _Digest
    source_clause_count: _PositiveInteger
    clause_inventory_sha256: _Digest
    proposition_count: _PositiveInteger
    numeric_quantity_count: _NonNegativeInteger
    safety_critical_clause_count: _NonNegativeInteger
    atomic_ledger_sha256: _Digest
    reconciliation_set_sha256: _Digest
    specialty_id: SafeIdentifier
    material_type: SafeIdentifier

    @model_validator(mode="after")
    def _require_possible_counts(self) -> Self:
        if (
            self.authenticated_transcript_byte_count + self.authenticated_ocr_byte_count
            == 0
        ):
            raise ValueError("short-material source text byte count must be positive")
        if self.safety_critical_clause_count > self.source_clause_count:
            raise ValueError("safety-critical clauses exceed source clauses")
        return self


class ShortMaterialCalibrationObservation(BaseModel):
    """Content-resolved reference observation used by calibrated density policy."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    material_identity: FinalStudyMaterialIdentity
    source_profile: ShortMaterialSourceProfile
    material_word_count: _PositiveInteger
    terminally_covered_source_clause_count: _NonNegativeInteger
    terminally_covered_proposition_count: _NonNegativeInteger
    terminally_covered_numeric_quantity_count: _NonNegativeInteger
    terminally_covered_safety_critical_clause_count: _NonNegativeInteger
    source_measurement_identity_sha256: _Digest
    final_material_resolver_identity_sha256: _Digest
    word_counting_policy_sha256: _Digest

    @model_validator(mode="after")
    def _require_bound_semantic_identities(self) -> Self:
        _require_material_profile_bindings(
            self.material_identity,
            self.source_profile,
        )
        return self


class CalibratedDensityDistribution(BaseModel):
    """Distribution deterministically derived from promoted calibration outcomes."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    distribution_id: SafeIdentifier
    metric_id: SafeIdentifier
    source_scale_sha256: _Digest
    source_scale_formula_sha256: _Digest
    sample_count: _PositiveInteger
    lower_observed_value: _NonNegativeDecimal
    median_observed_value: _NonNegativeDecimal
    upper_observed_value: _NonNegativeDecimal
    uncertainty_lower: _NonNegativeDecimal
    uncertainty_upper: _NonNegativeDecimal
    distribution_artifact_sha256: _Digest

    @model_validator(mode="after")
    def _require_ordered_distribution(self) -> Self:
        if not (
            self.lower_observed_value
            <= self.median_observed_value
            <= self.upper_observed_value
        ):
            raise ValueError("calibrated density distribution is not ordered")
        if self.uncertainty_lower > self.uncertainty_upper:
            raise ValueError("density uncertainty interval is reversed")
        return self


class ShortMaterialThresholdPolicyRequest(BaseModel):
    """Unsigned calibrated short-material decision for one exact final artifact."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    policy_id: SafeIdentifier
    policy_version: SafeIdentifier
    material_identity: FinalStudyMaterialIdentity
    source_profile: ShortMaterialSourceProfile
    final_material_resolver_identity_sha256: _Digest
    word_counting_policy_sha256: _Digest
    terminally_covered_source_clause_count: _NonNegativeInteger
    terminally_covered_proposition_count: _NonNegativeInteger
    terminally_covered_numeric_quantity_count: _NonNegativeInteger
    terminally_covered_safety_critical_clause_count: _NonNegativeInteger
    source_measurement_identity_sha256: _Digest
    corpus_payload_sha256: _Digest
    calibration_evidence_payload_sha256: _Digest
    calibration_policy_payload_sha256: _Digest
    authority_sequence: PersistedSequenceNumber
    issued_at_epoch_seconds: PersistedSequenceNumber
    expires_at_epoch_seconds: PersistedSequenceNumber

    @model_validator(mode="after")
    def _require_valid_window_and_counts(self) -> Self:
        if self.expires_at_epoch_seconds <= self.issued_at_epoch_seconds:
            raise ValueError("short-material policy must expire after issuance")
        if (
            self.terminally_covered_source_clause_count
            > self.source_profile.source_clause_count
        ):
            raise ValueError("covered clauses exceed source clause inventory")
        coverage_bindings = (
            (
                self.terminally_covered_proposition_count,
                self.source_profile.proposition_count,
                "propositions",
            ),
            (
                self.terminally_covered_numeric_quantity_count,
                self.source_profile.numeric_quantity_count,
                "numeric quantities",
            ),
            (
                self.terminally_covered_safety_critical_clause_count,
                self.source_profile.safety_critical_clause_count,
                "safety-critical clauses",
            ),
        )
        for observed, total, label in coverage_bindings:
            if observed > total:
                raise ValueError(f"covered {label} exceed authenticated inventory")
        _require_material_profile_bindings(
            self.material_identity,
            self.source_profile,
        )
        if (
            self.material_identity.expert_corpus_payload_sha256
            != self.corpus_payload_sha256
            or self.material_identity.calibration_policy_payload_sha256
            != self.calibration_policy_payload_sha256
        ):
            raise ValueError(
                "short-material request identities do not match corpus and policy"
            )
        return self


class AuthenticatedShortMaterialThresholdPolicy(BaseModel):
    """Signed artifact-local short-material policy without publication authority."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    request: ShortMaterialThresholdPolicyRequest
    cryptographic_domain: Literal["lecture-study.semantic-calibration.v1"]
    signature_purpose: Literal["short-material-threshold-policy"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    observed_material_word_count: _PositiveInteger
    density_distribution: CalibratedDensityDistribution
    minimum_acceptable_word_count: _PositiveInteger
    production_eligible: Literal[False]
    detached_signature: _Signature

    def authentication_payload(self) -> bytes:
        return _authentication_payload(self)


class SemanticCalibrationStateRecord(BaseModel):
    """Exact logical identity and validity bound to one persisted artifact."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    artifact_kind: CalibrationArtifactKind
    artifact_id: SafeIdentifier
    artifact_version: SafeIdentifier
    payload_sha256: _Digest
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    authority_sequence: PersistedSequenceNumber
    issued_at_epoch_seconds: PersistedSequenceNumber
    expires_at_epoch_seconds: PersistedSequenceNumber

    @model_validator(mode="after")
    def _require_valid_window(self) -> Self:
        if self.expires_at_epoch_seconds <= self.issued_at_epoch_seconds:
            raise ValueError("calibration state validity window must be positive")
        return self


@runtime_checkable
class SemanticCalibrationCurrentStatePort(Protocol):
    """Inward port for durable current-state and revocation evidence."""

    def record_issued(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        """Atomically record one newly issued artifact."""

    def require_current(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        """Reject unavailable, stale, conflicting, or revoked state."""


@runtime_checkable
class SemanticCalibrationTrustedClockPort(Protocol):
    """Inward trusted clock required for validity checks."""

    def current_epoch_seconds(self) -> int:
        """Return a trusted non-negative epoch second."""


@runtime_checkable
class SemanticCalibrationRevocationPort(Protocol):
    """Inward current revocation authority for exact calibration records."""

    def require_not_revoked(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        """Reject revoked or unverifiable logical identities."""


@runtime_checkable
class FinalMaterialContentResolverPort(Protocol):
    """Inward authenticated port resolving exact content-addressed final bytes."""

    @property
    def resolver_identity_sha256(self) -> str:
        """Return the externally authenticated resolver-policy identity."""

    def require_current_bytes(
        self,
        material_identity: FinalStudyMaterialIdentity,
    ) -> ResolvedFinalMaterialContent:
        """Resolve exact bytes or fail closed when evidence is not current."""


@runtime_checkable
class AuthenticatedMaterialWordCounterPort(Protocol):
    """Inward versioned counter whose identity is authenticated externally."""

    @property
    def policy_payload_sha256(self) -> str:
        """Return the exact current counting-policy identity."""

    def require_current(self) -> None:
        """Reject unavailable, stale, revoked, or substituted policy state."""

    def count_words(self, content: bytes) -> int:
        """Count words deterministically under the current policy."""


@dataclass(frozen=True, slots=True)
class ResolvedFinalMaterialContent:
    """Exact final artifact and canonical UTF-8 text returned by a trusted port."""

    artifact_bytes: bytes
    canonical_text_utf8: bytes

    def __post_init__(self) -> None:
        if not self.artifact_bytes:
            raise ValueError("resolved final artifact bytes must not be empty")
        if not self.canonical_text_utf8:
            raise ValueError("resolved final material text must not be empty")
        self.canonical_text_utf8.decode("utf-8", errors="strict")


class CalibrationReferenceCaseBinding(BaseModel):
    """Exact promoted reference case required by one evaluation scope."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    case_id: SafeIdentifier
    reference_case_sha256: _Digest


class IndependentCalibrationEvaluationScope(BaseModel):
    """Authority-selected immutable scope sent to an independent evaluator."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    calibration_policy_payload_sha256: _Digest
    corpus_payload_sha256: _Digest
    split_manifest_sha256: _Digest
    run_id: SafeIdentifier
    producer_identity_sha256s: tuple[_Digest, ...] = Field(min_length=2)
    producer_model_identity_sha256s: tuple[_Digest, ...] = Field(min_length=2)
    evaluator_identity_sha256: _Digest
    evaluator_model_identity_sha256: _Digest
    runtime_profile_sha256: _Digest
    tokenizer_identity_sha256: _Digest
    task_identity_sha256: _Digest
    reference_cases: tuple[CalibrationReferenceCaseBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_independent_canonical_scope(self) -> Self:
        _require_unique_canonical(
            self.producer_identity_sha256s,
            "independent evaluation producer identities",
        )
        _require_unique_canonical(
            self.producer_model_identity_sha256s,
            "independent evaluation producer model identities",
        )
        if self.evaluator_identity_sha256 in self.producer_identity_sha256s:
            raise ValueError("independent evaluator must differ from every producer")
        if self.evaluator_model_identity_sha256 in self.producer_model_identity_sha256s:
            raise ValueError(
                "independent evaluator model must differ from producer models"
            )
        _require_unique_canonical(
            tuple(str(binding.case_id) for binding in self.reference_cases),
            "independent evaluation reference cases",
        )
        return self


class IndependentCalibrationEvaluationEvidence(BaseModel):
    """Complete current evaluator result from an issuer-bound inward port."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    scope: IndependentCalibrationEvaluationScope
    case_outcomes: tuple[CalibrationCaseOutcome, ...] = Field(min_length=1)
    dimension_metrics: tuple[CalibrationDimensionMetric, ...] = Field(min_length=1)
    deterministic_gates: CalibrationDeterministicGateCounts
    sample_count: _PositiveInteger
    exclusion_count: _NonNegativeInteger
    failure_count: _NonNegativeInteger
    run_started_at_epoch_seconds: PersistedSequenceNumber
    run_completed_at_epoch_seconds: PersistedSequenceNumber
    evaluation_artifact_sha256: _Digest


class AuthenticatedSourceMaterialMeasurement(BaseModel):
    """Current source-derived measurement over exact authenticated artifacts."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    material_identity: FinalStudyMaterialIdentity
    source_profile: ShortMaterialSourceProfile
    terminally_covered_source_clause_count: _NonNegativeInteger
    terminally_covered_proposition_count: _NonNegativeInteger
    terminally_covered_numeric_quantity_count: _NonNegativeInteger
    terminally_covered_safety_critical_clause_count: _NonNegativeInteger
    measurement_artifact_sha256: _Digest

    @model_validator(mode="after")
    def _require_exact_authenticated_inventories(self) -> Self:
        _require_material_profile_bindings(
            self.material_identity,
            self.source_profile,
        )
        coverage = (
            (
                self.terminally_covered_source_clause_count,
                self.source_profile.source_clause_count,
                "source clauses",
            ),
            (
                self.terminally_covered_proposition_count,
                self.source_profile.proposition_count,
                "propositions",
            ),
            (
                self.terminally_covered_numeric_quantity_count,
                self.source_profile.numeric_quantity_count,
                "numeric quantities",
            ),
            (
                self.terminally_covered_safety_critical_clause_count,
                self.source_profile.safety_critical_clause_count,
                "safety-critical clauses",
            ),
        )
        for observed, expected, label in coverage:
            if observed > expected:
                raise ValueError(f"source measurement covered {label} exceed inventory")
        return self


class AuthenticatedCalibrationSplitEvidence(BaseModel):
    """Externally authenticated corpus and source-lineage partition evidence."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    corpus_payload_sha256: _Digest
    split_manifest: CalibrationSplitManifest
    source_lineage_artifact_sha256: _Digest
    production_eligible: Literal[False]


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedIndependentCalibrationEvaluation:
    """Issuer-only capability returned by the configured independent evaluator."""

    evidence: IndependentCalibrationEvaluationEvidence

    def __new__(cls) -> VerifiedIndependentCalibrationEvaluation:
        raise TypeError("independent calibration evaluation capability is issuer-only")


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedSourceMaterialMeasurement:
    """Issuer-only capability returned by authenticated source measurement."""

    evidence: AuthenticatedSourceMaterialMeasurement

    def __new__(cls) -> VerifiedSourceMaterialMeasurement:
        raise TypeError("source-material measurement capability is issuer-only")


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedCalibrationSplitEvidence:
    """Issuer-only capability for exact corpus and source-lineage partitioning."""

    evidence: AuthenticatedCalibrationSplitEvidence

    def __new__(cls) -> VerifiedCalibrationSplitEvidence:
        raise TypeError("calibration split evidence capability is issuer-only")


@runtime_checkable
class IndependentCalibrationEvaluatorPort(Protocol):
    """Inward port that independently evaluates an authority-selected scope."""

    def evaluate_current(
        self,
        scope: IndependentCalibrationEvaluationScope,
        *,
        capability_issuer: IndependentCalibrationEvaluationCapabilityIssuer,
    ) -> VerifiedIndependentCalibrationEvaluation:
        """Evaluate exact cases and issue an authenticated in-process capability."""

    def require_current(
        self,
        capability: VerifiedIndependentCalibrationEvaluation,
    ) -> IndependentCalibrationEvaluationEvidence:
        """Revalidate evaluator provenance and exact current evidence."""


@runtime_checkable
class AuthenticatedSourceMaterialMeasurementPort(Protocol):
    """Inward port deriving measurements from authenticated source artifacts."""

    def resolve_current(
        self,
        material_identity: FinalStudyMaterialIdentity,
        *,
        capability_issuer: SourceMaterialMeasurementCapabilityIssuer,
    ) -> VerifiedSourceMaterialMeasurement:
        """Resolve exact current source, ledger, and reconciliation measurements."""

    def require_current(
        self,
        capability: VerifiedSourceMaterialMeasurement,
    ) -> AuthenticatedSourceMaterialMeasurement:
        """Revalidate issuer provenance and every source-derived identity."""


@runtime_checkable
class AuthenticatedCalibrationSplitEvidencePort(Protocol):
    """Inward port proving partition lineage against promoted source evidence."""

    def resolve_current(
        self,
        *,
        corpus_payload_sha256: str,
        split_manifest: CalibrationSplitManifest,
        capability_issuer: CalibrationSplitEvidenceCapabilityIssuer,
    ) -> VerifiedCalibrationSplitEvidence:
        """Authenticate the exact proposed split against current source lineage."""

    def require_current(
        self,
        capability: VerifiedCalibrationSplitEvidence,
    ) -> AuthenticatedCalibrationSplitEvidence:
        """Revalidate the externally authenticated split capability."""


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class IndependentCalibrationEvaluationCapabilityIssuer:
    """Authority-held issuer delegated only to one injected evaluator port."""

    _port: IndependentCalibrationEvaluatorPort

    def __new__(cls) -> IndependentCalibrationEvaluationCapabilityIssuer:
        raise TypeError("independent evaluation issuer is authority-owned")

    def issue(
        self,
        *,
        port: IndependentCalibrationEvaluatorPort,
        evidence: IndependentCalibrationEvaluationEvidence,
    ) -> VerifiedIndependentCalibrationEvaluation:
        return _issue_bound_capability(
            issuer=self,
            port=port,
            evidence=evidence,
            capability_type=VerifiedIndependentCalibrationEvaluation,
        )


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class SourceMaterialMeasurementCapabilityIssuer:
    """Authority-held issuer delegated only to one authenticated source port."""

    _port: AuthenticatedSourceMaterialMeasurementPort

    def __new__(cls) -> SourceMaterialMeasurementCapabilityIssuer:
        raise TypeError("source-material measurement issuer is authority-owned")

    def issue(
        self,
        *,
        port: AuthenticatedSourceMaterialMeasurementPort,
        evidence: AuthenticatedSourceMaterialMeasurement,
    ) -> VerifiedSourceMaterialMeasurement:
        return _issue_bound_capability(
            issuer=self,
            port=port,
            evidence=evidence,
            capability_type=VerifiedSourceMaterialMeasurement,
        )


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class CalibrationSplitEvidenceCapabilityIssuer:
    """Authority-held issuer delegated only to one split-evidence port."""

    _port: AuthenticatedCalibrationSplitEvidencePort

    def __new__(cls) -> CalibrationSplitEvidenceCapabilityIssuer:
        raise TypeError("calibration split evidence issuer is authority-owned")

    def issue(
        self,
        *,
        port: AuthenticatedCalibrationSplitEvidencePort,
        evidence: AuthenticatedCalibrationSplitEvidence,
    ) -> VerifiedCalibrationSplitEvidence:
        return _issue_bound_capability(
            issuer=self,
            port=port,
            evidence=evidence,
            capability_type=VerifiedCalibrationSplitEvidence,
        )


@dataclass(frozen=True, slots=True)
class _BoundIssuerRegistration:
    issuer_reference: weakref.ReferenceType[object]
    port: object


@dataclass(frozen=True, slots=True)
class _BoundCapabilityRegistration:
    capability_reference: weakref.ReferenceType[object]
    issuer_reference: weakref.ReferenceType[object]
    port: object
    evidence_sha256: str


_BOUND_ISSUER_REGISTRATIONS: dict[int, _BoundIssuerRegistration] = {}
_BOUND_CAPABILITY_REGISTRATIONS: dict[int, _BoundCapabilityRegistration] = {}


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class SemanticCalibrationHmacKey:
    """Opaque key material used only by semantic calibration authority."""

    _material: bytes

    def __post_init__(self) -> None:
        if not isinstance(self._material, bytes):
            raise TypeError("semantic calibration key material must be bytes")
        if len(self._material) < _MINIMUM_KEY_BYTES:
            raise ValueError(
                "semantic calibration key material must contain at least "
                f"{_MINIMUM_KEY_BYTES} bytes"
            )


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedSemanticCalibrationPolicy:
    """Issuer-only capability for one current calibration policy."""

    evidence: AuthenticatedSemanticCalibrationPolicy

    def __new__(cls) -> VerifiedSemanticCalibrationPolicy:
        raise TypeError("VerifiedSemanticCalibrationPolicy is issuer-only")

    @property
    def publication_ready(self) -> bool:
        return False

    @property
    def production_eligible(self) -> bool:
        return False


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedSemanticCalibrationEvidence:
    """Issuer-only capability for one current passing calibration result."""

    evidence: AuthenticatedSemanticCalibrationEvidence

    def __new__(cls) -> VerifiedSemanticCalibrationEvidence:
        raise TypeError("VerifiedSemanticCalibrationEvidence is issuer-only")

    @property
    def publication_ready(self) -> bool:
        return False

    @property
    def production_eligible(self) -> bool:
        return False


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedShortMaterialThresholdPolicy:
    """Issuer-only capability for one exact material-local density decision."""

    evidence: AuthenticatedShortMaterialThresholdPolicy

    def __new__(cls) -> VerifiedShortMaterialThresholdPolicy:
        raise TypeError("VerifiedShortMaterialThresholdPolicy is issuer-only")

    @property
    def publication_ready(self) -> bool:
        return False

    @property
    def production_eligible(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class _CapabilityRegistration:
    capability_reference: weakref.ReferenceType[object]
    authority_reference: weakref.ReferenceType[SemanticCalibrationHmacAuthority]
    artifact_kind: CalibrationArtifactKind
    payload_sha256: str
    detached_signature: str
    dependency_check: Callable[[], None]


_CAPABILITY_REGISTRATIONS: dict[int, _CapabilityRegistration] = {}


class SemanticCalibrationHmacAuthority:
    """Issue and revalidate calibration artifacts under dedicated trust."""

    def __init__(
        self,
        *,
        key: SemanticCalibrationHmacKey,
        authority_id: str,
        current_state: SemanticCalibrationCurrentStatePort,
        independent_evaluator: IndependentCalibrationEvaluatorPort,
        source_measurements: AuthenticatedSourceMaterialMeasurementPort,
        split_evidence: AuthenticatedCalibrationSplitEvidencePort,
    ) -> None:
        if not isinstance(key, SemanticCalibrationHmacKey):
            raise TypeError("key must be a SemanticCalibrationHmacKey")
        if not isinstance(current_state, SemanticCalibrationCurrentStatePort):
            raise TypeError(
                "current_state must implement SemanticCalibrationCurrentStatePort"
            )
        if not isinstance(independent_evaluator, IndependentCalibrationEvaluatorPort):
            raise TypeError(
                "independent_evaluator must implement "
                "IndependentCalibrationEvaluatorPort"
            )
        if not isinstance(
            source_measurements,
            AuthenticatedSourceMaterialMeasurementPort,
        ):
            raise TypeError(
                "source_measurements must implement "
                "AuthenticatedSourceMaterialMeasurementPort"
            )
        if not isinstance(split_evidence, AuthenticatedCalibrationSplitEvidencePort):
            raise TypeError(
                "split_evidence must implement "
                "AuthenticatedCalibrationSplitEvidencePort"
            )
        self._key = key
        self._authority_id = str(
            _SAFE_IDENTIFIER_ADAPTER.validate_python(authority_id, strict=True)
        )
        self._current_state = current_state
        self._independent_evaluator = independent_evaluator
        self._source_measurements = source_measurements
        self._split_evidence = split_evidence
        self._evaluation_capability_issuer = _create_bound_issuer(
            IndependentCalibrationEvaluationCapabilityIssuer,
            independent_evaluator,
        )
        self._source_measurement_capability_issuer = _create_bound_issuer(
            SourceMaterialMeasurementCapabilityIssuer,
            source_measurements,
        )
        self._split_evidence_capability_issuer = _create_bound_issuer(
            CalibrationSplitEvidenceCapabilityIssuer,
            split_evidence,
        )

    @property
    def key_id(self) -> str:
        return hashlib.sha256(_KEY_ID_CONTEXT + self._key._material).hexdigest()

    @property
    def authority_id(self) -> str:
        return self._authority_id

    def issue_policy(
        self,
        request: SemanticCalibrationPolicyRequest,
        *,
        corpus: VerifiedVeterinaryExpertCorpus,
        corpus_authority: VeterinaryCorpusPromotionHmacAuthority,
    ) -> VerifiedSemanticCalibrationPolicy:
        """Authenticate a corpus-bound, leakage-free calibration policy."""

        corpus_evidence = corpus_authority.require_current(corpus)
        corpus_sha256 = canonical_expert_corpus_evidence_sha256(corpus_evidence)
        validated = SemanticCalibrationPolicyRequest.model_validate(
            request.model_dump(mode="python", warnings=False)
        )
        if validated.corpus_payload_sha256 != corpus_sha256:
            _raise_quarantine(
                "calibration policy is bound to a substituted corpus",
                "calibration_corpus_substitution",
            )
        self._require_split_equals_promoted_corpus(
            validated.split_manifest,
            corpus_evidence.request.corpus_manifest,
        )
        split_capability, split_evidence = self._require_split_evidence(
            corpus_sha256,
            validated.split_manifest,
        )
        evidence = self._signed_policy(validated)

        def require_corpus_current() -> None:
            current = corpus_authority.verify(corpus_evidence)
            corpus_authority.require_current(current)
            current_split = _require_bound_capability(
                capability=split_capability,
                port=self._split_evidence,
                evidence_type=AuthenticatedCalibrationSplitEvidence,
            )
            if current_split != split_evidence:
                _raise_quarantine(
                    "calibration split evidence changed after policy issuance",
                    "calibration_split_evidence_substitution",
                )

        return self._record_and_issue(
            evidence=evidence,
            artifact_kind=CalibrationArtifactKind.POLICY,
            capability_type=VerifiedSemanticCalibrationPolicy,
            dependency_check=require_corpus_current,
        )

    def issue_evidence(
        self,
        request: SemanticCalibrationEvidenceRequest,
        *,
        policy: VerifiedSemanticCalibrationPolicy,
        final_material_resolver: FinalMaterialContentResolverPort | None,
        word_counter: AuthenticatedMaterialWordCounterPort | None,
    ) -> VerifiedSemanticCalibrationEvidence:
        """Issue evidence only after every fixed and caller-selected gate passes."""

        policy_evidence = self.require_policy_current(policy)
        policy_request = policy_evidence.request
        try:
            validated = SemanticCalibrationEvidenceRequest.model_validate(
                request.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise CalibrationPromotionError(
                "calibration evidence request is structurally invalid",
                kind=CalibrationFailureKind.FALSIFIED,
                reason_code="invalid_calibration_evidence_request",
            ) from error
        evaluation_capability, evaluation = self._require_independent_evaluation(
            validated,
            policy_evidence,
        )
        self._require_evidence_bindings(validated, policy_evidence)
        self._require_exact_calibration_cases(validated, policy_request)
        self._require_calibration_observations(
            validated,
            policy_evidence,
            final_material_resolver,
            word_counter,
        )
        self._require_per_dimension_gates(validated, policy_request)
        evidence = self._signed_calibration_evidence(validated)
        require_policy_current = self._capture_registered_dependency(
            capability=policy,
            evidence=policy_evidence,
            artifact_kind=CalibrationArtifactKind.POLICY,
        )

        def require_calibration_dependencies_current() -> None:
            require_policy_current()
            current_evaluation = _require_bound_capability(
                capability=evaluation_capability,
                port=self._independent_evaluator,
                evidence_type=IndependentCalibrationEvaluationEvidence,
            )
            if current_evaluation != evaluation:
                _raise_quarantine(
                    "independent calibration evaluation changed after issuance",
                    "independent_calibration_evaluation_substitution",
                )
            self._require_calibration_observations(
                validated,
                policy_evidence,
                final_material_resolver,
                word_counter,
            )

        return self._record_and_issue(
            evidence=evidence,
            artifact_kind=CalibrationArtifactKind.EVIDENCE,
            capability_type=VerifiedSemanticCalibrationEvidence,
            dependency_check=require_calibration_dependencies_current,
        )

    def issue_short_material_policy(
        self,
        request: ShortMaterialThresholdPolicyRequest,
        *,
        calibration: VerifiedSemanticCalibrationEvidence,
        final_material_resolver: FinalMaterialContentResolverPort | None,
        word_counter: AuthenticatedMaterialWordCounterPort | None,
    ) -> VerifiedShortMaterialThresholdPolicy:
        """Issue an artifact-local short-material decision after hard coverage."""

        calibration_evidence = self.require_evidence_current(calibration)
        try:
            validated = ShortMaterialThresholdPolicyRequest.model_validate(
                request.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise CalibrationPromotionError(
                "short-material policy request is structurally invalid",
                kind=CalibrationFailureKind.FALSIFIED,
                reason_code="invalid_short_material_policy_request",
            ) from error
        calibration_sha256 = canonical_semantic_calibration_artifact_sha256(
            calibration_evidence
        )
        if (
            validated.calibration_evidence_payload_sha256 != calibration_sha256
            or validated.corpus_payload_sha256
            != calibration_evidence.request.corpus_payload_sha256
            or validated.calibration_policy_payload_sha256
            != calibration_evidence.request.calibration_policy_payload_sha256
        ):
            _raise_quarantine(
                "short-material policy is bound to substituted calibration evidence",
                "short_material_calibration_substitution",
            )
        _require_material_actor_scope(
            validated.material_identity,
            calibration_evidence.request,
        )
        measurement = self._require_source_measurement(validated.material_identity)
        measurement_bindings = (
            (validated.source_profile, measurement.source_profile),
            (
                validated.terminally_covered_source_clause_count,
                measurement.terminally_covered_source_clause_count,
            ),
            (
                validated.terminally_covered_proposition_count,
                measurement.terminally_covered_proposition_count,
            ),
            (
                validated.terminally_covered_numeric_quantity_count,
                measurement.terminally_covered_numeric_quantity_count,
            ),
            (
                validated.terminally_covered_safety_critical_clause_count,
                measurement.terminally_covered_safety_critical_clause_count,
            ),
            (
                validated.source_measurement_identity_sha256,
                canonical_source_material_measurement_sha256(measurement),
            ),
        )
        if any(observed != expected for observed, expected in measurement_bindings):
            _raise_quarantine(
                "short-material source measurement is substituted",
                "short_material_source_measurement_substitution",
            )
        coverage = (
            (
                measurement.terminally_covered_source_clause_count,
                measurement.source_profile.source_clause_count,
                "clause",
            ),
            (
                measurement.terminally_covered_proposition_count,
                measurement.source_profile.proposition_count,
                "proposition",
            ),
            (
                measurement.terminally_covered_numeric_quantity_count,
                measurement.source_profile.numeric_quantity_count,
                "numeric",
            ),
            (
                measurement.terminally_covered_safety_critical_clause_count,
                measurement.source_profile.safety_critical_clause_count,
                "safety-critical",
            ),
        )
        if any(observed != expected for observed, expected, _ in coverage):
            _raise_needs_review(
                "short material lacks complete authenticated semantic coverage",
                "incomplete_short_material_semantic_coverage",
            )
        resolved, observed_word_count = self._require_resolved_word_count(
            validated.material_identity,
            validated.final_material_resolver_identity_sha256,
            validated.word_counting_policy_sha256,
            final_material_resolver,
            word_counter,
        )
        del resolved
        distribution = self._derive_density_distribution(
            calibration_evidence,
            validated,
        )
        minimum_acceptable_word_count = _minimum_calibrated_word_count(
            distribution.lower_observed_value
        )
        if observed_word_count < minimum_acceptable_word_count:
            _raise_needs_review(
                "material is below its authenticated calibrated density threshold",
                "short_material_underproduction",
            )
        evidence = self._signed_short_material_policy(
            validated,
            observed_material_word_count=observed_word_count,
            density_distribution=distribution,
            minimum_acceptable_word_count=minimum_acceptable_word_count,
        )
        require_calibration_current = self._capture_registered_dependency(
            capability=calibration,
            evidence=calibration_evidence,
            artifact_kind=CalibrationArtifactKind.EVIDENCE,
        )

        return self._record_and_issue(
            evidence=evidence,
            artifact_kind=CalibrationArtifactKind.SHORT_MATERIAL_POLICY,
            capability_type=VerifiedShortMaterialThresholdPolicy,
            dependency_check=require_calibration_current,
        )

    def require_policy_current(
        self,
        capability: VerifiedSemanticCalibrationPolicy,
    ) -> AuthenticatedSemanticCalibrationPolicy:
        return self._require_capability_current(
            capability=capability,
            capability_type=VerifiedSemanticCalibrationPolicy,
            evidence_type=AuthenticatedSemanticCalibrationPolicy,
            artifact_kind=CalibrationArtifactKind.POLICY,
        )

    def require_evidence_current(
        self,
        capability: VerifiedSemanticCalibrationEvidence,
    ) -> AuthenticatedSemanticCalibrationEvidence:
        return self._require_capability_current(
            capability=capability,
            capability_type=VerifiedSemanticCalibrationEvidence,
            evidence_type=AuthenticatedSemanticCalibrationEvidence,
            artifact_kind=CalibrationArtifactKind.EVIDENCE,
        )

    def require_short_material_policy_current(
        self,
        capability: VerifiedShortMaterialThresholdPolicy,
        *,
        final_material_resolver: FinalMaterialContentResolverPort | None,
        word_counter: AuthenticatedMaterialWordCounterPort | None,
    ) -> AuthenticatedShortMaterialThresholdPolicy:
        evidence = self._require_capability_current(
            capability=capability,
            capability_type=VerifiedShortMaterialThresholdPolicy,
            evidence_type=AuthenticatedShortMaterialThresholdPolicy,
            artifact_kind=CalibrationArtifactKind.SHORT_MATERIAL_POLICY,
        )
        _, observed_word_count = self._require_resolved_word_count(
            evidence.request.material_identity,
            evidence.request.final_material_resolver_identity_sha256,
            evidence.request.word_counting_policy_sha256,
            final_material_resolver,
            word_counter,
        )
        if observed_word_count != evidence.observed_material_word_count:
            _raise_quarantine(
                "current final-material count differs from signed evidence",
                "short_material_count_substitution",
            )
        measurement = self._require_source_measurement(
            evidence.request.material_identity
        )
        if (
            measurement.source_profile != evidence.request.source_profile
            or canonical_source_material_measurement_sha256(measurement)
            != evidence.request.source_measurement_identity_sha256
        ):
            _raise_quarantine(
                "current short-material source measurement is substituted",
                "current_short_material_source_measurement_substitution",
            )
        return evidence

    def verify_policy_evidence(
        self,
        evidence: AuthenticatedSemanticCalibrationPolicy,
        *,
        corpus: VerifiedVeterinaryExpertCorpus,
        corpus_authority: VeterinaryCorpusPromotionHmacAuthority,
    ) -> VerifiedSemanticCalibrationPolicy:
        """Rehydrate one current signed policy without trusting process memory."""

        validated = AuthenticatedSemanticCalibrationPolicy.model_validate(
            evidence.model_dump(mode="python", warnings=False)
        )
        self._require_authentic(validated, CalibrationArtifactKind.POLICY)
        self._require_current_state(
            _semantic_calibration_state_record(
                validated,
                CalibrationArtifactKind.POLICY,
            )
        )
        corpus_evidence = corpus_authority.require_current(corpus)
        if validated.request.corpus_payload_sha256 != (
            canonical_expert_corpus_evidence_sha256(corpus_evidence)
        ):
            _raise_quarantine(
                "rehydrated calibration policy corpus is substituted",
                "rehydrated_calibration_corpus_substitution",
            )
        self._require_split_equals_promoted_corpus(
            validated.request.split_manifest,
            corpus_evidence.request.corpus_manifest,
        )
        split_capability, split_evidence = self._require_split_evidence(
            validated.request.corpus_payload_sha256,
            validated.request.split_manifest,
        )

        def require_corpus_current() -> None:
            current = corpus_authority.verify(corpus_evidence)
            corpus_authority.require_current(current)
            current_split = _require_bound_capability(
                capability=split_capability,
                port=self._split_evidence,
                evidence_type=AuthenticatedCalibrationSplitEvidence,
            )
            if current_split != split_evidence:
                _raise_quarantine(
                    "rehydrated calibration split evidence is substituted",
                    "rehydrated_calibration_split_evidence_substitution",
                )

        return self._restore_capability(
            evidence=validated,
            artifact_kind=CalibrationArtifactKind.POLICY,
            capability_type=VerifiedSemanticCalibrationPolicy,
            dependency_check=require_corpus_current,
        )

    def verify_calibration_evidence(
        self,
        evidence: AuthenticatedSemanticCalibrationEvidence,
        *,
        policy: VerifiedSemanticCalibrationPolicy,
        final_material_resolver: FinalMaterialContentResolverPort | None,
        word_counter: AuthenticatedMaterialWordCounterPort | None,
    ) -> VerifiedSemanticCalibrationEvidence:
        """Rehydrate current calibration evidence and all exact dependencies."""

        validated = AuthenticatedSemanticCalibrationEvidence.model_validate(
            evidence.model_dump(mode="python", warnings=False)
        )
        self._require_authentic(validated, CalibrationArtifactKind.EVIDENCE)
        self._require_current_state(
            _semantic_calibration_state_record(
                validated,
                CalibrationArtifactKind.EVIDENCE,
            )
        )
        policy_evidence = self.require_policy_current(policy)
        evaluation_capability, evaluation = self._require_independent_evaluation(
            validated.request,
            policy_evidence,
        )
        self._require_evidence_bindings(validated.request, policy_evidence)
        self._require_exact_calibration_cases(
            validated.request,
            policy_evidence.request,
        )
        self._require_calibration_observations(
            validated.request,
            policy_evidence,
            final_material_resolver,
            word_counter,
        )
        self._require_per_dimension_gates(
            validated.request,
            policy_evidence.request,
        )
        require_policy_current = self._capture_registered_dependency(
            capability=policy,
            evidence=policy_evidence,
            artifact_kind=CalibrationArtifactKind.POLICY,
        )

        def require_calibration_dependencies_current() -> None:
            require_policy_current()
            current_evaluation = _require_bound_capability(
                capability=evaluation_capability,
                port=self._independent_evaluator,
                evidence_type=IndependentCalibrationEvaluationEvidence,
            )
            if current_evaluation != evaluation:
                _raise_quarantine(
                    "independent calibration evaluation changed after rehydration",
                    "rehydrated_calibration_evaluation_substitution",
                )
            self._require_calibration_observations(
                validated.request,
                policy_evidence,
                final_material_resolver,
                word_counter,
            )

        return self._restore_capability(
            evidence=validated,
            artifact_kind=CalibrationArtifactKind.EVIDENCE,
            capability_type=VerifiedSemanticCalibrationEvidence,
            dependency_check=require_calibration_dependencies_current,
        )

    def verify_short_material_evidence(
        self,
        evidence: AuthenticatedShortMaterialThresholdPolicy,
        *,
        calibration: VerifiedSemanticCalibrationEvidence,
        final_material_resolver: FinalMaterialContentResolverPort | None,
        word_counter: AuthenticatedMaterialWordCounterPort | None,
    ) -> VerifiedShortMaterialThresholdPolicy:
        """Rehydrate one artifact-local decision from persistent signed evidence."""

        validated = AuthenticatedShortMaterialThresholdPolicy.model_validate(
            evidence.model_dump(mode="python", warnings=False)
        )
        self._require_authentic(
            validated,
            CalibrationArtifactKind.SHORT_MATERIAL_POLICY,
        )
        self._require_current_state(
            _semantic_calibration_state_record(
                validated,
                CalibrationArtifactKind.SHORT_MATERIAL_POLICY,
            )
        )
        calibration_evidence = self.require_evidence_current(calibration)
        if (
            validated.request.calibration_evidence_payload_sha256
            != canonical_semantic_calibration_artifact_sha256(calibration_evidence)
        ):
            _raise_quarantine(
                "rehydrated short-material calibration is substituted",
                "rehydrated_short_material_calibration_substitution",
            )
        _require_material_actor_scope(
            validated.request.material_identity,
            calibration_evidence.request,
        )
        measurement = self._require_source_measurement(
            validated.request.material_identity
        )
        if (
            measurement.source_profile != validated.request.source_profile
            or canonical_source_material_measurement_sha256(measurement)
            != validated.request.source_measurement_identity_sha256
        ):
            _raise_quarantine(
                "rehydrated short-material measurement is substituted",
                "rehydrated_short_material_measurement_substitution",
            )
        _, observed_word_count = self._require_resolved_word_count(
            validated.request.material_identity,
            validated.request.final_material_resolver_identity_sha256,
            validated.request.word_counting_policy_sha256,
            final_material_resolver,
            word_counter,
        )
        distribution = self._derive_density_distribution(
            calibration_evidence,
            validated.request,
        )
        if (
            observed_word_count != validated.observed_material_word_count
            or distribution != validated.density_distribution
            or _minimum_calibrated_word_count(distribution.lower_observed_value)
            != validated.minimum_acceptable_word_count
        ):
            _raise_quarantine(
                "rehydrated short-material decision is substituted",
                "rehydrated_short_material_decision_substitution",
            )
        require_calibration_current = self._capture_registered_dependency(
            capability=calibration,
            evidence=calibration_evidence,
            artifact_kind=CalibrationArtifactKind.EVIDENCE,
        )

        return self._restore_capability(
            evidence=validated,
            artifact_kind=CalibrationArtifactKind.SHORT_MATERIAL_POLICY,
            capability_type=VerifiedShortMaterialThresholdPolicy,
            dependency_check=require_calibration_current,
        )

    def _require_capability_current[EvidenceModel: BaseModel](
        self,
        *,
        capability: object,
        capability_type: type[object],
        evidence_type: type[EvidenceModel],
        artifact_kind: CalibrationArtifactKind,
    ) -> EvidenceModel:
        if type(capability) is not capability_type:
            _raise_quarantine(
                "semantic calibration capability type is invalid",
                "forged_calibration_capability",
            )
        registration = _CAPABILITY_REGISTRATIONS.get(id(capability))
        if (
            registration is None
            or registration.capability_reference() is not capability
            or registration.authority_reference() is not self
            or registration.artifact_kind is not artifact_kind
        ):
            _raise_quarantine(
                "semantic calibration capability was not issued by this authority",
                "foreign_calibration_capability",
            )
        raw_evidence = getattr(capability, "evidence", None)
        if not isinstance(raw_evidence, BaseModel):
            _raise_quarantine(
                "semantic calibration capability has no valid evidence",
                "missing_calibration_capability_evidence",
            )
        try:
            evidence = evidence_type.model_validate(
                raw_evidence.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise CalibrationPromotionError(
                "semantic calibration capability evidence is invalid",
                kind=CalibrationFailureKind.FALSIFIED,
                reason_code="invalid_calibration_capability_evidence",
            ) from error
        payload_sha256 = canonical_semantic_calibration_artifact_sha256(evidence)
        if not hmac.compare_digest(
            registration.payload_sha256, payload_sha256
        ) or not hmac.compare_digest(
            registration.detached_signature,
            str(getattr(evidence, "detached_signature")),
        ):
            _raise_quarantine(
                "semantic calibration capability evidence was substituted",
                "substituted_calibration_capability",
            )
        self._require_authentic(
            cast(
                AuthenticatedSemanticCalibrationPolicy
                | AuthenticatedSemanticCalibrationEvidence
                | AuthenticatedShortMaterialThresholdPolicy,
                evidence,
            ),
            artifact_kind,
        )
        self._require_current_state(
            _semantic_calibration_state_record(
                cast(
                    AuthenticatedSemanticCalibrationPolicy
                    | AuthenticatedSemanticCalibrationEvidence
                    | AuthenticatedShortMaterialThresholdPolicy,
                    evidence,
                ),
                artifact_kind,
            )
        )
        registration.dependency_check()
        return evidence

    def _capture_registered_dependency(
        self,
        *,
        capability: object,
        evidence: (
            AuthenticatedSemanticCalibrationPolicy
            | AuthenticatedSemanticCalibrationEvidence
        ),
        artifact_kind: CalibrationArtifactKind,
    ) -> Callable[[], None]:
        registration = _CAPABILITY_REGISTRATIONS.get(id(capability))
        if (
            registration is None
            or registration.capability_reference() is not capability
            or registration.authority_reference() is not self
            or registration.artifact_kind is not artifact_kind
        ):
            _raise_quarantine(
                "calibration dependency capability is not issuer-owned",
                "foreign_calibration_dependency",
            )
        payload_sha256 = canonical_semantic_calibration_artifact_sha256(evidence)
        detached_signature = evidence.detached_signature
        ancestor_check = registration.dependency_check

        def require_dependency_current() -> None:
            if not hmac.compare_digest(
                registration.payload_sha256, payload_sha256
            ) or not hmac.compare_digest(
                registration.detached_signature,
                detached_signature,
            ):
                _raise_quarantine(
                    "calibration dependency registration is substituted",
                    "substituted_calibration_dependency",
                )
            self._require_authentic(evidence, artifact_kind)
            self._require_current_state(
                _semantic_calibration_state_record(evidence, artifact_kind)
            )
            ancestor_check()

        return require_dependency_current

    def _require_split_evidence(
        self,
        corpus_payload_sha256: str,
        split_manifest: CalibrationSplitManifest,
    ) -> tuple[
        VerifiedCalibrationSplitEvidence,
        AuthenticatedCalibrationSplitEvidence,
    ]:
        try:
            capability = self._split_evidence.resolve_current(
                corpus_payload_sha256=corpus_payload_sha256,
                split_manifest=split_manifest,
                capability_issuer=self._split_evidence_capability_issuer,
            )
            evidence = _require_bound_capability(
                capability=capability,
                port=self._split_evidence,
                evidence_type=AuthenticatedCalibrationSplitEvidence,
            )
        except CalibrationPromotionError:
            raise
        except Exception as error:
            raise CalibrationPromotionError(
                "authenticated calibration split evidence is unavailable",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="calibration_split_evidence_unavailable",
            ) from error
        if (
            evidence.corpus_payload_sha256 != corpus_payload_sha256
            or evidence.split_manifest != split_manifest
        ):
            _raise_quarantine(
                "authenticated calibration split evidence is substituted",
                "calibration_split_evidence_scope_substitution",
            )
        return capability, evidence

    def _require_independent_evaluation(
        self,
        request: SemanticCalibrationEvidenceRequest,
        policy: AuthenticatedSemanticCalibrationPolicy,
    ) -> tuple[
        VerifiedIndependentCalibrationEvaluation,
        IndependentCalibrationEvaluationEvidence,
    ]:
        calibration_cases = tuple(
            CalibrationReferenceCaseBinding(
                case_id=lineage.case_id,
                reference_case_sha256=lineage.reference_case_sha256,
            )
            for lineage in policy.request.split_manifest.cases
            if lineage.partition is CalibrationPartition.CALIBRATION
        )
        scope = IndependentCalibrationEvaluationScope(
            calibration_policy_payload_sha256=(
                canonical_semantic_calibration_artifact_sha256(policy)
            ),
            corpus_payload_sha256=policy.request.corpus_payload_sha256,
            split_manifest_sha256=canonical_calibration_split_sha256(
                policy.request.split_manifest
            ),
            run_id=request.run_id,
            producer_identity_sha256s=policy.request.producer_identity_sha256s,
            producer_model_identity_sha256s=(
                policy.request.producer_model_identity_sha256s
            ),
            evaluator_identity_sha256=policy.request.evaluator_identity_sha256,
            evaluator_model_identity_sha256=(
                policy.request.evaluator_model_identity_sha256
            ),
            runtime_profile_sha256=policy.request.runtime_profile_sha256,
            tokenizer_identity_sha256=policy.request.tokenizer_identity_sha256,
            task_identity_sha256=policy.request.task_identity_sha256,
            reference_cases=calibration_cases,
        )
        try:
            capability = self._independent_evaluator.evaluate_current(
                scope,
                capability_issuer=self._evaluation_capability_issuer,
            )
            evidence = _require_bound_capability(
                capability=capability,
                port=self._independent_evaluator,
                evidence_type=IndependentCalibrationEvaluationEvidence,
            )
        except CalibrationPromotionError:
            raise
        except Exception as error:
            raise CalibrationPromotionError(
                "independent calibration evaluation is unavailable",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="independent_calibration_evaluation_unavailable",
            ) from error
        if evidence.scope != scope:
            _raise_quarantine(
                "independent calibration evaluation scope is substituted",
                "independent_calibration_evaluation_scope_substitution",
            )
        bindings = (
            (request.case_outcomes, evidence.case_outcomes),
            (request.dimension_metrics, evidence.dimension_metrics),
            (request.deterministic_gates, evidence.deterministic_gates),
            (request.sample_count, evidence.sample_count),
            (request.exclusion_count, evidence.exclusion_count),
            (request.failure_count, evidence.failure_count),
            (
                request.run_started_at_epoch_seconds,
                evidence.run_started_at_epoch_seconds,
            ),
            (
                request.run_completed_at_epoch_seconds,
                evidence.run_completed_at_epoch_seconds,
            ),
            (
                request.evaluation_artifact_sha256,
                evidence.evaluation_artifact_sha256,
            ),
        )
        if any(actual != expected for actual, expected in bindings):
            _raise_quarantine(
                "caller calibration assertions differ from independent evidence",
                "caller_calibration_assertion_substitution",
            )
        return capability, evidence

    @staticmethod
    def _require_split_equals_promoted_corpus(
        split: CalibrationSplitManifest,
        corpus: ReferenceCorpusManifest,
    ) -> None:
        manifest_sha256 = canonical_reference_corpus_manifest_sha256(corpus)
        if (
            str(split.corpus_id) != str(corpus.corpus_id)
            or str(split.corpus_version) != str(corpus.corpus_version)
            or split.corpus_manifest_sha256 != manifest_sha256
        ):
            _raise_quarantine(
                "calibration split corpus identity is substituted",
                "calibration_split_corpus_identity_mismatch",
            )
        corpus_cases = {str(case.case_id): case for case in corpus.cases}
        if tuple(str(case.case_id) for case in split.cases) != tuple(corpus_cases):
            _raise_quarantine(
                "calibration split does not equal the promoted corpus inventory",
                "calibration_split_inventory_mismatch",
            )
        for lineage in split.cases:
            reference = corpus_cases[str(lineage.case_id)]
            artifacts = reference.artifact_identities
            bindings = (
                (str(lineage.lecture_id), str(reference.lecture_id)),
                (str(lineage.learning_unit_id), str(reference.learning_unit_id)),
                (str(lineage.source_clause_id), str(reference.source_clause_id)),
                (
                    lineage.reference_case_sha256,
                    canonical_reference_corpus_case_sha256(reference),
                ),
                (
                    lineage.source_manifest_sha256,
                    artifacts.source_manifest_sha256,
                ),
                (
                    lineage.source_content_aggregation_sha256,
                    artifacts.source_content_aggregation_sha256,
                ),
                (lineage.source_universe_sha256, artifacts.source_universe_sha256),
                (
                    lineage.clause_inventory_sha256,
                    artifacts.clause_inventory_sha256,
                ),
                (
                    lineage.candidate_batches_sha256,
                    artifacts.candidate_batches_sha256,
                ),
                (lineage.atomic_ledger_sha256, artifacts.atomic_ledger_sha256),
                (
                    lineage.reconciliations_sha256,
                    artifacts.reconciliations_sha256,
                ),
            )
            if any(actual != expected for actual, expected in bindings):
                _raise_quarantine(
                    "calibration split lineage differs from promoted corpus evidence",
                    "calibration_split_lineage_mismatch",
                )

    def _require_evidence_bindings(
        self,
        evidence: SemanticCalibrationEvidenceRequest,
        policy: AuthenticatedSemanticCalibrationPolicy,
    ) -> None:
        expected = policy.request
        bindings = (
            (
                evidence.corpus_payload_sha256,
                expected.corpus_payload_sha256,
            ),
            (
                evidence.calibration_policy_payload_sha256,
                canonical_semantic_calibration_artifact_sha256(policy),
            ),
            (
                evidence.split_manifest_sha256,
                canonical_calibration_split_sha256(expected.split_manifest),
            ),
            (
                evidence.producer_identity_sha256s,
                expected.producer_identity_sha256s,
            ),
            (
                evidence.producer_model_identity_sha256s,
                expected.producer_model_identity_sha256s,
            ),
            (
                evidence.evaluator_identity_sha256,
                expected.evaluator_identity_sha256,
            ),
            (
                evidence.evaluator_model_identity_sha256,
                expected.evaluator_model_identity_sha256,
            ),
            (
                evidence.runtime_profile_sha256,
                expected.runtime_profile_sha256,
            ),
            (
                evidence.tokenizer_identity_sha256,
                expected.tokenizer_identity_sha256,
            ),
            (
                evidence.task_identity_sha256,
                expected.task_identity_sha256,
            ),
        )
        if (
            any(actual != expected_value for actual, expected_value in bindings)
            or evidence.short_material_rule != expected.short_material_rule
        ):
            _raise_quarantine(
                "calibration evidence identity binding is substituted",
                "calibration_evidence_binding_mismatch",
            )

    @staticmethod
    def _require_exact_calibration_cases(
        evidence: SemanticCalibrationEvidenceRequest,
        policy: SemanticCalibrationPolicyRequest,
    ) -> None:
        expected_case_ids = tuple(
            str(case.case_id)
            for case in policy.split_manifest.cases
            if case.partition is CalibrationPartition.CALIBRATION
        )
        observed_case_ids = tuple(
            str(outcome.case_id) for outcome in evidence.case_outcomes
        )
        if observed_case_ids != expected_case_ids:
            _raise_quarantine(
                "calibration outcomes do not equal the calibration partition",
                "calibration_case_cherry_picking",
            )
        expected_reference_hashes = {
            str(case.case_id): case.reference_case_sha256
            for case in policy.split_manifest.cases
            if case.partition is CalibrationPartition.CALIBRATION
        }
        if any(
            outcome.reference_case_sha256
            != expected_reference_hashes[str(outcome.case_id)]
            for outcome in evidence.case_outcomes
        ):
            _raise_quarantine(
                "calibration outcome reference case is substituted",
                "calibration_reference_case_substitution",
            )
        exclusions = tuple(
            outcome
            for outcome in evidence.case_outcomes
            if outcome.exclusion_reason_sha256 is not None
        )
        permitted = set(policy.permitted_exclusion_reason_sha256s)
        if any(
            outcome.exclusion_reason_sha256 not in permitted for outcome in exclusions
        ):
            _raise_quarantine(
                "calibration outcome uses an unauthorized exclusion",
                "unauthorized_calibration_exclusion",
            )
        included = len(evidence.case_outcomes) - len(exclusions)
        failures = sum(
            bool(outcome.failed_dimensions)
            for outcome in evidence.case_outcomes
            if outcome.exclusion_reason_sha256 is None
        )
        if (
            evidence.sample_count != included
            or evidence.exclusion_count != len(exclusions)
            or evidence.failure_count != failures
        ):
            _raise_quarantine(
                "calibration sample, exclusion, or failure counts are inconsistent",
                "calibration_summary_count_mismatch",
            )
        included_outcomes = tuple(
            outcome
            for outcome in evidence.case_outcomes
            if outcome.exclusion_reason_sha256 is None
        )
        for metric in evidence.dimension_metrics:
            dimension_failure_count = sum(
                metric.dimension in outcome.failed_dimensions
                for outcome in included_outcomes
            )
            dimension_pass_count = sum(
                metric.dimension in outcome.passed_dimensions
                for outcome in included_outcomes
            )
            if (
                metric.sample_count != included
                or metric.exclusion_count != len(exclusions)
                or metric.failure_count != dimension_failure_count
                or metric.denominator != included
                or metric.numerator != dimension_pass_count
            ):
                _raise_quarantine(
                    "calibration metric does not match immutable case outcomes",
                    "calibration_dimension_count_mismatch",
                )

    def _require_calibration_observations(
        self,
        evidence: SemanticCalibrationEvidenceRequest,
        policy: AuthenticatedSemanticCalibrationPolicy,
        resolver: FinalMaterialContentResolverPort | None,
        word_counter: AuthenticatedMaterialWordCounterPort | None,
    ) -> None:
        rule = policy.request.short_material_rule
        policy_sha256 = canonical_semantic_calibration_artifact_sha256(policy)
        matching_observation_count = 0
        for outcome in evidence.case_outcomes:
            if outcome.exclusion_reason_sha256 is not None:
                continue
            observation = outcome.short_material_observation
            if observation is None:
                _raise_needs_review(
                    "calibration case lacks resolved short-material evidence",
                    "missing_short_material_calibration_observation",
                )
            if (
                str(observation.source_profile.specialty_id) != str(rule.specialty_id)
                or str(observation.source_profile.material_type)
                != str(rule.material_type)
                or observation.final_material_resolver_identity_sha256
                != rule.final_material_resolver_identity_sha256
                or observation.word_counting_policy_sha256
                != rule.word_counting_policy_sha256
                or observation.material_identity.expert_corpus_payload_sha256
                != policy.request.corpus_payload_sha256
                or observation.material_identity.calibration_policy_payload_sha256
                != policy_sha256
            ):
                _raise_quarantine(
                    "calibration short-material observation identity is substituted",
                    "short_material_observation_identity_mismatch",
                )
            _require_material_actor_scope(
                observation.material_identity,
                policy.request,
            )
            measurement = self._require_source_measurement(
                observation.material_identity
            )
            measurement_bindings = (
                (observation.source_profile, measurement.source_profile),
                (
                    observation.terminally_covered_source_clause_count,
                    measurement.terminally_covered_source_clause_count,
                ),
                (
                    observation.terminally_covered_proposition_count,
                    measurement.terminally_covered_proposition_count,
                ),
                (
                    observation.terminally_covered_numeric_quantity_count,
                    measurement.terminally_covered_numeric_quantity_count,
                ),
                (
                    observation.terminally_covered_safety_critical_clause_count,
                    measurement.terminally_covered_safety_critical_clause_count,
                ),
                (
                    observation.source_measurement_identity_sha256,
                    canonical_source_material_measurement_sha256(measurement),
                ),
            )
            if any(observed != expected for observed, expected in measurement_bindings):
                _raise_quarantine(
                    "calibration source measurement is substituted",
                    "calibration_source_measurement_substitution",
                )
            _, observed_word_count = self._require_resolved_word_count(
                observation.material_identity,
                observation.final_material_resolver_identity_sha256,
                observation.word_counting_policy_sha256,
                resolver,
                word_counter,
            )
            if observation.material_word_count != observed_word_count:
                _raise_quarantine(
                    "calibration short-material word count is substituted",
                    "short_material_observation_count_mismatch",
                )
            matching_observation_count += 1
        if matching_observation_count < rule.minimum_case_count:
            _raise_needs_review(
                "calibration has insufficient short-material reference cases",
                "insufficient_short_material_calibration_sample",
            )

    def _require_source_measurement(
        self,
        material_identity: FinalStudyMaterialIdentity,
    ) -> AuthenticatedSourceMaterialMeasurement:
        try:
            capability = self._source_measurements.resolve_current(
                material_identity,
                capability_issuer=self._source_measurement_capability_issuer,
            )
            evidence = _require_bound_capability(
                capability=capability,
                port=self._source_measurements,
                evidence_type=AuthenticatedSourceMaterialMeasurement,
            )
        except CalibrationPromotionError:
            raise
        except Exception as error:
            raise CalibrationPromotionError(
                "authenticated source-material measurement is unavailable",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="source_material_measurement_unavailable",
            ) from error
        if evidence.material_identity != material_identity:
            _raise_quarantine(
                "source-material measurement identity is substituted",
                "source_material_measurement_identity_substitution",
            )
        return evidence

    @staticmethod
    def _require_resolved_word_count(
        material_identity: FinalStudyMaterialIdentity,
        resolver_identity_sha256: str,
        counting_policy_sha256: str,
        resolver: FinalMaterialContentResolverPort | None,
        word_counter: AuthenticatedMaterialWordCounterPort | None,
    ) -> tuple[ResolvedFinalMaterialContent, int]:
        if not isinstance(resolver, FinalMaterialContentResolverPort):
            _raise_needs_review(
                "authenticated final-material resolver is unavailable",
                "final_material_resolver_unavailable",
            )
        if not isinstance(word_counter, AuthenticatedMaterialWordCounterPort):
            _raise_needs_review(
                "authenticated material word counter is unavailable",
                "material_word_counter_unavailable",
            )
        try:
            word_counter.require_current()
            if not hmac.compare_digest(
                resolver.resolver_identity_sha256,
                resolver_identity_sha256,
            ):
                _raise_quarantine(
                    "final-material resolver identity is substituted",
                    "final_material_resolver_identity_substitution",
                )
            if not hmac.compare_digest(
                word_counter.policy_payload_sha256,
                counting_policy_sha256,
            ):
                _raise_quarantine(
                    "material word-counting policy is substituted",
                    "material_word_counting_policy_substitution",
                )
            resolved = resolver.require_current_bytes(material_identity)
        except CalibrationPromotionError:
            raise
        except Exception as error:
            raise CalibrationPromotionError(
                "authenticated final-material evidence is unavailable",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="final_material_evidence_unavailable",
            ) from error
        if not isinstance(resolved, ResolvedFinalMaterialContent):
            _raise_quarantine(
                "final-material resolver returned invalid evidence",
                "invalid_final_material_resolution",
            )
        artifact_sha256 = hashlib.sha256(resolved.artifact_bytes).hexdigest()
        text_sha256 = hashlib.sha256(resolved.canonical_text_utf8).hexdigest()
        if (
            len(resolved.artifact_bytes) != material_identity.material_byte_length
            or not hmac.compare_digest(
                artifact_sha256,
                material_identity.final_artifact_sha256,
            )
            or not hmac.compare_digest(
                text_sha256,
                material_identity.material_text_sha256,
            )
        ):
            _raise_quarantine(
                "resolved final-material bytes do not match their identity",
                "final_material_content_substitution",
            )
        try:
            word_count = word_counter.count_words(resolved.canonical_text_utf8)
        except Exception as error:
            raise CalibrationPromotionError(
                "authenticated material word counting is unavailable",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="material_word_count_unavailable",
            ) from error
        if isinstance(word_count, bool) or not isinstance(word_count, int):
            _raise_quarantine(
                "material word counter returned an invalid count",
                "invalid_material_word_count",
            )
        if word_count < 1:
            _raise_needs_review(
                "resolved final material contains no countable words",
                "empty_final_material_word_count",
            )
        return resolved, word_count

    @staticmethod
    def _derive_density_distribution(
        calibration: AuthenticatedSemanticCalibrationEvidence,
        request: ShortMaterialThresholdPolicyRequest,
    ) -> CalibratedDensityDistribution:
        source_scale_sha256 = canonical_short_material_source_scale_sha256(
            request.source_profile
        )
        observations = tuple(
            outcome.short_material_observation
            for outcome in calibration.request.case_outcomes
            if outcome.exclusion_reason_sha256 is None
            and outcome.short_material_observation is not None
            and str(outcome.short_material_observation.source_profile.specialty_id)
            == str(request.source_profile.specialty_id)
            and str(outcome.short_material_observation.source_profile.material_type)
            == str(request.source_profile.material_type)
            and outcome.short_material_observation.final_material_resolver_identity_sha256
            == request.final_material_resolver_identity_sha256
            and outcome.short_material_observation.word_counting_policy_sha256
            == request.word_counting_policy_sha256
        )
        policy = calibration.request.calibration_policy_payload_sha256
        scaled_observations = tuple(
            sorted(
                (
                    *_calibrated_word_count_projection(
                        observation.material_word_count,
                        target=request.source_profile,
                        reference=observation.source_profile,
                    ),
                    canonical_short_material_source_scale_sha256(
                        observation.source_profile
                    ),
                    observation.material_identity.final_artifact_sha256,
                )
                for observation in observations
            )
        )
        rule = calibration.request.short_material_rule
        if (
            str(rule.specialty_id) != str(request.source_profile.specialty_id)
            or str(rule.material_type) != str(request.source_profile.material_type)
            or rule.final_material_resolver_identity_sha256
            != request.final_material_resolver_identity_sha256
            or rule.word_counting_policy_sha256 != request.word_counting_policy_sha256
        ):
            _raise_quarantine(
                "short-material request does not match calibrated policy scope",
                "short_material_rule_substitution",
            )
        minimum_case_count = rule.minimum_case_count
        if len(scaled_observations) < minimum_case_count:
            _raise_needs_review(
                "calibration has insufficient matching short-material observations",
                "insufficient_short_material_calibration_sample",
            )
        calibrated_word_counts = tuple(item[0] for item in scaled_observations)
        lower = calibrated_word_counts[0]
        upper = calibrated_word_counts[-1]
        median = _median_decimal_values(calibrated_word_counts)
        distribution_payload = {
            "calibration_evidence_payload_sha256": (
                canonical_semantic_calibration_artifact_sha256(calibration)
            ),
            "calibration_policy_payload_sha256": policy,
            "counting_policy_sha256": request.word_counting_policy_sha256,
            "final_material_resolver_identity_sha256": (
                request.final_material_resolver_identity_sha256
            ),
            "material_type": str(request.source_profile.material_type),
            "calibrated_word_count_projections": [
                {
                    "feature_ratios": {
                        feature_name: str(feature_ratio)
                        for feature_name, feature_ratio in feature_ratios
                    },
                    "material_sha256": material_sha256,
                    "projected_minimum_word_count": str(projected_word_count),
                    "source_scale_sha256": observation_scale_sha256,
                }
                for (
                    projected_word_count,
                    feature_ratios,
                    observation_scale_sha256,
                    material_sha256,
                ) in scaled_observations
            ],
            "specialty_id": str(request.source_profile.specialty_id),
            "source_scale_formula_sha256": _SOURCE_SCALE_FORMULA_SHA256,
            "source_scale_sha256": source_scale_sha256,
        }
        distribution_sha256 = hashlib.sha256(
            _IDENTITY_CONTEXT + canonical_json_bytes(distribution_payload)
        ).hexdigest()
        return CalibratedDensityDistribution(
            distribution_id=f"density-{distribution_sha256[:16]}",
            metric_id="calibration-reference-word-count-maximum-feature-ratio-v2",
            source_scale_sha256=source_scale_sha256,
            source_scale_formula_sha256=_SOURCE_SCALE_FORMULA_SHA256,
            sample_count=len(scaled_observations),
            lower_observed_value=lower,
            median_observed_value=median,
            upper_observed_value=upper,
            uncertainty_lower=lower,
            uncertainty_upper=upper,
            distribution_artifact_sha256=distribution_sha256,
        )

    @staticmethod
    def _require_per_dimension_gates(
        evidence: SemanticCalibrationEvidenceRequest,
        policy: SemanticCalibrationPolicyRequest,
    ) -> None:
        gates = evidence.deterministic_gates
        if (
            gates.terminal_source_clause_disposition_count
            != gates.total_source_clause_count
        ):
            _raise_needs_review(
                "source-clause terminal disposition is below 100 percent",
                "incomplete_terminal_clause_disposition",
            )
        if (
            gates.recalled_safety_critical_clause_count
            != gates.safety_critical_source_clause_count
        ):
            _raise_needs_review(
                "safety-critical clause recall is below 100 percent",
                "incomplete_safety_critical_recall",
            )
        forbidden_counts = (
            gates.accepted_semantic_reversal_count,
            gates.accepted_unsupported_safety_critical_claim_count,
            gates.accepted_unauthenticated_numeric_mutation_count,
        )
        if any(forbidden_counts):
            _raise_quarantine(
                "calibration accepted a forbidden semantic safety mutation",
                "accepted_forbidden_semantic_mutation",
            )

        metrics = {
            (metric.dimension, str(metric.metric_id)): metric
            for metric in evidence.dimension_metrics
        }
        for threshold in policy.thresholds:
            metric = metrics.get((threshold.dimension, str(threshold.metric_id)))
            if metric is None:
                _raise_needs_review(
                    "calibration metric required by policy is missing",
                    "missing_calibration_metric",
                )
            if metric.sample_count < threshold.minimum_sample_count:
                _raise_needs_review(
                    "calibration metric sample is below authenticated policy",
                    "insufficient_calibration_sample",
                )
            uncertainty_width = metric.uncertainty_upper - metric.uncertainty_lower
            if uncertainty_width > threshold.maximum_uncertainty_width:
                _raise_needs_review(
                    "calibration metric uncertainty exceeds authenticated policy",
                    "excessive_calibration_uncertainty",
                )
            if not _threshold_passes(metric.point_estimate, threshold):
                _raise_needs_review(
                    "an individually blocking calibration dimension failed",
                    f"failed_dimension_{threshold.dimension.value}",
                )

    def _signed_policy(
        self,
        request: SemanticCalibrationPolicyRequest,
    ) -> AuthenticatedSemanticCalibrationPolicy:
        authentication_payload = _calibration_authentication_payload(
            request=request,
            signature_purpose=SEMANTIC_CALIBRATION_POLICY_SIGNATURE_PURPOSE,
            trust_key_id=self.key_id,
            authority_id=self.authority_id,
        )
        return AuthenticatedSemanticCalibrationPolicy(
            request=request,
            cryptographic_domain="lecture-study.semantic-calibration.v1",
            signature_purpose="semantic-calibration-policy",
            trust_key_id=self.key_id,
            authority_id=self.authority_id,
            detached_signature=self._sign(
                authentication_payload,
                SEMANTIC_CALIBRATION_POLICY_SIGNATURE_PURPOSE,
            ),
        )

    def _signed_calibration_evidence(
        self,
        request: SemanticCalibrationEvidenceRequest,
    ) -> AuthenticatedSemanticCalibrationEvidence:
        authentication_payload = _calibration_authentication_payload(
            request=request,
            signature_purpose=SEMANTIC_CALIBRATION_POLICY_SIGNATURE_PURPOSE,
            trust_key_id=self.key_id,
            authority_id=self.authority_id,
        )
        return AuthenticatedSemanticCalibrationEvidence(
            request=request,
            cryptographic_domain="lecture-study.semantic-calibration.v1",
            signature_purpose="semantic-calibration-policy",
            trust_key_id=self.key_id,
            authority_id=self.authority_id,
            detached_signature=self._sign(
                authentication_payload,
                SEMANTIC_CALIBRATION_POLICY_SIGNATURE_PURPOSE,
            ),
        )

    def _signed_short_material_policy(
        self,
        request: ShortMaterialThresholdPolicyRequest,
        *,
        observed_material_word_count: int,
        density_distribution: CalibratedDensityDistribution,
        minimum_acceptable_word_count: int,
    ) -> AuthenticatedShortMaterialThresholdPolicy:
        unsigned_payload = {
            "authority_id": self.authority_id,
            "cryptographic_domain": SEMANTIC_CALIBRATION_CRYPTOGRAPHIC_DOMAIN,
            "density_distribution": density_distribution.model_dump(mode="json"),
            "minimum_acceptable_word_count": minimum_acceptable_word_count,
            "observed_material_word_count": observed_material_word_count,
            "production_eligible": False,
            "request": request.model_dump(mode="json"),
            "signature_purpose": SHORT_MATERIAL_THRESHOLD_SIGNATURE_PURPOSE,
            "trust_key_id": self.key_id,
        }
        return AuthenticatedShortMaterialThresholdPolicy(
            request=request,
            cryptographic_domain="lecture-study.semantic-calibration.v1",
            signature_purpose="short-material-threshold-policy",
            trust_key_id=self.key_id,
            authority_id=self.authority_id,
            observed_material_word_count=observed_material_word_count,
            density_distribution=density_distribution,
            minimum_acceptable_word_count=minimum_acceptable_word_count,
            production_eligible=False,
            detached_signature=self._sign(
                canonical_json_bytes(unsigned_payload),
                SHORT_MATERIAL_THRESHOLD_SIGNATURE_PURPOSE,
            ),
        )

    def _record_and_issue[CapabilityType: object, EvidenceType: BaseModel](
        self,
        *,
        evidence: EvidenceType,
        artifact_kind: CalibrationArtifactKind,
        capability_type: type[CapabilityType],
        dependency_check: Callable[[], None],
    ) -> CapabilityType:
        payload_sha256 = canonical_semantic_calibration_artifact_sha256(evidence)
        state_record = _semantic_calibration_state_record(
            cast(
                AuthenticatedSemanticCalibrationPolicy
                | AuthenticatedSemanticCalibrationEvidence
                | AuthenticatedShortMaterialThresholdPolicy,
                evidence,
            ),
            artifact_kind,
        )
        try:
            self._current_state.record_issued(state_record)
        except CalibrationPromotionError:
            raise
        except Exception as error:
            raise CalibrationPromotionError(
                "semantic calibration state registration is unavailable",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="calibration_state_registration_unavailable",
            ) from error
        self._require_current_state(state_record)
        capability = object.__new__(capability_type)
        object.__setattr__(capability, "evidence", evidence)
        _register_capability(
            capability=capability,
            authority=self,
            artifact_kind=artifact_kind,
            payload_sha256=payload_sha256,
            detached_signature=str(getattr(evidence, "detached_signature")),
            dependency_check=dependency_check,
        )
        return capability

    def _restore_capability[CapabilityType: object, EvidenceType: BaseModel](
        self,
        *,
        evidence: EvidenceType,
        artifact_kind: CalibrationArtifactKind,
        capability_type: type[CapabilityType],
        dependency_check: Callable[[], None],
    ) -> CapabilityType:
        payload_sha256 = canonical_semantic_calibration_artifact_sha256(evidence)
        state_record = _semantic_calibration_state_record(
            cast(
                AuthenticatedSemanticCalibrationPolicy
                | AuthenticatedSemanticCalibrationEvidence
                | AuthenticatedShortMaterialThresholdPolicy,
                evidence,
            ),
            artifact_kind,
        )
        self._require_current_state(state_record)
        dependency_check()
        capability = object.__new__(capability_type)
        object.__setattr__(capability, "evidence", evidence)
        _register_capability(
            capability=capability,
            authority=self,
            artifact_kind=artifact_kind,
            payload_sha256=payload_sha256,
            detached_signature=str(getattr(evidence, "detached_signature")),
            dependency_check=dependency_check,
        )
        return capability

    def _require_current_state(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        try:
            self._current_state.require_current(record)
        except CalibrationPromotionError:
            raise
        except Exception as error:
            raise CalibrationPromotionError(
                "semantic calibration current state is unavailable",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="calibration_current_state_unavailable",
            ) from error

    def _require_authentic(
        self,
        evidence: (
            AuthenticatedSemanticCalibrationPolicy
            | AuthenticatedSemanticCalibrationEvidence
            | AuthenticatedShortMaterialThresholdPolicy
        ),
        artifact_kind: CalibrationArtifactKind,
    ) -> None:
        expected_purpose = (
            SHORT_MATERIAL_THRESHOLD_SIGNATURE_PURPOSE
            if artifact_kind is CalibrationArtifactKind.SHORT_MATERIAL_POLICY
            else SEMANTIC_CALIBRATION_POLICY_SIGNATURE_PURPOSE
        )
        if (
            evidence.cryptographic_domain != SEMANTIC_CALIBRATION_CRYPTOGRAPHIC_DOMAIN
            or evidence.signature_purpose != expected_purpose
            or not hmac.compare_digest(evidence.trust_key_id, self.key_id)
            or str(evidence.authority_id) != self.authority_id
        ):
            _raise_quarantine(
                "semantic calibration trust scope is invalid",
                "semantic_calibration_trust_confusion",
            )
        expected_signature = self._sign(
            evidence.authentication_payload(),
            expected_purpose,
        )
        if not hmac.compare_digest(expected_signature, evidence.detached_signature):
            _raise_quarantine(
                "semantic calibration signature is invalid",
                "invalid_semantic_calibration_signature",
            )

    def _sign(self, payload: bytes, purpose: str) -> str:
        context = (
            SEMANTIC_CALIBRATION_CRYPTOGRAPHIC_DOMAIN.encode("ascii")
            + b"\0"
            + purpose.encode("ascii")
            + b"\0"
        )
        return hmac.new(
            self._key._material,
            context + payload,
            hashlib.sha256,
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ShortMaterialAssessment:
    """Local decision preserving independent DAG continuation."""

    disposition: CalibrationDisposition
    reason_code: str

    @property
    def independent_work_may_continue(self) -> bool:
        return True

    @property
    def publication_ready(self) -> bool:
        return False


class ShortMaterialPolicyAssessor:
    """Consume only issuer-authenticated material-local threshold evidence."""

    def assess(
        self,
        capability: VerifiedShortMaterialThresholdPolicy | None,
        *,
        authority: SemanticCalibrationHmacAuthority,
        material_identity: FinalStudyMaterialIdentity,
        source_profile: ShortMaterialSourceProfile,
        final_material_resolver: FinalMaterialContentResolverPort | None,
        word_counter: AuthenticatedMaterialWordCounterPort | None,
    ) -> ShortMaterialAssessment:
        if capability is None:
            return ShortMaterialAssessment(
                disposition=CalibrationDisposition.NEEDS_REVIEW,
                reason_code="missing_authenticated_short_material_policy",
            )
        try:
            evidence = authority.require_short_material_policy_current(
                capability,
                final_material_resolver=final_material_resolver,
                word_counter=word_counter,
            )
        except CalibrationPromotionError as error:
            return ShortMaterialAssessment(
                disposition=error.disposition,
                reason_code=error.reason_code,
            )
        if (
            evidence.request.material_identity != material_identity
            or evidence.request.source_profile != source_profile
        ):
            return ShortMaterialAssessment(
                disposition=CalibrationDisposition.QUARANTINE,
                reason_code="substituted_short_material_input",
            )
        return ShortMaterialAssessment(
            disposition=CalibrationDisposition.NEEDS_REVIEW,
            reason_code="nonproduction_short_material_policy",
        )


def canonical_calibration_split_sha256(
    split: CalibrationSplitManifest,
) -> str:
    """Identify the entire leakage-controlled split in its own domain."""

    validated = CalibrationSplitManifest.model_validate(
        split.model_dump(mode="python", warnings=False)
    )
    return hashlib.sha256(
        _SPLIT_CONTEXT + canonical_json_bytes(validated.model_dump(mode="json"))
    ).hexdigest()


def canonical_semantic_calibration_artifact_sha256(
    evidence: BaseModel,
) -> str:
    """Identify one full calibration payload independently of its signature."""

    payload = evidence.model_dump(
        mode="json",
        exclude={"detached_signature"},
    )
    return hashlib.sha256(_IDENTITY_CONTEXT + canonical_json_bytes(payload)).hexdigest()


def _semantic_calibration_state_record(
    evidence: (
        AuthenticatedSemanticCalibrationPolicy
        | AuthenticatedSemanticCalibrationEvidence
        | AuthenticatedShortMaterialThresholdPolicy
    ),
    artifact_kind: CalibrationArtifactKind,
) -> SemanticCalibrationStateRecord:
    request = evidence.request
    if artifact_kind is CalibrationArtifactKind.POLICY:
        if not isinstance(request, SemanticCalibrationPolicyRequest):
            raise TypeError("calibration policy state request type is invalid")
        artifact_id = request.policy_id
        artifact_version = request.policy_version
    elif artifact_kind is CalibrationArtifactKind.EVIDENCE:
        if not isinstance(request, SemanticCalibrationEvidenceRequest):
            raise TypeError("calibration evidence state request type is invalid")
        artifact_id = request.evidence_id
        artifact_version = request.run_id
    else:
        if not isinstance(request, ShortMaterialThresholdPolicyRequest):
            raise TypeError("short-material state request type is invalid")
        artifact_id = request.policy_id
        artifact_version = request.policy_version
    return SemanticCalibrationStateRecord(
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        payload_sha256=canonical_semantic_calibration_artifact_sha256(evidence),
        trust_key_id=evidence.trust_key_id,
        authority_id=evidence.authority_id,
        authority_sequence=request.authority_sequence,
        issued_at_epoch_seconds=request.issued_at_epoch_seconds,
        expires_at_epoch_seconds=request.expires_at_epoch_seconds,
    )


def canonical_source_material_measurement_sha256(
    evidence: AuthenticatedSourceMaterialMeasurement,
) -> str:
    """Identify the complete authenticated source-derived measurement."""

    validated = AuthenticatedSourceMaterialMeasurement.model_validate(
        evidence.model_dump(mode="python", warnings=False)
    )
    return hashlib.sha256(
        _IDENTITY_CONTEXT
        + b"source-material-measurement\0"
        + canonical_json_bytes(validated.model_dump(mode="json"))
    ).hexdigest()


def canonical_short_material_source_scale_sha256(
    profile: ShortMaterialSourceProfile,
) -> str:
    """Identify every calibrated source-scale feature without weighting guesses."""

    validated = ShortMaterialSourceProfile.model_validate(
        profile.model_dump(mode="python", warnings=False)
    )
    payload = {
        "authenticated_ocr_byte_count": validated.authenticated_ocr_byte_count,
        "authenticated_transcript_byte_count": (
            validated.authenticated_transcript_byte_count
        ),
        "instructional_primary_visual_span_count": (
            validated.instructional_primary_visual_span_count
        ),
        "material_type": str(validated.material_type),
        "numeric_quantity_count": validated.numeric_quantity_count,
        "proposition_count": validated.proposition_count,
        "safety_critical_clause_count": validated.safety_critical_clause_count,
        "source_clause_count": validated.source_clause_count,
        "source_duration_milliseconds": validated.source_duration_milliseconds,
        "specialty_id": str(validated.specialty_id),
    }
    return hashlib.sha256(
        _IDENTITY_CONTEXT
        + b"short-material-source-scale\0"
        + canonical_json_bytes(payload)
    ).hexdigest()


def _authentication_payload(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json", exclude={"detached_signature"}),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _calibration_authentication_payload(
    *,
    request: BaseModel,
    signature_purpose: str,
    trust_key_id: str,
    authority_id: str,
) -> bytes:
    return canonical_json_bytes(
        {
            "authority_id": authority_id,
            "cryptographic_domain": SEMANTIC_CALIBRATION_CRYPTOGRAPHIC_DOMAIN,
            "request": request.model_dump(mode="json"),
            "signature_purpose": signature_purpose,
            "trust_key_id": trust_key_id,
        }
    )


def _create_bound_issuer[IssuerType: object](
    issuer_type: type[IssuerType],
    port: object,
) -> IssuerType:
    issuer = object.__new__(issuer_type)
    object.__setattr__(issuer, "_port", port)
    issuer_id = id(issuer)

    def discard(reference: weakref.ReferenceType[IssuerType]) -> None:
        registration = _BOUND_ISSUER_REGISTRATIONS.get(issuer_id)
        if registration is not None and registration.issuer_reference is reference:
            _BOUND_ISSUER_REGISTRATIONS.pop(issuer_id, None)

    reference = weakref.ref(issuer, discard)
    _BOUND_ISSUER_REGISTRATIONS[issuer_id] = _BoundIssuerRegistration(
        issuer_reference=cast(weakref.ReferenceType[object], reference),
        port=port,
    )
    return issuer


def _issue_bound_capability[
    CapabilityType: object,
    EvidenceType: BaseModel,
](
    *,
    issuer: object,
    port: object,
    evidence: EvidenceType,
    capability_type: type[CapabilityType],
) -> CapabilityType:
    issuer_registration = _BOUND_ISSUER_REGISTRATIONS.get(id(issuer))
    if (
        issuer_registration is None
        or issuer_registration.issuer_reference() is not issuer
        or issuer_registration.port is not port
    ):
        _raise_quarantine(
            "bound evidence issuer does not belong to the configured port",
            "foreign_bound_evidence_issuer",
        )
    validated = type(evidence).model_validate(
        evidence.model_dump(mode="python", warnings=False)
    )
    capability = object.__new__(capability_type)
    object.__setattr__(capability, "evidence", validated)
    capability_id = id(capability)

    def discard(reference: weakref.ReferenceType[CapabilityType]) -> None:
        registration = _BOUND_CAPABILITY_REGISTRATIONS.get(capability_id)
        if registration is not None and registration.capability_reference is reference:
            _BOUND_CAPABILITY_REGISTRATIONS.pop(capability_id, None)

    capability_reference = weakref.ref(capability, discard)
    _BOUND_CAPABILITY_REGISTRATIONS[capability_id] = _BoundCapabilityRegistration(
        capability_reference=cast(
            weakref.ReferenceType[object],
            capability_reference,
        ),
        issuer_reference=issuer_registration.issuer_reference,
        port=port,
        evidence_sha256=hashlib.sha256(
            _IDENTITY_CONTEXT
            + b"bound-external-evidence\0"
            + canonical_json_bytes(validated.model_dump(mode="json"))
        ).hexdigest(),
    )
    return capability


def _require_bound_capability[EvidenceType: BaseModel](
    *,
    capability: object,
    port: object,
    evidence_type: type[EvidenceType],
) -> EvidenceType:
    registration = _BOUND_CAPABILITY_REGISTRATIONS.get(id(capability))
    if (
        registration is None
        or registration.capability_reference() is not capability
        or registration.issuer_reference() is None
        or registration.port is not port
    ):
        _raise_quarantine(
            "external evidence capability is not issuer-owned",
            "foreign_external_evidence_capability",
        )
    current: BaseModel
    try:
        if isinstance(port, IndependentCalibrationEvaluatorPort):
            current = port.require_current(
                cast(VerifiedIndependentCalibrationEvaluation, capability)
            )
        elif isinstance(port, AuthenticatedSourceMaterialMeasurementPort):
            current = port.require_current(
                cast(VerifiedSourceMaterialMeasurement, capability)
            )
        elif isinstance(port, AuthenticatedCalibrationSplitEvidencePort):
            current = port.require_current(
                cast(VerifiedCalibrationSplitEvidence, capability)
            )
        else:
            raise TypeError("external evidence port is unsupported")
        validated = evidence_type.model_validate(
            current.model_dump(mode="python", warnings=False)
        )
    except CalibrationPromotionError:
        raise
    except Exception as error:
        raise CalibrationPromotionError(
            "external evidence capability is unavailable",
            kind=CalibrationFailureKind.UNAVAILABLE,
            reason_code="external_evidence_capability_unavailable",
        ) from error
    observed_sha256 = hashlib.sha256(
        _IDENTITY_CONTEXT
        + b"bound-external-evidence\0"
        + canonical_json_bytes(validated.model_dump(mode="json"))
    ).hexdigest()
    if not hmac.compare_digest(observed_sha256, registration.evidence_sha256):
        _raise_quarantine(
            "external evidence capability was substituted",
            "external_evidence_capability_substitution",
        )
    return validated


def _register_capability(
    *,
    capability: object,
    authority: SemanticCalibrationHmacAuthority,
    artifact_kind: CalibrationArtifactKind,
    payload_sha256: str,
    detached_signature: str,
    dependency_check: Callable[[], None],
) -> None:
    capability_id = id(capability)

    def discard(reference: weakref.ReferenceType[object]) -> None:
        registration = _CAPABILITY_REGISTRATIONS.get(capability_id)
        if registration is not None and registration.capability_reference is reference:
            _CAPABILITY_REGISTRATIONS.pop(capability_id, None)

    reference = weakref.ref(capability, discard)
    _CAPABILITY_REGISTRATIONS[capability_id] = _CapabilityRegistration(
        capability_reference=reference,
        authority_reference=weakref.ref(authority),
        artifact_kind=artifact_kind,
        payload_sha256=payload_sha256,
        detached_signature=detached_signature,
        dependency_check=dependency_check,
    )


def _threshold_passes(
    point_estimate: Decimal,
    threshold: CalibrationThreshold,
) -> bool:
    if threshold.comparator is ThresholdComparator.GREATER_THAN_OR_EQUAL:
        return point_estimate >= threshold.threshold
    return point_estimate <= threshold.threshold


def _require_no_cross_partition_overlap(
    cases: tuple[CalibrationCaseLineage, ...],
    *,
    values: Callable[[CalibrationCaseLineage], tuple[str, ...]],
    label: str,
) -> None:
    value_extractor = values
    seen: dict[str, CalibrationPartition] = {}
    for case in cases:
        extracted = value_extractor(case)
        for value in extracted:
            previous = seen.get(value)
            if previous is not None and previous is not case.partition:
                raise ValueError(f"{label} crosses calibration partitions")
            seen[value] = case.partition


def _require_one_split_identity(
    cases: tuple[CalibrationCaseLineage, ...],
    *,
    values: Callable[[CalibrationCaseLineage], str],
    label: str,
) -> None:
    if len({values(case) for case in cases}) != 1:
        raise ValueError(f"calibration split {label} identity is inconsistent")


def _require_material_profile_bindings(
    material: FinalStudyMaterialIdentity,
    source: ShortMaterialSourceProfile,
) -> None:
    bindings = (
        (
            material.source_content_aggregation_sha256,
            source.source_content_aggregation_sha256,
        ),
        (material.clause_inventory_sha256, source.clause_inventory_sha256),
        (material.atomic_ledger_sha256, source.atomic_ledger_sha256),
        (material.reconciliation_sha256, source.reconciliation_set_sha256),
    )
    if any(actual != expected for actual, expected in bindings):
        raise ValueError("final material and source-profile identities do not match")


def _require_material_actor_scope(
    material: FinalStudyMaterialIdentity,
    calibrated_scope: (
        SemanticCalibrationPolicyRequest | SemanticCalibrationEvidenceRequest
    ),
) -> None:
    bindings = (
        (material.runtime_profile_sha256, calibrated_scope.runtime_profile_sha256),
        (
            material.producer_identity_sha256s,
            calibrated_scope.producer_identity_sha256s,
        ),
        (
            material.producer_model_identity_sha256s,
            calibrated_scope.producer_model_identity_sha256s,
        ),
        (
            material.evaluator_identity_sha256,
            calibrated_scope.evaluator_identity_sha256,
        ),
        (
            material.evaluator_model_identity_sha256,
            calibrated_scope.evaluator_model_identity_sha256,
        ),
        (
            material.tokenizer_identity_sha256,
            calibrated_scope.tokenizer_identity_sha256,
        ),
    )
    if any(actual != expected for actual, expected in bindings):
        _raise_quarantine(
            "final material actors differ from authenticated calibration scope",
            "final_material_calibrated_actor_scope_substitution",
        )


def _source_scale_features(
    profile: ShortMaterialSourceProfile,
) -> tuple[tuple[str, int], ...]:
    return (
        ("source_duration_milliseconds", profile.source_duration_milliseconds),
        (
            "authenticated_transcript_byte_count",
            profile.authenticated_transcript_byte_count,
        ),
        ("authenticated_ocr_byte_count", profile.authenticated_ocr_byte_count),
        (
            "instructional_primary_visual_span_count",
            profile.instructional_primary_visual_span_count,
        ),
        ("source_clause_count", profile.source_clause_count),
        ("proposition_count", profile.proposition_count),
        ("numeric_quantity_count", profile.numeric_quantity_count),
        (
            "safety_critical_clause_count",
            profile.safety_critical_clause_count,
        ),
    )


def _calibrated_feature_ratio(
    feature_name: str,
    target_value: int,
    reference_value: int,
) -> Decimal:
    if reference_value == 0:
        if target_value == 0:
            return Decimal(1)
        _raise_needs_review(
            f"calibration reference lacks the {feature_name} source feature",
            "uncalibrated_short_material_source_feature",
        )
    with localcontext() as context:
        context.prec = 80
        return Decimal(target_value) / Decimal(reference_value)


def _source_scale_feature_ratios(
    target: ShortMaterialSourceProfile,
    reference: ShortMaterialSourceProfile,
) -> tuple[tuple[str, Decimal], ...]:
    reference_features = dict(_source_scale_features(reference))
    return tuple(
        (
            feature_name,
            _calibrated_feature_ratio(
                feature_name,
                target_value,
                reference_features[feature_name],
            ),
        )
        for feature_name, target_value in _source_scale_features(target)
    )


def _calibrated_word_count_projection(
    word_count: int,
    *,
    target: ShortMaterialSourceProfile,
    reference: ShortMaterialSourceProfile,
) -> tuple[Decimal, tuple[tuple[str, Decimal], ...]]:
    feature_ratios = _source_scale_feature_ratios(target, reference)
    conservative_scale = max(ratio for _, ratio in feature_ratios)
    with localcontext() as context:
        context.prec = 80
        projected_word_count = Decimal(word_count) * conservative_scale
    return projected_word_count, feature_ratios


def _minimum_calibrated_word_count(projected_word_count: Decimal) -> int:
    with localcontext() as context:
        context.prec = 80
        minimum = projected_word_count.to_integral_value(rounding=ROUND_CEILING)
    return max(1, int(minimum))


def _median_decimal_values(values: tuple[Decimal, ...]) -> Decimal:
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal(2)


def _require_unique_canonical(
    identities: tuple[str, ...],
    label: str,
) -> None:
    if len(identities) != len(set(identities)):
        raise ValueError(f"{label} must be unique")
    if identities != tuple(sorted(identities)):
        raise ValueError(f"{label} must use canonical order")


def _raise_needs_review(message: str, reason_code: str) -> NoReturn:
    raise CalibrationPromotionError(
        message,
        kind=CalibrationFailureKind.UNVERIFIABLE,
        reason_code=reason_code,
    )


def _raise_quarantine(message: str, reason_code: str) -> NoReturn:
    raise CalibrationPromotionError(
        message,
        kind=CalibrationFailureKind.INVALID,
        reason_code=reason_code,
    )
