"""Adversarial tests for semantic calibration and short-material promotion."""

from __future__ import annotations

import gc
import hashlib
from decimal import Decimal

import pytest
from pydantic import ValidationError

from lecture_study.quality.calibration_promotion import (
    AuthenticatedCalibrationSplitEvidence,
    AuthenticatedSemanticCalibrationEvidence,
    AuthenticatedSemanticCalibrationPolicy,
    AuthenticatedSourceMaterialMeasurement,
    AuthenticatedSourceMaterialMeasurementPort,
    CalibrationArtifactKind,
    CalibrationCaseLineage,
    CalibrationCaseOutcome,
    CalibrationDeterministicGateCounts,
    CalibrationDimension,
    CalibrationDimensionMetric,
    CalibrationDisposition,
    CalibrationFailureKind,
    CalibrationMetricFormula,
    CalibrationPartition,
    CalibrationPromotionError,
    CalibrationReferenceCaseBinding,
    CalibrationSampleRequirement,
    CalibrationSplitManifest,
    CalibrationStatisticalPolicy,
    CalibrationThreshold,
    CalibrationIntervalKind,
    AuthenticatedMaterialWordCounterPort,
    AuthenticatedCalibrationSplitEvidencePort,
    CalibrationSplitEvidenceCapabilityIssuer,
    FinalMaterialContentResolverPort,
    FinalStudyMaterialIdentity,
    IndependentCalibrationEvaluationCapabilityIssuer,
    IndependentCalibrationEvaluationEvidence,
    IndependentCalibrationEvaluationScope,
    IndependentCalibrationEvaluatorPort,
    ResolvedFinalMaterialContent,
    SemanticCalibrationCurrentStatePort,
    SemanticCalibrationEvidenceRequest,
    SemanticCalibrationHmacAuthority,
    SemanticCalibrationHmacKey,
    SemanticCalibrationPolicyRequest,
    SemanticCalibrationStateRecord,
    ShortMaterialPolicyAssessor,
    ShortMaterialCalibrationObservation,
    ShortMaterialCalibrationRule,
    ShortMaterialSourceProfile,
    ShortMaterialThresholdPolicyRequest,
    SourceMaterialMeasurementCapabilityIssuer,
    ThresholdComparator,
    VerifiedCalibrationSplitEvidence,
    VerifiedIndependentCalibrationEvaluation,
    VerifiedSemanticCalibrationEvidence,
    VerifiedSemanticCalibrationPolicy,
    VerifiedSourceMaterialMeasurement,
    canonical_calibration_split_sha256,
    canonical_source_material_measurement_sha256,
    canonical_semantic_calibration_artifact_sha256,
)
from lecture_study.quality.calibration import (
    canonical_reference_corpus_case_sha256,
    canonical_reference_corpus_manifest_sha256,
)
from lecture_study.quality.expert_corpus import (
    ExpertCorpusPromotionError,
    VerifiedVeterinaryExpertCorpus,
    VeterinaryCorpusPromotionHmacAuthority,
)
from test_expert_corpus import (
    _CurrentCorpusState,
    _authority as _corpus_authority,
    _promotion_request,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


_WORD_COUNTING_POLICY_SHA256 = _digest("synthetic-word-counting-policy")
_FINAL_MATERIAL_RESOLVER_SHA256 = _digest("synthetic-final-material-resolver")
_SYNTHETIC_SOURCE_MEASUREMENTS: dict[
    str,
    AuthenticatedSourceMaterialMeasurement,
] = {}
_SYNTHETIC_EVALUATIONS: dict[str, IndependentCalibrationEvaluationEvidence] = {}


class _SyntheticSplitEvidence(AuthenticatedCalibrationSplitEvidencePort):
    def __init__(self) -> None:
        self.available = True
        self.substituted: AuthenticatedCalibrationSplitEvidence | None = None
        self._issued: dict[int, VerifiedCalibrationSplitEvidence] = {}

    def resolve_current(
        self,
        *,
        corpus_payload_sha256: str,
        split_manifest: CalibrationSplitManifest,
        capability_issuer: CalibrationSplitEvidenceCapabilityIssuer,
    ) -> VerifiedCalibrationSplitEvidence:
        if not self.available:
            raise RuntimeError("synthetic split evidence unavailable")
        evidence = AuthenticatedCalibrationSplitEvidence(
            corpus_payload_sha256=corpus_payload_sha256,
            split_manifest=split_manifest,
            source_lineage_artifact_sha256=_digest("synthetic-source-lineage-evidence"),
            production_eligible=False,
        )
        capability = capability_issuer.issue(port=self, evidence=evidence)
        self._issued[id(capability)] = capability
        return capability

    def require_current(
        self,
        capability: VerifiedCalibrationSplitEvidence,
    ) -> AuthenticatedCalibrationSplitEvidence:
        if not self.available:
            raise RuntimeError("synthetic split evidence unavailable")
        if self._issued.get(id(capability)) is not capability:
            raise RuntimeError("foreign synthetic split capability")
        return self.substituted or capability.evidence


class _SyntheticIndependentEvaluator(IndependentCalibrationEvaluatorPort):
    def __init__(self) -> None:
        self.available = True
        self.substituted: IndependentCalibrationEvaluationEvidence | None = None
        self._issued: dict[int, VerifiedIndependentCalibrationEvaluation] = {}

    def evaluate_current(
        self,
        scope: IndependentCalibrationEvaluationScope,
        *,
        capability_issuer: IndependentCalibrationEvaluationCapabilityIssuer,
    ) -> VerifiedIndependentCalibrationEvaluation:
        if not self.available:
            raise RuntimeError("synthetic independent evaluator unavailable")
        evidence = _SYNTHETIC_EVALUATIONS[str(scope.run_id)]
        if evidence.scope != scope:
            raise RuntimeError("synthetic evaluation scope mismatch")
        capability = capability_issuer.issue(port=self, evidence=evidence)
        self._issued[id(capability)] = capability
        return capability

    def require_current(
        self,
        capability: VerifiedIndependentCalibrationEvaluation,
    ) -> IndependentCalibrationEvaluationEvidence:
        if not self.available:
            raise RuntimeError("synthetic independent evaluator unavailable")
        if self._issued.get(id(capability)) is not capability:
            raise RuntimeError("foreign synthetic evaluation capability")
        return self.substituted or capability.evidence


class _SyntheticSourceMeasurements(AuthenticatedSourceMaterialMeasurementPort):
    def __init__(self) -> None:
        self.available = True
        self.substituted: AuthenticatedSourceMaterialMeasurement | None = None
        self._issued: dict[int, VerifiedSourceMaterialMeasurement] = {}

    def resolve_current(
        self,
        material_identity: FinalStudyMaterialIdentity,
        *,
        capability_issuer: SourceMaterialMeasurementCapabilityIssuer,
    ) -> VerifiedSourceMaterialMeasurement:
        if not self.available:
            raise RuntimeError("synthetic source measurements unavailable")
        evidence = _SYNTHETIC_SOURCE_MEASUREMENTS[
            material_identity.final_artifact_sha256
        ]
        capability = capability_issuer.issue(port=self, evidence=evidence)
        self._issued[id(capability)] = capability
        return capability

    def require_current(
        self,
        capability: VerifiedSourceMaterialMeasurement,
    ) -> AuthenticatedSourceMaterialMeasurement:
        if not self.available:
            raise RuntimeError("synthetic source measurements unavailable")
        if self._issued.get(id(capability)) is not capability:
            raise RuntimeError("foreign synthetic source-measurement capability")
        return self.substituted or capability.evidence


class _SyntheticWordCounter(AuthenticatedMaterialWordCounterPort):
    def __init__(
        self,
        *,
        available: bool = True,
        policy_payload_sha256: str = _WORD_COUNTING_POLICY_SHA256,
    ) -> None:
        self.available = available
        self._policy_payload_sha256 = policy_payload_sha256

    @property
    def policy_payload_sha256(self) -> str:
        return self._policy_payload_sha256

    def require_current(self) -> None:
        if not self.available:
            raise RuntimeError("synthetic word-counting policy unavailable")

    def count_words(self, content: bytes) -> int:
        return len(content.decode("utf-8").split())


class _SyntheticFinalMaterialResolver(FinalMaterialContentResolverPort):
    def __init__(
        self,
        entries: tuple[
            tuple[FinalStudyMaterialIdentity, ResolvedFinalMaterialContent], ...
        ],
        *,
        available: bool = True,
    ) -> None:
        self._entries = {
            identity.final_artifact_sha256: (identity, resolved)
            for identity, resolved in entries
        }
        self.available = available

    @property
    def resolver_identity_sha256(self) -> str:
        return _FINAL_MATERIAL_RESOLVER_SHA256

    def require_current_bytes(
        self,
        material_identity: FinalStudyMaterialIdentity,
    ) -> ResolvedFinalMaterialContent:
        if not self.available:
            raise RuntimeError("synthetic final-material resolver unavailable")
        expected, resolved = self._entries[material_identity.final_artifact_sha256]
        if expected != material_identity:
            raise CalibrationPromotionError(
                "synthetic final-material identity mismatch",
                kind=CalibrationFailureKind.SUBSTITUTED,
                reason_code="synthetic_final_material_substitution",
            )
        return resolved


class _CurrentCalibrationState(SemanticCalibrationCurrentStatePort):
    def __init__(self) -> None:
        self.current: dict[
            tuple[CalibrationArtifactKind, str],
            SemanticCalibrationStateRecord,
        ] = {}
        self.revoked: set[tuple[CalibrationArtifactKind, str]] = set()
        self.available = True

    def record_issued(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        if not self.available:
            raise RuntimeError("synthetic calibration state unavailable")
        self.current[(record.artifact_kind, record.payload_sha256)] = record

    def require_current(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        if not self.available:
            raise RuntimeError("synthetic calibration state unavailable")
        identity = (record.artifact_kind, record.payload_sha256)
        if identity in self.revoked:
            raise CalibrationPromotionError(
                "synthetic calibration artifact revoked",
                kind=CalibrationFailureKind.REVOKED,
                reason_code="revoked_calibration_artifact",
            )
        if self.current.get(identity) != record:
            raise RuntimeError("synthetic calibration artifact missing")


def _calibration_authority(
    state: _CurrentCalibrationState,
    *,
    authority_id: str = "synthetic-calibration-authority",
    key_label: str = "primary",
) -> SemanticCalibrationHmacAuthority:
    material = (
        f"synthetic-non-production-calibration-key-{key_label}-not-a-real-secret"
    ).encode()
    return SemanticCalibrationHmacAuthority(
        key=SemanticCalibrationHmacKey(material),
        authority_id=authority_id,
        current_state=state,
        independent_evaluator=_SyntheticIndependentEvaluator(),
        source_measurements=_SyntheticSourceMeasurements(),
        split_evidence=_SyntheticSplitEvidence(),
    )


def _promoted_corpus() -> tuple[
    VerifiedVeterinaryExpertCorpus,
    VeterinaryCorpusPromotionHmacAuthority,
]:
    corpus_state = _CurrentCorpusState()
    corpus_authority = _corpus_authority(corpus_state)
    return corpus_authority.issue(_promotion_request()), corpus_authority


def _split_manifest() -> CalibrationSplitManifest:
    manifest = _promotion_request().corpus_manifest
    partitions = (
        CalibrationPartition.TRAIN,
        CalibrationPartition.DEVELOPMENT,
        CalibrationPartition.CALIBRATION,
        CalibrationPartition.FINAL_HELD_OUT,
    )
    return CalibrationSplitManifest(
        schema_version=1,
        split_id="synthetic-split",
        split_version="synthetic-v1",
        corpus_id=manifest.corpus_id,
        corpus_version=manifest.corpus_version,
        corpus_manifest_sha256=canonical_reference_corpus_manifest_sha256(manifest),
        cases=tuple(
            CalibrationCaseLineage(
                case_id=reference.case_id,
                partition=partition,
                lecture_id=reference.lecture_id,
                learning_unit_id=reference.learning_unit_id,
                source_clause_id=reference.source_clause_id,
                reference_case_sha256=canonical_reference_corpus_case_sha256(reference),
                source_manifest_sha256=(
                    reference.artifact_identities.source_manifest_sha256
                ),
                source_artifact_ids=(f"artifact-{index}",),
                source_record_ids=(f"record-{index}",),
                source_content_aggregation_sha256=(
                    reference.artifact_identities.source_content_aggregation_sha256
                ),
                source_universe_sha256=(
                    reference.artifact_identities.source_universe_sha256
                ),
                clause_inventory_sha256=(
                    reference.artifact_identities.clause_inventory_sha256
                ),
                candidate_batches_sha256=(
                    reference.artifact_identities.candidate_batches_sha256
                ),
                atomic_ledger_sha256=(
                    reference.artifact_identities.atomic_ledger_sha256
                ),
                reconciliations_sha256=(
                    reference.artifact_identities.reconciliations_sha256
                ),
                near_duplicate_cluster_ids=(f"cluster-{index}",),
                derivative_root_sha256=_digest(f"derivative-root-{index}"),
                specialty_id="synthetic-specialty",
                task_taxonomy_sha256=_digest("synthetic-task"),
                split_policy_sha256=_digest("synthetic-split-policy"),
                deduplication_policy_sha256=_digest("synthetic-deduplication-policy"),
            )
            for index, (partition, reference) in enumerate(
                zip(partitions, manifest.cases, strict=True)
            )
        ),
    )


def _dimensions() -> tuple[CalibrationDimension, ...]:
    return tuple(sorted(CalibrationDimension, key=str))


def _policy_request(corpus_sha256: str) -> SemanticCalibrationPolicyRequest:
    return SemanticCalibrationPolicyRequest(
        schema_version=1,
        policy_id="synthetic-calibration-policy",
        policy_version="synthetic-v1",
        corpus_payload_sha256=corpus_sha256,
        split_manifest=_split_manifest(),
        producer_identity_sha256s=tuple(
            sorted(
                (
                    _digest("synthetic-producer-a"),
                    _digest("synthetic-producer-b"),
                )
            )
        ),
        producer_model_identity_sha256s=tuple(
            sorted(
                (
                    _digest("synthetic-producer-model-a"),
                    _digest("synthetic-producer-model-b"),
                )
            )
        ),
        evaluator_identity_sha256=_digest("synthetic-independent-evaluator"),
        evaluator_model_identity_sha256=_digest(
            "synthetic-independent-evaluator-model"
        ),
        runtime_profile_sha256=_digest("synthetic-runtime-profile"),
        tokenizer_identity_sha256=_digest("synthetic-tokenizer"),
        task_identity_sha256=_digest("synthetic-task"),
        statistical_policy=CalibrationStatisticalPolicy(
            sample_size_method_sha256=_digest("synthetic-sample-size-method"),
            minimum_cases_per_task_and_specialty=(
                CalibrationSampleRequirement(
                    task_identity_sha256=_digest("synthetic-task"),
                    specialty_id="synthetic-specialty",
                    minimum_case_count=1,
                ),
            ),
            uncertainty_method_sha256=_digest("synthetic-uncertainty-method"),
            interval_kind=CalibrationIntervalKind.CONFIDENCE,
            interval_level=Decimal("0.95"),
            exclusion_policy_sha256=_digest("synthetic-exclusion-policy"),
            stopping_rule_sha256=_digest("synthetic-stopping-rule"),
            failure_policy_sha256=_digest("synthetic-failure-policy"),
        ),
        blocking_dimensions=_dimensions(),
        thresholds=tuple(
            CalibrationThreshold(
                dimension=dimension,
                metric_id=f"metric-{dimension.value}",
                metric_formula=CalibrationMetricFormula.CASE_DIMENSION_PASS_RATE,
                comparator=ThresholdComparator.GREATER_THAN_OR_EQUAL,
                threshold=Decimal("1"),
                minimum_sample_count=1,
                maximum_uncertainty_width=Decimal("0"),
            )
            for dimension in _dimensions()
        ),
        short_material_rule=ShortMaterialCalibrationRule(
            specialty_id="synthetic-specialty",
            material_type="synthetic-study-guide",
            final_material_resolver_identity_sha256=(_FINAL_MATERIAL_RESOLVER_SHA256),
            word_counting_policy_sha256=_WORD_COUNTING_POLICY_SHA256,
            threshold_formula=(
                "minimum-calibrated-word-count-maximum-feature-ratio-v2"
            ),
            minimum_case_count=1,
        ),
        permitted_exclusion_reason_sha256s=(),
        authority_sequence=1,
        issued_at_epoch_seconds=100,
        expires_at_epoch_seconds=900,
    )


def _passing_metrics() -> tuple[CalibrationDimensionMetric, ...]:
    return tuple(
        CalibrationDimensionMetric(
            dimension=dimension,
            metric_id=f"metric-{dimension.value}",
            numerator=1,
            denominator=1,
            point_estimate=Decimal("1"),
            uncertainty_lower=Decimal("1"),
            uncertainty_upper=Decimal("1"),
            sample_count=1,
            exclusion_count=0,
            failure_count=0,
        )
        for dimension in _dimensions()
    )


def _material_fixture(
    *,
    label: str,
    word_count: int,
    corpus_payload_sha256: str,
    calibration_policy_payload_sha256: str,
    calibrated_scope: (
        SemanticCalibrationPolicyRequest | SemanticCalibrationEvidenceRequest
    ),
) -> tuple[
    FinalStudyMaterialIdentity,
    ShortMaterialSourceProfile,
    ResolvedFinalMaterialContent,
]:
    words = (f"{label}-word",) + tuple(
        f"word-{index}" for index in range(word_count - 1)
    )
    canonical_text = " ".join(words).encode()
    source_aggregation_sha256 = _digest(f"{label}-source-aggregation")
    clause_inventory_sha256 = _digest(f"{label}-clause-inventory")
    atomic_ledger_sha256 = _digest(f"{label}-atomic-ledger")
    reconciliation_sha256 = _digest(f"{label}-reconciliation")
    identity = FinalStudyMaterialIdentity(
        lecture_id=f"{label}-lecture",
        learning_unit_id=f"{label}-unit",
        material_id=f"{label}-material",
        material_version="synthetic-v1",
        final_artifact_sha256=hashlib.sha256(canonical_text).hexdigest(),
        material_byte_length=len(canonical_text),
        material_text_sha256=hashlib.sha256(canonical_text).hexdigest(),
        clause_inventory_sha256=clause_inventory_sha256,
        atomic_ledger_sha256=atomic_ledger_sha256,
        reconciliation_sha256=reconciliation_sha256,
        source_content_aggregation_sha256=source_aggregation_sha256,
        runtime_profile_sha256=calibrated_scope.runtime_profile_sha256,
        producer_identity_sha256s=calibrated_scope.producer_identity_sha256s,
        producer_model_identity_sha256s=(
            calibrated_scope.producer_model_identity_sha256s
        ),
        evaluator_identity_sha256=calibrated_scope.evaluator_identity_sha256,
        evaluator_model_identity_sha256=(
            calibrated_scope.evaluator_model_identity_sha256
        ),
        tokenizer_identity_sha256=calibrated_scope.tokenizer_identity_sha256,
        expert_corpus_payload_sha256=corpus_payload_sha256,
        calibration_policy_payload_sha256=calibration_policy_payload_sha256,
    )
    source_profile = ShortMaterialSourceProfile(
        source_duration_milliseconds=7_200_000,
        authenticated_transcript_byte_count=80_000,
        authenticated_ocr_byte_count=20_000,
        source_content_aggregation_sha256=source_aggregation_sha256,
        source_universe_sha256=_digest(f"{label}-source-universe"),
        instructional_primary_visual_span_count=30,
        visual_inventory_sha256=_digest(f"{label}-visual-inventory"),
        source_clause_count=10,
        clause_inventory_sha256=clause_inventory_sha256,
        proposition_count=20,
        numeric_quantity_count=4,
        safety_critical_clause_count=2,
        atomic_ledger_sha256=atomic_ledger_sha256,
        reconciliation_set_sha256=reconciliation_sha256,
        specialty_id="synthetic-specialty",
        material_type="synthetic-study-guide",
    )
    resolved = ResolvedFinalMaterialContent(
        artifact_bytes=canonical_text,
        canonical_text_utf8=canonical_text,
    )
    measurement = AuthenticatedSourceMaterialMeasurement(
        material_identity=identity,
        source_profile=source_profile,
        terminally_covered_source_clause_count=source_profile.source_clause_count,
        terminally_covered_proposition_count=source_profile.proposition_count,
        terminally_covered_numeric_quantity_count=(
            source_profile.numeric_quantity_count
        ),
        terminally_covered_safety_critical_clause_count=(
            source_profile.safety_critical_clause_count
        ),
        measurement_artifact_sha256=_digest(f"{label}-source-measurement-artifact"),
    )
    _SYNTHETIC_SOURCE_MEASUREMENTS[identity.final_artifact_sha256] = measurement
    return identity, source_profile, resolved


def _calibration_material_ports(
    policy: AuthenticatedSemanticCalibrationPolicy,
) -> tuple[
    ShortMaterialCalibrationObservation,
    _SyntheticFinalMaterialResolver,
    _SyntheticWordCounter,
]:
    identity, source_profile, resolved = _material_fixture(
        label="synthetic-calibration-case",
        word_count=500,
        corpus_payload_sha256=policy.request.corpus_payload_sha256,
        calibration_policy_payload_sha256=(
            canonical_semantic_calibration_artifact_sha256(policy)
        ),
        calibrated_scope=policy.request,
    )
    observation = ShortMaterialCalibrationObservation(
        material_identity=identity,
        source_profile=source_profile,
        material_word_count=500,
        terminally_covered_source_clause_count=source_profile.source_clause_count,
        terminally_covered_proposition_count=source_profile.proposition_count,
        terminally_covered_numeric_quantity_count=(
            source_profile.numeric_quantity_count
        ),
        terminally_covered_safety_critical_clause_count=(
            source_profile.safety_critical_clause_count
        ),
        source_measurement_identity_sha256=(
            canonical_source_material_measurement_sha256(
                _SYNTHETIC_SOURCE_MEASUREMENTS[identity.final_artifact_sha256]
            )
        ),
        final_material_resolver_identity_sha256=_FINAL_MATERIAL_RESOLVER_SHA256,
        word_counting_policy_sha256=_WORD_COUNTING_POLICY_SHA256,
    )
    return (
        observation,
        _SyntheticFinalMaterialResolver(((identity, resolved),)),
        _SyntheticWordCounter(),
    )


def _passing_evidence_request(
    policy_evidence: AuthenticatedSemanticCalibrationPolicy,
) -> SemanticCalibrationEvidenceRequest:
    policy = policy_evidence.request
    observation, _, _ = _calibration_material_ports(policy_evidence)
    calibration_lineages = tuple(
        case
        for case in policy.split_manifest.cases
        if case.partition is CalibrationPartition.CALIBRATION
    )
    request = SemanticCalibrationEvidenceRequest(
        schema_version=1,
        evidence_id="synthetic-calibration-evidence",
        run_id="synthetic-calibration-run",
        corpus_payload_sha256=policy.corpus_payload_sha256,
        calibration_policy_payload_sha256=(
            canonical_semantic_calibration_artifact_sha256(policy_evidence)
        ),
        split_manifest_sha256=canonical_calibration_split_sha256(policy.split_manifest),
        producer_identity_sha256s=policy.producer_identity_sha256s,
        producer_model_identity_sha256s=policy.producer_model_identity_sha256s,
        evaluator_identity_sha256=policy.evaluator_identity_sha256,
        evaluator_model_identity_sha256=policy.evaluator_model_identity_sha256,
        runtime_profile_sha256=policy.runtime_profile_sha256,
        tokenizer_identity_sha256=policy.tokenizer_identity_sha256,
        task_identity_sha256=policy.task_identity_sha256,
        short_material_rule=policy.short_material_rule,
        case_outcomes=(
            CalibrationCaseOutcome(
                case_id="case-2",
                reference_case_sha256=calibration_lineages[0].reference_case_sha256,
                outcome_sha256=_digest("synthetic-case-2-outcome"),
                passed_dimensions=_dimensions(),
                failed_dimensions=(),
                exclusion_reason_sha256=None,
                short_material_observation=observation,
            ),
        ),
        dimension_metrics=_passing_metrics(),
        deterministic_gates=CalibrationDeterministicGateCounts(
            total_source_clause_count=10,
            terminal_source_clause_disposition_count=10,
            safety_critical_source_clause_count=2,
            recalled_safety_critical_clause_count=2,
            accepted_semantic_reversal_count=0,
            accepted_unsupported_safety_critical_claim_count=0,
            accepted_unauthenticated_numeric_mutation_count=0,
        ),
        sample_count=1,
        exclusion_count=0,
        failure_count=0,
        run_started_at_epoch_seconds=200,
        run_completed_at_epoch_seconds=300,
        evaluation_artifact_sha256=_digest("synthetic-evaluation-artifact"),
        authority_sequence=2,
        issued_at_epoch_seconds=300,
        expires_at_epoch_seconds=800,
    )
    _register_synthetic_evaluation(request, policy_evidence)
    return request


def _register_synthetic_evaluation(
    request: SemanticCalibrationEvidenceRequest,
    policy_evidence: AuthenticatedSemanticCalibrationPolicy,
) -> None:
    calibration_lineages = tuple(
        case
        for case in policy_evidence.request.split_manifest.cases
        if case.partition is CalibrationPartition.CALIBRATION
    )
    scope = IndependentCalibrationEvaluationScope(
        calibration_policy_payload_sha256=request.calibration_policy_payload_sha256,
        corpus_payload_sha256=request.corpus_payload_sha256,
        split_manifest_sha256=request.split_manifest_sha256,
        run_id=request.run_id,
        producer_identity_sha256s=request.producer_identity_sha256s,
        producer_model_identity_sha256s=request.producer_model_identity_sha256s,
        evaluator_identity_sha256=request.evaluator_identity_sha256,
        evaluator_model_identity_sha256=request.evaluator_model_identity_sha256,
        runtime_profile_sha256=request.runtime_profile_sha256,
        tokenizer_identity_sha256=request.tokenizer_identity_sha256,
        task_identity_sha256=request.task_identity_sha256,
        reference_cases=tuple(
            CalibrationReferenceCaseBinding(
                case_id=case.case_id,
                reference_case_sha256=case.reference_case_sha256,
            )
            for case in calibration_lineages
        ),
    )
    _SYNTHETIC_EVALUATIONS[str(request.run_id)] = (
        IndependentCalibrationEvaluationEvidence(
            scope=scope,
            case_outcomes=request.case_outcomes,
            dimension_metrics=request.dimension_metrics,
            deterministic_gates=request.deterministic_gates,
            sample_count=request.sample_count,
            exclusion_count=request.exclusion_count,
            failure_count=request.failure_count,
            run_started_at_epoch_seconds=request.run_started_at_epoch_seconds,
            run_completed_at_epoch_seconds=request.run_completed_at_epoch_seconds,
            evaluation_artifact_sha256=request.evaluation_artifact_sha256,
        )
    )


def _issue_evidence(
    authority: SemanticCalibrationHmacAuthority,
    request: SemanticCalibrationEvidenceRequest,
    *,
    policy: VerifiedSemanticCalibrationPolicy,
) -> VerifiedSemanticCalibrationEvidence:
    _, resolver, counter = _calibration_material_ports(policy.evidence)
    return authority.issue_evidence(
        request,
        policy=policy,
        final_material_resolver=resolver,
        word_counter=counter,
    )


def _issued_calibration() -> tuple[
    SemanticCalibrationHmacAuthority,
    _CurrentCalibrationState,
    VerifiedVeterinaryExpertCorpus,
    VeterinaryCorpusPromotionHmacAuthority,
    VerifiedSemanticCalibrationPolicy,
    VerifiedSemanticCalibrationEvidence,
]:
    corpus, corpus_authority = _promoted_corpus()
    corpus_evidence = corpus_authority.require_current(corpus)
    from lecture_study.quality.expert_corpus import (
        canonical_expert_corpus_evidence_sha256,
    )

    corpus_sha256 = canonical_expert_corpus_evidence_sha256(corpus_evidence)
    state = _CurrentCalibrationState()
    authority = _calibration_authority(state)
    policy = authority.issue_policy(
        _policy_request(corpus_sha256),
        corpus=corpus,
        corpus_authority=corpus_authority,
    )
    evidence = _issue_evidence(
        authority,
        _passing_evidence_request(policy.evidence),
        policy=policy,
    )
    return authority, state, corpus, corpus_authority, policy, evidence


def _short_material_request(
    calibration_evidence: AuthenticatedSemanticCalibrationEvidence,
    *,
    material_word_count: int = 500,
    covered_clause_count: int = 10,
) -> tuple[
    ShortMaterialThresholdPolicyRequest,
    _SyntheticFinalMaterialResolver,
    _SyntheticWordCounter,
]:
    request = calibration_evidence.request
    identity, source_profile, resolved = _material_fixture(
        label="synthetic-final",
        word_count=material_word_count,
        corpus_payload_sha256=request.corpus_payload_sha256,
        calibration_policy_payload_sha256=(request.calibration_policy_payload_sha256),
        calibrated_scope=request,
    )
    measurement = _SYNTHETIC_SOURCE_MEASUREMENTS[identity.final_artifact_sha256]
    if covered_clause_count != source_profile.source_clause_count:
        measurement = measurement.model_copy(
            update={
                "terminally_covered_source_clause_count": covered_clause_count,
            }
        )
        _SYNTHETIC_SOURCE_MEASUREMENTS[identity.final_artifact_sha256] = measurement
    policy_request = ShortMaterialThresholdPolicyRequest(
        schema_version=1,
        policy_id="synthetic-short-material-policy",
        policy_version="synthetic-v1",
        material_identity=identity,
        source_profile=source_profile,
        final_material_resolver_identity_sha256=_FINAL_MATERIAL_RESOLVER_SHA256,
        word_counting_policy_sha256=_WORD_COUNTING_POLICY_SHA256,
        terminally_covered_source_clause_count=covered_clause_count,
        terminally_covered_proposition_count=source_profile.proposition_count,
        terminally_covered_numeric_quantity_count=source_profile.numeric_quantity_count,
        terminally_covered_safety_critical_clause_count=(
            source_profile.safety_critical_clause_count
        ),
        source_measurement_identity_sha256=(
            canonical_source_material_measurement_sha256(measurement)
        ),
        corpus_payload_sha256=request.corpus_payload_sha256,
        calibration_evidence_payload_sha256=(
            canonical_semantic_calibration_artifact_sha256(calibration_evidence)
        ),
        calibration_policy_payload_sha256=(request.calibration_policy_payload_sha256),
        authority_sequence=3,
        issued_at_epoch_seconds=400,
        expires_at_epoch_seconds=700,
    )
    return (
        policy_request,
        _SyntheticFinalMaterialResolver(((identity, resolved),)),
        _SyntheticWordCounter(),
    )


def test_calibration_and_short_material_capabilities_never_authorize_publication() -> (
    None
):
    authority, _, corpus, corpus_authority, policy, calibration = _issued_calibration()
    request, resolver, counter = _short_material_request(calibration.evidence)
    short_policy = authority.issue_short_material_policy(
        request,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )

    assert policy.publication_ready is False
    assert calibration.publication_ready is False
    assert short_policy.publication_ready is False
    assert policy.production_eligible is False
    assert calibration.production_eligible is False
    assert short_policy.production_eligible is False
    assert short_policy.evidence.production_eligible is False
    assert (
        authority.require_short_material_policy_current(
            short_policy,
            final_material_resolver=resolver,
            word_counter=counter,
        ).request
        == short_policy.evidence.request
    )
    assert corpus_authority.require_current(corpus) == corpus.evidence


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("lecture_id", "lecture-0"),
        ("source_artifact_ids", ("artifact-0",)),
        ("source_record_ids", ("record-0",)),
        ("source_content_aggregation_sha256", _digest("aggregation-0")),
        ("near_duplicate_cluster_ids", ("cluster-0",)),
        ("derivative_root_sha256", _digest("derivative-root-0")),
    ),
)
def test_cross_partition_lineage_leakage_is_rejected(
    field: str,
    replacement: object,
) -> None:
    split = _split_manifest()
    leaked_case = split.cases[1].model_copy(update={field: replacement})

    with pytest.raises(ValidationError, match="crosses calibration partitions"):
        CalibrationSplitManifest.model_validate(
            split.model_copy(
                update={"cases": (split.cases[0], leaked_case, *split.cases[2:])}
            ).model_dump(mode="python")
        )


@pytest.mark.parametrize("failed_dimension", _dimensions())
def test_cherry_picking_and_every_blocking_dimension_fail_closed(
    failed_dimension: CalibrationDimension,
) -> None:
    corpus, corpus_authority = _promoted_corpus()
    from lecture_study.quality.expert_corpus import (
        canonical_expert_corpus_evidence_sha256,
    )

    corpus_sha256 = canonical_expert_corpus_evidence_sha256(
        corpus_authority.require_current(corpus)
    )
    state = _CurrentCalibrationState()
    authority = _calibration_authority(state)
    policy = authority.issue_policy(
        _policy_request(corpus_sha256),
        corpus=corpus,
        corpus_authority=corpus_authority,
    )
    request = _passing_evidence_request(policy.evidence)
    held_out = request.case_outcomes[0].model_copy(update={"case_id": "case-3"})
    with pytest.raises(CalibrationPromotionError) as cherry_picking:
        _issue_evidence(
            authority,
            request.model_copy(update={"case_outcomes": (held_out,)}),
            policy=policy,
        )
    assert cherry_picking.value.disposition is CalibrationDisposition.QUARANTINE

    failed_metric = next(
        metric
        for metric in request.dimension_metrics
        if metric.dimension is failed_dimension
    ).model_copy(
        update={
            "numerator": 0,
            "point_estimate": Decimal("0"),
            "uncertainty_lower": Decimal("0"),
            "uncertainty_upper": Decimal("0"),
            "failure_count": 1,
        }
    )
    failed_metrics = tuple(
        failed_metric if metric.dimension is failed_dimension else metric
        for metric in request.dimension_metrics
    )
    failed_outcome = request.case_outcomes[0].model_copy(
        update={
            "passed_dimensions": tuple(
                dimension
                for dimension in _dimensions()
                if dimension is not failed_dimension
            ),
            "failed_dimensions": (failed_dimension,),
        }
    )
    failed_request = request.model_copy(
        update={
            "case_outcomes": (failed_outcome,),
            "dimension_metrics": failed_metrics,
            "failure_count": 1,
        }
    )
    _register_synthetic_evaluation(failed_request, policy.evidence)
    with pytest.raises(CalibrationPromotionError) as dimension_failure:
        _issue_evidence(
            authority,
            failed_request,
            policy=policy,
        )
    assert dimension_failure.value.reason_code.startswith("failed_dimension_")
    assert dimension_failure.value.disposition is CalibrationDisposition.NEEDS_REVIEW


@pytest.mark.parametrize(
    "field",
    (
        "specialty_id",
        "task_taxonomy_sha256",
        "split_policy_sha256",
        "deduplication_policy_sha256",
    ),
)
def test_partition_policy_identity_replay_is_rejected(field: str) -> None:
    split = _split_manifest()
    replacement = (
        "substituted-specialty"
        if field == "specialty_id"
        else _digest(f"substituted-{field}")
    )
    substituted_case = split.cases[1].model_copy(update={field: replacement})

    with pytest.raises(ValidationError, match="identity is inconsistent"):
        CalibrationSplitManifest.model_validate(
            split.model_copy(
                update={
                    "cases": (
                        split.cases[0],
                        substituted_case,
                        *split.cases[2:],
                    )
                }
            ).model_dump(mode="python")
        )


@pytest.mark.parametrize(
    "metric_update",
    (
        {
            "numerator": 0,
            "point_estimate": Decimal("0"),
        },
        {
            "denominator": 2,
            "point_estimate": Decimal("0.5"),
        },
        {
            "point_estimate": Decimal("0"),
        },
    ),
)
def test_declared_metric_cannot_contradict_case_outcomes(
    metric_update: dict[str, object],
) -> None:
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request = _passing_evidence_request(policy.evidence)
    mutated_metric = request.dimension_metrics[0].model_copy(update=metric_update)
    mutated_metrics = (mutated_metric, *request.dimension_metrics[1:])

    with pytest.raises(CalibrationPromotionError) as mismatch:
        _issue_evidence(
            authority,
            request.model_copy(update={"dimension_metrics": mutated_metrics}),
            policy=policy,
        )

    assert mismatch.value.disposition is CalibrationDisposition.QUARANTINE


def test_expert_corpus_substitution_cannot_promote_calibration_policy() -> None:
    corpus, corpus_authority = _promoted_corpus()
    authority = _calibration_authority(_CurrentCalibrationState())
    substituted_request = _policy_request(_digest("substituted-expert-corpus"))

    with pytest.raises(CalibrationPromotionError) as substitution:
        authority.issue_policy(
            substituted_request,
            corpus=corpus,
            corpus_authority=corpus_authority,
        )
    assert substitution.value.disposition is CalibrationDisposition.QUARANTINE
    assert substitution.value.reason_code == "calibration_corpus_substitution"


@pytest.mark.parametrize(
    "gate_update",
    (
        {"terminal_source_clause_disposition_count": 9},
        {"recalled_safety_critical_clause_count": 1},
        {"accepted_semantic_reversal_count": 1},
        {"accepted_unsupported_safety_critical_claim_count": 1},
        {"accepted_unauthenticated_numeric_mutation_count": 1},
    ),
)
def test_fixed_deterministic_gates_cannot_be_weakened(
    gate_update: dict[str, int],
) -> None:
    corpus, corpus_authority = _promoted_corpus()
    from lecture_study.quality.expert_corpus import (
        canonical_expert_corpus_evidence_sha256,
    )

    corpus_sha256 = canonical_expert_corpus_evidence_sha256(
        corpus_authority.require_current(corpus)
    )
    authority = _calibration_authority(_CurrentCalibrationState())
    policy = authority.issue_policy(
        _policy_request(corpus_sha256),
        corpus=corpus,
        corpus_authority=corpus_authority,
    )
    request = _passing_evidence_request(policy.evidence)
    gates = request.deterministic_gates.model_copy(update=gate_update)
    with pytest.raises(CalibrationPromotionError):
        _issue_evidence(
            authority,
            request.model_copy(update={"deterministic_gates": gates}),
            policy=policy,
        )


def test_policy_threshold_and_evaluator_identity_mutation_are_rejected() -> None:
    authority, _, corpus, corpus_authority, policy, _ = _issued_calibration()
    first_threshold = policy.evidence.request.thresholds[0].model_copy(
        update={"threshold": Decimal("0")}
    )
    mutated_request = policy.evidence.request.model_copy(
        update={
            "thresholds": (
                first_threshold,
                *policy.evidence.request.thresholds[1:],
            )
        }
    )
    mutated_evidence = policy.evidence.model_copy(update={"request": mutated_request})
    object.__setattr__(policy, "evidence", mutated_evidence)

    with pytest.raises(CalibrationPromotionError) as threshold_mutation:
        authority.require_policy_current(policy)
    assert threshold_mutation.value.disposition is CalibrationDisposition.QUARANTINE
    assert threshold_mutation.value.reason_code == (
        "substituted_calibration_capability"
    )
    assert corpus_authority.require_current(corpus) == corpus.evidence

    corpus_sha256 = policy.evidence.request.corpus_payload_sha256
    confused_policy = _policy_request(corpus_sha256).model_copy(
        update={
            "evaluator_identity_sha256": _policy_request(
                corpus_sha256
            ).producer_identity_sha256s[0],
        }
    )
    with pytest.raises(ValidationError, match="must be independent"):
        SemanticCalibrationPolicyRequest.model_validate(
            confused_policy.model_dump(mode="python")
        )


def test_short_incomplete_verbose_low_coverage_and_missing_policy_need_review() -> None:
    authority, _, corpus, corpus_authority, _, calibration = _issued_calibration()
    short_request, short_resolver, short_counter = _short_material_request(
        calibration.evidence,
        material_word_count=99,
    )
    with pytest.raises(CalibrationPromotionError) as short:
        authority.issue_short_material_policy(
            short_request,
            calibration=calibration,
            final_material_resolver=short_resolver,
            word_counter=short_counter,
        )
    assert short.value.disposition is CalibrationDisposition.NEEDS_REVIEW

    verbose_request, verbose_resolver, verbose_counter = _short_material_request(
        calibration.evidence,
        material_word_count=5_000,
        covered_clause_count=9,
    )
    with pytest.raises(CalibrationPromotionError) as verbose_but_incomplete:
        authority.issue_short_material_policy(
            verbose_request,
            calibration=calibration,
            final_material_resolver=verbose_resolver,
            word_counter=verbose_counter,
        )
    assert verbose_but_incomplete.value.reason_code == (
        "incomplete_short_material_semantic_coverage"
    )

    assessor = ShortMaterialPolicyAssessor()
    request, _, _ = _short_material_request(calibration.evidence)
    missing = assessor.assess(
        None,
        authority=authority,
        material_identity=request.material_identity,
        source_profile=request.source_profile,
        final_material_resolver=None,
        word_counter=None,
    )
    assert missing.disposition is CalibrationDisposition.NEEDS_REVIEW
    assert missing.independent_work_may_continue is True
    assert missing.publication_ready is False
    assert corpus_authority.require_current(corpus) == corpus.evidence


def test_short_material_requires_current_resolver_and_counting_policy() -> None:
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request, resolver, _ = _short_material_request(calibration.evidence)

    with pytest.raises(CalibrationPromotionError) as missing_resolver:
        authority.issue_short_material_policy(
            request,
            calibration=calibration,
            final_material_resolver=None,
            word_counter=_SyntheticWordCounter(),
        )
    assert missing_resolver.value.disposition is CalibrationDisposition.NEEDS_REVIEW

    with pytest.raises(CalibrationPromotionError) as missing_counter:
        authority.issue_short_material_policy(
            request,
            calibration=calibration,
            final_material_resolver=resolver,
            word_counter=None,
        )
    assert missing_counter.value.disposition is CalibrationDisposition.NEEDS_REVIEW

    with pytest.raises(CalibrationPromotionError) as substituted_counter:
        authority.issue_short_material_policy(
            request,
            calibration=calibration,
            final_material_resolver=resolver,
            word_counter=_SyntheticWordCounter(
                policy_payload_sha256=_digest("substituted-counting-policy")
            ),
        )
    assert substituted_counter.value.disposition is CalibrationDisposition.QUARANTINE


def test_short_material_distribution_is_derived_from_calibration_evidence() -> None:
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request, resolver, counter = _short_material_request(calibration.evidence)

    short_policy = authority.issue_short_material_policy(
        request,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )
    evidence = authority.require_short_material_policy_current(
        short_policy,
        final_material_resolver=resolver,
        word_counter=counter,
    )

    assert evidence.observed_material_word_count == 500
    assert evidence.minimum_acceptable_word_count == 500
    assert evidence.density_distribution.sample_count == 1
    assert evidence.density_distribution.lower_observed_value == Decimal("500")
    assert evidence.density_distribution.source_scale_formula_sha256 == _digest(
        "lecture-study:short-material:calibration-reference-feature-ratios:maximum:v2"
    )
    assert evidence.density_distribution.distribution_artifact_sha256 != (
        calibration.evidence.request.evaluation_artifact_sha256
    )


def test_calibration_observation_count_and_content_substitution_are_quarantined() -> (
    None
):
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request = _passing_evidence_request(policy.evidence)
    observation = request.case_outcomes[0].short_material_observation
    assert observation is not None
    substituted_observation = observation.model_copy(
        update={"material_word_count": observation.material_word_count + 1}
    )
    substituted_outcome = request.case_outcomes[0].model_copy(
        update={"short_material_observation": substituted_observation}
    )
    _, resolver, counter = _calibration_material_ports(policy.evidence)

    with pytest.raises(CalibrationPromotionError) as count_substitution:
        authority.issue_evidence(
            request.model_copy(update={"case_outcomes": (substituted_outcome,)}),
            policy=policy,
            final_material_resolver=resolver,
            word_counter=counter,
        )
    assert count_substitution.value.disposition is CalibrationDisposition.QUARANTINE

    wrong_bytes = ResolvedFinalMaterialContent(
        artifact_bytes=b"substituted material",
        canonical_text_utf8=b"substituted material",
    )
    substituted_resolver = _SyntheticFinalMaterialResolver(
        ((observation.material_identity, wrong_bytes),)
    )
    with pytest.raises(CalibrationPromotionError) as content_substitution:
        authority.issue_evidence(
            request,
            policy=policy,
            final_material_resolver=substituted_resolver,
            word_counter=counter,
        )
    assert content_substitution.value.disposition is CalibrationDisposition.QUARANTINE


@pytest.mark.parametrize(
    "mutation",
    (
        "specialty",
        "material-type",
        "full-material-identity",
    ),
)
def test_density_observation_scope_substitution_is_quarantined(
    mutation: str,
) -> None:
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request = _passing_evidence_request(policy.evidence)
    observation = request.case_outcomes[0].short_material_observation
    assert observation is not None
    if mutation == "specialty":
        source_profile = observation.source_profile.model_copy(
            update={"specialty_id": "substituted-specialty"}
        )
        substituted = observation.model_copy(update={"source_profile": source_profile})
    elif mutation == "material-type":
        source_profile = observation.source_profile.model_copy(
            update={"material_type": "substituted-material-type"}
        )
        substituted = observation.model_copy(update={"source_profile": source_profile})
    else:
        material_identity = observation.material_identity.model_copy(
            update={"runtime_profile_sha256": _digest("substituted-runtime-profile")}
        )
        substituted = observation.model_copy(
            update={"material_identity": material_identity}
        )
    substituted_outcome = request.case_outcomes[0].model_copy(
        update={"short_material_observation": substituted}
    )
    _, resolver, counter = _calibration_material_ports(policy.evidence)

    with pytest.raises(CalibrationPromotionError) as scope_substitution:
        authority.issue_evidence(
            request.model_copy(update={"case_outcomes": (substituted_outcome,)}),
            policy=policy,
            final_material_resolver=resolver,
            word_counter=counter,
        )

    assert scope_substitution.value.disposition is CalibrationDisposition.QUARANTINE


def test_calibration_evidence_requires_authenticated_material_ports() -> None:
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request = _passing_evidence_request(policy.evidence)
    _, resolver, counter = _calibration_material_ports(policy.evidence)

    with pytest.raises(CalibrationPromotionError) as missing_resolver:
        authority.issue_evidence(
            request,
            policy=policy,
            final_material_resolver=None,
            word_counter=counter,
        )
    assert missing_resolver.value.disposition is CalibrationDisposition.NEEDS_REVIEW

    with pytest.raises(CalibrationPromotionError) as missing_counter:
        authority.issue_evidence(
            request,
            policy=policy,
            final_material_resolver=resolver,
            word_counter=None,
        )
    assert missing_counter.value.disposition is CalibrationDisposition.NEEDS_REVIEW


def test_calibration_evidence_revalidates_material_ports_before_consumption() -> None:
    corpus, corpus_authority = _promoted_corpus()
    from lecture_study.quality.expert_corpus import (
        canonical_expert_corpus_evidence_sha256,
    )

    corpus_sha256 = canonical_expert_corpus_evidence_sha256(
        corpus_authority.require_current(corpus)
    )
    authority = _calibration_authority(_CurrentCalibrationState())
    policy = authority.issue_policy(
        _policy_request(corpus_sha256),
        corpus=corpus,
        corpus_authority=corpus_authority,
    )
    request = _passing_evidence_request(policy.evidence)
    _, resolver, counter = _calibration_material_ports(policy.evidence)
    calibration = authority.issue_evidence(
        request,
        policy=policy,
        final_material_resolver=resolver,
        word_counter=counter,
    )
    counter.available = False

    with pytest.raises(CalibrationPromotionError) as unavailable:
        authority.require_evidence_current(calibration)

    assert unavailable.value.disposition is CalibrationDisposition.NEEDS_REVIEW


@pytest.mark.parametrize(
    "field",
    (
        "clause_inventory_sha256",
        "atomic_ledger_sha256",
        "reconciliation_sha256",
        "source_content_aggregation_sha256",
        "runtime_profile_sha256",
        "producer_identity_sha256s",
        "producer_model_identity_sha256s",
        "evaluator_identity_sha256",
        "evaluator_model_identity_sha256",
        "tokenizer_identity_sha256",
        "expert_corpus_payload_sha256",
        "calibration_policy_payload_sha256",
    ),
)
def test_final_material_identity_replay_is_rejected(field: str) -> None:
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request, resolver, counter = _short_material_request(calibration.evidence)
    replacement: object = (
        tuple(
            sorted(
                (
                    _digest("substituted-producer-a"),
                    _digest("substituted-producer-b"),
                )
            )
        )
        if field
        in {
            "producer_identity_sha256s",
            "producer_model_identity_sha256s",
        }
        else _digest(f"substituted-{field}")
    )
    substituted_identity = request.material_identity.model_copy(
        update={field: replacement}
    )
    substituted_request = request.model_copy(
        update={"material_identity": substituted_identity}
    )

    with pytest.raises(CalibrationPromotionError) as substitution:
        authority.issue_short_material_policy(
            substituted_request,
            calibration=calibration,
            final_material_resolver=resolver,
            word_counter=counter,
        )
    assert substitution.value.disposition is CalibrationDisposition.QUARANTINE
    if field in {
        "runtime_profile_sha256",
        "producer_identity_sha256s",
        "producer_model_identity_sha256s",
        "evaluator_identity_sha256",
        "evaluator_model_identity_sha256",
        "tokenizer_identity_sha256",
    }:
        assert substitution.value.reason_code == (
            "final_material_calibrated_actor_scope_substitution"
        )


def test_capabilities_can_be_revalidated_after_authority_restart() -> None:
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request, resolver, counter = _short_material_request(calibration.evidence)
    short_policy = authority.issue_short_material_policy(
        request,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )
    policy_evidence = policy.evidence
    calibration_evidence = calibration.evidence
    short_evidence = short_policy.evidence

    restarted = _calibration_authority(state)
    restored_policy = restarted.verify_policy_evidence(
        policy_evidence,
        corpus=corpus,
        corpus_authority=corpus_authority,
    )
    _, calibration_resolver, calibration_counter = _calibration_material_ports(
        policy_evidence
    )
    restored_calibration = restarted.verify_calibration_evidence(
        calibration_evidence,
        policy=restored_policy,
        final_material_resolver=calibration_resolver,
        word_counter=calibration_counter,
    )
    restored_short_policy = restarted.verify_short_material_evidence(
        short_evidence,
        calibration=restored_calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )

    assert (
        restarted.require_short_material_policy_current(
            restored_short_policy,
            final_material_resolver=resolver,
            word_counter=counter,
        )
        == short_evidence
    )


def test_descendant_capability_survives_ancestor_capability_collection() -> None:
    (
        authority,
        state,
        corpus,
        corpus_authority,
        policy,
        calibration,
    ) = _issued_calibration()
    request, resolver, counter = _short_material_request(calibration.evidence)
    short_policy = authority.issue_short_material_policy(
        request,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )

    del corpus
    del policy
    del calibration
    gc.collect()

    assert (
        authority.require_short_material_policy_current(
            short_policy,
            final_material_resolver=resolver,
            word_counter=counter,
        )
        == short_policy.evidence
    )


def test_substitution_revocation_unavailability_and_trust_confusion_fail_closed() -> (
    None
):
    authority, state, corpus, corpus_authority, _, calibration = _issued_calibration()
    short_request, resolver, counter = _short_material_request(calibration.evidence)
    short_policy = authority.issue_short_material_policy(
        short_request,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )
    request = short_policy.evidence.request
    substituted_material = request.material_identity.model_copy(
        update={"final_artifact_sha256": _digest("substituted-material")}
    )
    assessment = ShortMaterialPolicyAssessor().assess(
        short_policy,
        authority=authority,
        material_identity=substituted_material,
        source_profile=request.source_profile,
        final_material_resolver=resolver,
        word_counter=counter,
    )
    assert assessment.disposition is CalibrationDisposition.QUARANTINE

    short_identity = (
        CalibrationArtifactKind.SHORT_MATERIAL_POLICY,
        canonical_semantic_calibration_artifact_sha256(short_policy.evidence),
    )
    state.revoked.add(short_identity)
    with pytest.raises(CalibrationPromotionError) as revoked:
        authority.require_short_material_policy_current(
            short_policy,
            final_material_resolver=resolver,
            word_counter=counter,
        )
    assert revoked.value.disposition is CalibrationDisposition.QUARANTINE

    foreign = _calibration_authority(
        state,
        authority_id="foreign-calibration-authority",
        key_label="foreign",
    )
    with pytest.raises(CalibrationPromotionError) as confused:
        foreign.require_evidence_current(calibration)
    assert confused.value.reason_code == "foreign_calibration_capability"

    state.available = False
    with pytest.raises(CalibrationPromotionError) as unavailable:
        authority.require_evidence_current(calibration)
    assert unavailable.value.disposition is CalibrationDisposition.NEEDS_REVIEW
    assert unavailable.value.independent_work_may_continue is True
    assert unavailable.value.publication_ready is False
    assert corpus_authority.require_current(corpus) == corpus.evidence


def test_corpus_revocation_invalidates_dependent_calibration_capability() -> None:
    corpus_state = _CurrentCorpusState()
    corpus_authority = _corpus_authority(corpus_state)
    corpus = corpus_authority.issue(_promotion_request())
    from lecture_study.quality.expert_corpus import (
        canonical_expert_corpus_evidence_sha256,
    )

    corpus_sha256 = canonical_expert_corpus_evidence_sha256(corpus.evidence)
    authority = _calibration_authority(_CurrentCalibrationState())
    policy = authority.issue_policy(
        _policy_request(corpus_sha256),
        corpus=corpus,
        corpus_authority=corpus_authority,
    )
    calibration = _issue_evidence(
        authority,
        _passing_evidence_request(policy.evidence),
        policy=policy,
    )
    corpus_state.revoked.add(corpus_sha256)

    with pytest.raises(ExpertCorpusPromotionError):
        authority.require_evidence_current(calibration)


@pytest.mark.parametrize(
    "field",
    (
        "sample_size_method_sha256",
        "uncertainty_method_sha256",
        "interval_kind",
        "interval_level",
        "exclusion_policy_sha256",
        "stopping_rule_sha256",
        "failure_policy_sha256",
    ),
)
def test_every_statistical_governance_field_is_authenticated(field: str) -> None:
    corpus, corpus_authority = _promoted_corpus()
    from lecture_study.quality.expert_corpus import (
        canonical_expert_corpus_evidence_sha256,
    )

    corpus_sha256 = canonical_expert_corpus_evidence_sha256(
        corpus_authority.require_current(corpus)
    )
    authority = _calibration_authority(_CurrentCalibrationState())
    policy = authority.issue_policy(
        _policy_request(corpus_sha256),
        corpus=corpus,
        corpus_authority=corpus_authority,
    )
    replacements: dict[str, object] = {
        "sample_size_method_sha256": _digest("substituted-sample-method"),
        "uncertainty_method_sha256": _digest("substituted-uncertainty-method"),
        "interval_kind": CalibrationIntervalKind.CREDIBLE,
        "interval_level": Decimal("0.90"),
        "exclusion_policy_sha256": _digest("substituted-exclusion-policy"),
        "stopping_rule_sha256": _digest("substituted-stopping-rule"),
        "failure_policy_sha256": _digest("substituted-failure-policy"),
    }
    statistical = policy.evidence.request.statistical_policy.model_copy(
        update={field: replacements[field]}
    )
    request = policy.evidence.request.model_copy(
        update={"statistical_policy": statistical}
    )
    object.__setattr__(
        policy,
        "evidence",
        policy.evidence.model_copy(update={"request": request}),
    )

    with pytest.raises(CalibrationPromotionError) as substituted:
        authority.require_policy_current(policy)

    assert substituted.value.disposition is CalibrationDisposition.QUARANTINE


@pytest.mark.parametrize(
    "field",
    (
        "corpus_id",
        "corpus_version",
        "corpus_manifest_sha256",
    ),
)
def test_split_identity_is_bound_to_promoted_corpus(field: str) -> None:
    corpus, corpus_authority = _promoted_corpus()
    from lecture_study.quality.expert_corpus import (
        canonical_expert_corpus_evidence_sha256,
    )

    corpus_sha256 = canonical_expert_corpus_evidence_sha256(corpus.evidence)
    split = _split_manifest().model_copy(
        update={
            field: (
                _digest(f"substituted-{field}")
                if field == "corpus_manifest_sha256"
                else f"substituted-{field}"
            )
        }
    )
    request = _policy_request(corpus_sha256).model_copy(
        update={"split_manifest": split}
    )

    with pytest.raises(CalibrationPromotionError) as substituted:
        _calibration_authority(_CurrentCalibrationState()).issue_policy(
            request,
            corpus=corpus,
            corpus_authority=corpus_authority,
        )

    assert substituted.value.disposition is CalibrationDisposition.QUARANTINE


def test_split_lineage_requires_issuer_bound_external_evidence() -> None:
    corpus, corpus_authority = _promoted_corpus()
    from lecture_study.quality.expert_corpus import (
        canonical_expert_corpus_evidence_sha256,
    )

    corpus_sha256 = canonical_expert_corpus_evidence_sha256(corpus.evidence)
    authority = _calibration_authority(_CurrentCalibrationState())
    assert isinstance(authority._split_evidence, _SyntheticSplitEvidence)
    authority._split_evidence.available = False

    with pytest.raises(CalibrationPromotionError) as unavailable:
        authority.issue_policy(
            _policy_request(corpus_sha256),
            corpus=corpus,
            corpus_authority=corpus_authority,
        )

    assert unavailable.value.disposition is CalibrationDisposition.NEEDS_REVIEW
    with pytest.raises(TypeError, match="issuer-only"):
        VerifiedCalibrationSplitEvidence()


def test_caller_outcomes_cannot_replace_independent_evaluator_evidence() -> None:
    authority, _, _, _, policy, _ = _issued_calibration()
    request = _passing_evidence_request(policy.evidence)
    substituted_outcome = request.case_outcomes[0].model_copy(
        update={"outcome_sha256": _digest("caller-selected-outcome")}
    )

    with pytest.raises(CalibrationPromotionError) as substituted:
        _issue_evidence(
            authority,
            request.model_copy(update={"case_outcomes": (substituted_outcome,)}),
            policy=policy,
        )

    assert substituted.value.reason_code == "caller_calibration_assertion_substitution"
    with pytest.raises(TypeError, match="issuer-only"):
        VerifiedIndependentCalibrationEvaluation()


@pytest.mark.parametrize(
    "field",
    (
        "producer_identity_sha256s",
        "evaluator_identity_sha256",
        "runtime_profile_sha256",
        "tokenizer_identity_sha256",
        "reference_case_sha256",
    ),
)
def test_independent_evaluation_replay_bindings_fail_closed(field: str) -> None:
    authority, _, _, _, policy, _ = _issued_calibration()
    request = _passing_evidence_request(policy.evidence)
    if field == "reference_case_sha256":
        outcome = request.case_outcomes[0].model_copy(
            update={"reference_case_sha256": _digest("substituted-reference-case")}
        )
        mutated = request.model_copy(update={"case_outcomes": (outcome,)})
    else:
        replacement: object = (
            tuple(
                sorted(
                    (
                        _digest("substituted-producer-a"),
                        _digest("substituted-producer-b"),
                    )
                )
            )
            if field == "producer_identity_sha256s"
            else _digest(f"substituted-{field}")
        )
        mutated = request.model_copy(update={field: replacement})

    with pytest.raises(CalibrationPromotionError) as replay:
        _issue_evidence(authority, mutated, policy=policy)

    assert replay.value.disposition in {
        CalibrationDisposition.NEEDS_REVIEW,
        CalibrationDisposition.QUARANTINE,
    }


def test_independent_evaluator_unavailability_is_scope_local() -> None:
    authority, _, _, _, policy, _ = _issued_calibration()
    request = _passing_evidence_request(policy.evidence)
    assert isinstance(
        authority._independent_evaluator,
        _SyntheticIndependentEvaluator,
    )
    authority._independent_evaluator.available = False

    with pytest.raises(CalibrationPromotionError) as unavailable:
        _issue_evidence(authority, request, policy=policy)

    assert unavailable.value.disposition is CalibrationDisposition.NEEDS_REVIEW
    assert unavailable.value.independent_work_may_continue is True


def _with_authenticated_coverage(
    request: ShortMaterialThresholdPolicyRequest,
    **updates: int,
) -> ShortMaterialThresholdPolicyRequest:
    key = request.material_identity.final_artifact_sha256
    measurement = _SYNTHETIC_SOURCE_MEASUREMENTS[key].model_copy(update=updates)
    _SYNTHETIC_SOURCE_MEASUREMENTS[key] = measurement
    return request.model_copy(
        update={
            **updates,
            "source_measurement_identity_sha256": (
                canonical_source_material_measurement_sha256(measurement)
            ),
        }
    )


@pytest.mark.parametrize(
    "field",
    (
        "terminally_covered_source_clause_count",
        "terminally_covered_proposition_count",
        "terminally_covered_numeric_quantity_count",
        "terminally_covered_safety_critical_clause_count",
    ),
)
def test_every_authenticated_coverage_inventory_is_individually_blocking(
    field: str,
) -> None:
    authority, _, _, _, _, calibration = _issued_calibration()
    request, resolver, counter = _short_material_request(
        calibration.evidence,
        material_word_count=5_000,
    )
    totals = {
        "terminally_covered_source_clause_count": (
            request.source_profile.source_clause_count
        ),
        "terminally_covered_proposition_count": request.source_profile.proposition_count,
        "terminally_covered_numeric_quantity_count": (
            request.source_profile.numeric_quantity_count
        ),
        "terminally_covered_safety_critical_clause_count": (
            request.source_profile.safety_critical_clause_count
        ),
    }
    incomplete = _with_authenticated_coverage(
        request,
        **{field: max(0, totals[field] - 1)},
    )

    with pytest.raises(CalibrationPromotionError) as blocked:
        authority.issue_short_material_policy(
            incomplete,
            calibration=calibration,
            final_material_resolver=resolver,
            word_counter=counter,
        )

    assert blocked.value.reason_code == "incomplete_short_material_semantic_coverage"
    assert blocked.value.disposition is CalibrationDisposition.NEEDS_REVIEW


def test_source_measurement_is_issuer_only_current_and_fail_closed() -> None:
    authority, _, _, _, _, calibration = _issued_calibration()
    request, resolver, counter = _short_material_request(calibration.evidence)
    assert isinstance(
        authority._source_measurements,
        _SyntheticSourceMeasurements,
    )
    authority._source_measurements.available = False

    with pytest.raises(CalibrationPromotionError) as unavailable:
        authority.issue_short_material_policy(
            request,
            calibration=calibration,
            final_material_resolver=resolver,
            word_counter=counter,
        )

    assert unavailable.value.disposition is CalibrationDisposition.NEEDS_REVIEW
    with pytest.raises(TypeError, match="issuer-only"):
        VerifiedSourceMaterialMeasurement()


def test_one_reduced_feature_cannot_mask_unchanged_source_features() -> None:
    authority, _, _, _, _, calibration = _issued_calibration()
    request, resolver, counter = _short_material_request(calibration.evidence)
    profile = request.source_profile.model_copy(
        update={"source_duration_milliseconds": 3_600_000}
    )
    key = request.material_identity.final_artifact_sha256
    measurement = _SYNTHETIC_SOURCE_MEASUREMENTS[key].model_copy(
        update={"source_profile": profile}
    )
    _SYNTHETIC_SOURCE_MEASUREMENTS[key] = measurement
    substituted_scale = request.model_copy(
        update={
            "source_profile": profile,
            "source_measurement_identity_sha256": (
                canonical_source_material_measurement_sha256(measurement)
            ),
        }
    )

    capability = authority.issue_short_material_policy(
        substituted_scale,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )
    assert capability.evidence.minimum_acceptable_word_count == 500


@pytest.mark.parametrize(
    "field",
    (
        "source_duration_milliseconds",
        "authenticated_transcript_byte_count",
        "authenticated_ocr_byte_count",
        "instructional_primary_visual_span_count",
        "source_clause_count",
        "proposition_count",
        "numeric_quantity_count",
        "safety_critical_clause_count",
    ),
)
def test_every_single_authenticated_scale_feature_changes_threshold_linearly(
    field: str,
) -> None:
    authority, _, _, _, _, calibration = _issued_calibration()
    request, resolver, counter = _short_material_request(
        calibration.evidence,
        material_word_count=1_000,
    )
    original = getattr(request.source_profile, field)
    profile = request.source_profile.model_copy(update={field: original * 2})
    key = request.material_identity.final_artifact_sha256
    coverage_updates: dict[str, int] = {}
    coverage_field_by_scale = {
        "source_clause_count": "terminally_covered_source_clause_count",
        "proposition_count": "terminally_covered_proposition_count",
        "numeric_quantity_count": "terminally_covered_numeric_quantity_count",
        "safety_critical_clause_count": (
            "terminally_covered_safety_critical_clause_count"
        ),
    }
    coverage_field = coverage_field_by_scale.get(field)
    if coverage_field is not None:
        coverage_updates[coverage_field] = getattr(profile, field)
    measurement = _SYNTHETIC_SOURCE_MEASUREMENTS[key].model_copy(
        update={"source_profile": profile, **coverage_updates}
    )
    _SYNTHETIC_SOURCE_MEASUREMENTS[key] = measurement
    scaled_request = request.model_copy(
        update={
            "source_profile": profile,
            **coverage_updates,
            "source_measurement_identity_sha256": (
                canonical_source_material_measurement_sha256(measurement)
            ),
        }
    )

    capability = authority.issue_short_material_policy(
        scaled_request,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )

    assert capability.evidence.minimum_acceptable_word_count == 1_000


@pytest.mark.parametrize(
    ("numerator", "denominator", "material_word_count", "expected_minimum"),
    (
        (1, 2, 250, 250),
        (2, 1, 1_000, 1_000),
    ),
)
def test_combined_proportional_source_scaling_is_linear(
    numerator: int,
    denominator: int,
    material_word_count: int,
    expected_minimum: int,
) -> None:
    authority, _, _, _, _, calibration = _issued_calibration()
    request, resolver, counter = _short_material_request(
        calibration.evidence,
        material_word_count=material_word_count,
    )
    scale_fields = (
        "source_duration_milliseconds",
        "authenticated_transcript_byte_count",
        "authenticated_ocr_byte_count",
        "instructional_primary_visual_span_count",
        "source_clause_count",
        "proposition_count",
        "numeric_quantity_count",
        "safety_critical_clause_count",
    )
    profile = request.source_profile.model_copy(
        update={
            field: getattr(request.source_profile, field) * numerator // denominator
            for field in scale_fields
        }
    )
    coverage_updates = {
        "terminally_covered_source_clause_count": profile.source_clause_count,
        "terminally_covered_proposition_count": profile.proposition_count,
        "terminally_covered_numeric_quantity_count": profile.numeric_quantity_count,
        "terminally_covered_safety_critical_clause_count": (
            profile.safety_critical_clause_count
        ),
    }
    key = request.material_identity.final_artifact_sha256
    measurement = _SYNTHETIC_SOURCE_MEASUREMENTS[key].model_copy(
        update={"source_profile": profile, **coverage_updates}
    )
    _SYNTHETIC_SOURCE_MEASUREMENTS[key] = measurement
    scaled_request = request.model_copy(
        update={
            "source_profile": profile,
            **coverage_updates,
            "source_measurement_identity_sha256": (
                canonical_source_material_measurement_sha256(measurement)
            ),
        }
    )

    capability = authority.issue_short_material_policy(
        scaled_request,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )

    assert capability.evidence.minimum_acceptable_word_count == expected_minimum


def test_two_hour_two_paragraph_material_and_nonproduction_policy_never_verify() -> (
    None
):
    authority, _, _, _, _, calibration = _issued_calibration()
    short_request, short_resolver, short_counter = _short_material_request(
        calibration.evidence,
        material_word_count=20,
    )
    with pytest.raises(CalibrationPromotionError) as underproduction:
        authority.issue_short_material_policy(
            short_request,
            calibration=calibration,
            final_material_resolver=short_resolver,
            word_counter=short_counter,
        )
    assert underproduction.value.reason_code == "short_material_underproduction"

    request, resolver, counter = _short_material_request(calibration.evidence)
    capability = authority.issue_short_material_policy(
        request,
        calibration=calibration,
        final_material_resolver=resolver,
        word_counter=counter,
    )
    assessment = ShortMaterialPolicyAssessor().assess(
        capability,
        authority=authority,
        material_identity=request.material_identity,
        source_profile=request.source_profile,
        final_material_resolver=resolver,
        word_counter=counter,
    )
    assert assessment.disposition is CalibrationDisposition.NEEDS_REVIEW
    assert assessment.reason_code == "nonproduction_short_material_policy"
