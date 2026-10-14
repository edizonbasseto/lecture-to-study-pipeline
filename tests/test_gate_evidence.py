from __future__ import annotations

import hashlib
import json
import math

import pytest
from pydantic import ValidationError

from lecture_study.quality.certification import QualityGateName
from lecture_study.quality.gate_evidence import (
    ArtifactLineageEvidence,
    EvidenceArtifact,
    FindingSeverity,
    GateEvidenceEnvelope,
    LearningUnitCompletenessEvidence,
    PromptBudgetEvidence,
    QualityFindingEvidence,
    SourceMediaIntegrityEvidence,
    TimelineClassificationEvidence,
    UnavailableGateEvidence,
    canonical_evidence_bytes,
    canonical_evidence_sha256,
)

_SOURCE_DIGEST = "a" * 64


def _artifact(
    artifact_id: str,
    relative_path: str,
    *,
    digest: str = "b" * 64,
    byte_size: int = 128,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id=artifact_id,
        relative_path=relative_path,
        sha256=digest,
        byte_size=byte_size,
        media_type="application/json",
    )


def _source_payload() -> SourceMediaIntegrityEvidence:
    return SourceMediaIntegrityEvidence(
        evidence_type="source-media-integrity",
        source_artifact_id="source-media",
        expected_sha256=_SOURCE_DIGEST,
        observed_sha256=_SOURCE_DIGEST,
        expected_byte_count=1_024,
        observed_byte_count=1_024,
        expected_duration_seconds=7_200.0,
        observed_duration_seconds=7_200.0,
        duration_tolerance_seconds=0.5,
        decoded_sample_count=120,
        failed_sample_count=0,
    )


def _envelope(
    *,
    gate_name: QualityGateName = QualityGateName.SOURCE_MEDIA_INTEGRITY,
    payload: (
        SourceMediaIntegrityEvidence
        | PromptBudgetEvidence
        | ArtifactLineageEvidence
        | TimelineClassificationEvidence
        | LearningUnitCompletenessEvidence
        | UnavailableGateEvidence
        | None
    ) = None,
    calibration_artifact: EvidenceArtifact | None = None,
    findings: tuple[QualityFindingEvidence, ...] = (),
    input_artifacts: tuple[EvidenceArtifact, ...] | None = None,
) -> GateEvidenceEnvelope:
    source_artifact = _artifact(
        "source-media",
        "source/lecture.mp4",
        digest=_SOURCE_DIGEST,
        byte_size=1_024,
    )
    return GateEvidenceEnvelope(
        schema_version=1,
        lecture_id="lecture-001",
        gate_name=gate_name,
        source_snapshot_sha256=_SOURCE_DIGEST,
        policy_artifact=_artifact("policy", "quality/policy.json"),
        calibration_artifact=calibration_artifact
        or _artifact("calibration", "quality/calibration.json"),
        evaluator_artifact=_artifact("evaluator", "quality/evaluator.json"),
        input_artifacts=input_artifacts or (source_artifact,),
        payload=payload or _source_payload(),
        findings=findings,
    )


def test_source_media_evidence_has_internally_consistent_declared_metrics() -> None:
    envelope = _envelope()

    assert envelope.declared_metrics_are_internally_consistent is True
    assert not hasattr(envelope, "is_eligible_for_publication")
    assert "passed" not in GateEvidenceEnvelope.model_fields
    assert "calibrated" not in GateEvidenceEnvelope.model_fields


def test_caller_cannot_inject_passed_or_calibrated_flags() -> None:
    data = _envelope().model_dump(mode="python")
    data["passed"] = True
    data["calibrated"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GateEvidenceEnvelope.model_validate(data)


def test_missing_calibration_breaks_declared_metric_contract() -> None:
    data = _envelope().model_dump(mode="python")
    data.pop("calibration_artifact")

    envelope = GateEvidenceEnvelope.model_validate(data)

    assert envelope.declared_metrics_are_internally_consistent is False


def test_blocking_finding_breaks_declared_metric_contract() -> None:
    finding = QualityFindingEvidence(
        finding_id="source-truncated",
        severity=FindingSeverity.BLOCKING,
        code="SOURCE_TRUNCATED",
        message="The observed source ends before the declared duration.",
        artifact_ids=("source-media",),
    )

    envelope = _envelope(findings=(finding,))

    assert envelope.declared_metrics_are_internally_consistent is False


def test_warning_does_not_replace_objective_gate_metrics() -> None:
    warning = QualityFindingEvidence(
        finding_id="duration-near-tolerance",
        severity=FindingSeverity.WARNING,
        code="DURATION_NEAR_TOLERANCE",
        message="The duration difference is close to the configured tolerance.",
        artifact_ids=("source-media",),
    )

    envelope = _envelope(findings=(warning,))

    assert envelope.declared_metrics_are_internally_consistent is True


def test_gate_payload_mismatch_breaks_declared_metric_contract() -> None:
    envelope = _envelope(
        gate_name=QualityGateName.PROMPT_BUDGET,
        payload=_source_payload(),
    )

    assert envelope.declared_metrics_are_internally_consistent is False


def test_unavailable_payload_is_false_for_every_unimplemented_gate() -> None:
    concrete_gates = {
        QualityGateName.SOURCE_MEDIA_INTEGRITY,
        QualityGateName.PROMPT_BUDGET,
        QualityGateName.ARTIFACT_LINEAGE,
        QualityGateName.TIMELINE_CLASSIFICATION,
        QualityGateName.LEARNING_UNIT_COMPLETENESS,
    }

    for gate_name in set(QualityGateName) - concrete_gates:
        envelope = _envelope(
            gate_name=gate_name,
            payload=UnavailableGateEvidence(
                evidence_type="unavailable",
                gate_name=gate_name,
                reason_code="EVALUATOR_NOT_IMPLEMENTED",
                detail="No executable evaluator is available for this gate.",
            ),
        )

        assert envelope.declared_metrics_are_internally_consistent is False


def test_prompt_budget_is_derived_from_token_counts() -> None:
    prompt_artifact = _artifact("prompt", "prompts/semantic.txt")
    payload = PromptBudgetEvidence(
        evidence_type="prompt-budget",
        prompt_artifact_id="prompt",
        input_tokens=7_799,
        output_reserve_tokens=1_024,
        context_window_tokens=4_096,
        safety_margin_tokens=128,
    )

    envelope = _envelope(
        gate_name=QualityGateName.PROMPT_BUDGET,
        payload=payload,
        input_artifacts=(prompt_artifact,),
    )

    assert payload.required_tokens == 8_951
    assert envelope.declared_metrics_are_internally_consistent is False


def test_prompt_budget_metrics_are_consistent_when_all_counts_fit() -> None:
    prompt_artifact = _artifact("prompt", "prompts/semantic.txt")
    payload = PromptBudgetEvidence(
        evidence_type="prompt-budget",
        prompt_artifact_id="prompt",
        input_tokens=2_000,
        output_reserve_tokens=1_024,
        context_window_tokens=4_096,
        safety_margin_tokens=128,
    )

    envelope = _envelope(
        gate_name=QualityGateName.PROMPT_BUDGET,
        payload=payload,
        input_artifacts=(prompt_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is True


def test_timeline_classification_requires_exact_resolved_partition() -> None:
    timeline_artifact = _artifact("timeline", "analysis/timeline/universe.json")
    payload = TimelineClassificationEvidence(
        evidence_type="timeline-classification",
        timeline_artifact_id="timeline",
        expected_duration_seconds=600.0,
        classified_duration_seconds=600.0,
        duration_tolerance_seconds=0.001,
        interval_count=2,
        unresolved_interval_count=0,
        unresolved_source_span_count=0,
        classification_mismatch_count=0,
        gap_duration_seconds=0.0,
        overlap_duration_seconds=0.0,
        outside_media_duration_seconds=0.0,
        unreferenced_source_span_count=0,
        multiply_referenced_source_span_count=0,
        source_span_boundary_violation_count=0,
    )

    envelope = _envelope(
        gate_name=QualityGateName.TIMELINE_CLASSIFICATION,
        payload=payload,
        input_artifacts=(timeline_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is True


def test_timeline_classification_rejects_gap_or_unresolved_interval() -> None:
    timeline_artifact = _artifact("timeline", "analysis/timeline/universe.json")
    payload = TimelineClassificationEvidence(
        evidence_type="timeline-classification",
        timeline_artifact_id="timeline",
        expected_duration_seconds=600.0,
        classified_duration_seconds=590.0,
        duration_tolerance_seconds=0.001,
        interval_count=2,
        unresolved_interval_count=1,
        unresolved_source_span_count=0,
        classification_mismatch_count=0,
        gap_duration_seconds=10.0,
        overlap_duration_seconds=0.0,
        outside_media_duration_seconds=0.0,
        unreferenced_source_span_count=0,
        multiply_referenced_source_span_count=0,
        source_span_boundary_violation_count=0,
    )

    envelope = _envelope(
        gate_name=QualityGateName.TIMELINE_CLASSIFICATION,
        payload=payload,
        input_artifacts=(timeline_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is False


def test_learning_unit_completeness_requires_exact_primary_ownership() -> None:
    universe_artifact = _artifact(
        "source-universe",
        "analysis/learning-units/universe.json",
    )
    payload = LearningUnitCompletenessEvidence(
        evidence_type="learning-unit-completeness",
        source_universe_artifact_id="source-universe",
        expected_unit_count=2,
        observed_unit_count=2,
        instructional_source_span_count=10,
        owned_instructional_source_span_count=10,
        missing_primary_ownership_count=0,
        duplicate_primary_ownership_count=0,
        invalid_primary_reference_count=0,
        out_of_window_primary_span_count=0,
        out_of_window_context_span_count=0,
        unit_timeline_gap_seconds=0.0,
        unit_timeline_overlap_seconds=0.0,
        unit_outside_media_duration_seconds=0.0,
        unjustified_duration_unit_count=0,
    )

    envelope = _envelope(
        gate_name=QualityGateName.LEARNING_UNIT_COMPLETENESS,
        payload=payload,
        input_artifacts=(universe_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is True


def test_learning_unit_completeness_rejects_ownership_anomalies() -> None:
    universe_artifact = _artifact(
        "source-universe",
        "analysis/learning-units/universe.json",
    )
    payload = LearningUnitCompletenessEvidence(
        evidence_type="learning-unit-completeness",
        source_universe_artifact_id="source-universe",
        expected_unit_count=2,
        observed_unit_count=2,
        instructional_source_span_count=10,
        owned_instructional_source_span_count=8,
        missing_primary_ownership_count=1,
        duplicate_primary_ownership_count=1,
        invalid_primary_reference_count=0,
        out_of_window_primary_span_count=0,
        out_of_window_context_span_count=0,
        unit_timeline_gap_seconds=0.0,
        unit_timeline_overlap_seconds=0.0,
        unit_outside_media_duration_seconds=0.0,
        unjustified_duration_unit_count=0,
    )

    envelope = _envelope(
        gate_name=QualityGateName.LEARNING_UNIT_COMPLETENESS,
        payload=payload,
        input_artifacts=(universe_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is False


def test_artifact_lineage_requires_exact_consumed_input_set() -> None:
    source_artifact = _artifact(
        "source-media",
        "source/lecture.mp4",
        digest=_SOURCE_DIGEST,
    )
    output_artifact = _artifact("canonical", "canonical/document.json")
    payload = ArtifactLineageEvidence(
        evidence_type="artifact-lineage",
        declared_input_artifact_ids=("source-media",),
        consumed_input_artifact_ids=("different-source",),
        output_artifacts=(output_artifact,),
    )

    envelope = _envelope(
        gate_name=QualityGateName.ARTIFACT_LINEAGE,
        payload=payload,
        input_artifacts=(source_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is False


def test_artifact_lineage_metrics_are_consistent_for_an_exact_closed_set() -> None:
    source_artifact = _artifact(
        "source-media",
        "source/lecture.mp4",
        digest=_SOURCE_DIGEST,
    )
    payload = ArtifactLineageEvidence(
        evidence_type="artifact-lineage",
        declared_input_artifact_ids=("source-media",),
        consumed_input_artifact_ids=("source-media",),
        output_artifacts=(_artifact("canonical", "canonical/document.json"),),
    )

    envelope = _envelope(
        gate_name=QualityGateName.ARTIFACT_LINEAGE,
        payload=payload,
        input_artifacts=(source_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is True


def test_duplicate_artifacts_are_rejected_and_fail_closed_defensively() -> None:
    source_artifact = _artifact(
        "source-media",
        "source/lecture.mp4",
        digest=_SOURCE_DIGEST,
    )
    data = _envelope().model_dump(mode="python")
    data["input_artifacts"] = (source_artifact, source_artifact)

    with pytest.raises(ValidationError, match="duplicate input artifact"):
        GateEvidenceEnvelope.model_validate(data)

    unchecked = GateEvidenceEnvelope.model_construct(**data)
    assert unchecked.declared_metrics_are_internally_consistent is False


def test_duplicate_findings_are_rejected_and_fail_closed_defensively() -> None:
    warning = QualityFindingEvidence(
        finding_id="same-finding",
        severity=FindingSeverity.WARNING,
        code="REPEATED_FINDING",
        message="The finding was emitted more than once.",
        artifact_ids=("source-media",),
    )
    data = _envelope().model_dump(mode="python")
    data["findings"] = (warning, warning)

    with pytest.raises(ValidationError, match="duplicate finding"):
        GateEvidenceEnvelope.model_validate(data)

    unchecked = _envelope().model_copy(update={"findings": (warning, warning)})
    assert unchecked.declared_metrics_are_internally_consistent is False


def test_path_traversal_is_rejected_and_fails_closed_defensively() -> None:
    with pytest.raises(ValidationError, match="relative_path"):
        _artifact("escape", "../outside.json")

    unsafe_artifact = EvidenceArtifact.model_construct(
        artifact_id="source-media",
        relative_path="../outside.json",
        sha256=_SOURCE_DIGEST,
        byte_size=128,
        media_type="application/json",
    )
    unchecked = _envelope().model_copy(
        update={"input_artifacts": (unsafe_artifact,)},
    )
    assert unchecked.declared_metrics_are_internally_consistent is False


def test_nonfinite_metrics_are_rejected_and_fail_closed_defensively() -> None:
    source_payload_data = _source_payload().model_dump(mode="python")
    source_payload_data["observed_duration_seconds"] = math.inf

    with pytest.raises(ValidationError, match="finite number"):
        SourceMediaIntegrityEvidence.model_validate(source_payload_data)

    unchecked_payload = SourceMediaIntegrityEvidence.model_construct(
        **source_payload_data,
    )
    unchecked = _envelope().model_copy(update={"payload": unchecked_payload})
    assert unchecked.declared_metrics_are_internally_consistent is False


def test_out_of_range_metrics_fail_closed_defensively() -> None:
    source_payload_data = _source_payload().model_dump(mode="python")
    source_payload_data["expected_byte_count"] = 0
    source_payload_data["observed_byte_count"] = 0

    with pytest.raises(ValidationError, match="greater than 0"):
        SourceMediaIntegrityEvidence.model_validate(source_payload_data)

    unchecked_payload = SourceMediaIntegrityEvidence.model_construct(
        **source_payload_data,
    )
    unchecked = _envelope().model_copy(update={"payload": unchecked_payload})
    assert unchecked.declared_metrics_are_internally_consistent is False


def test_invalid_artifact_size_fails_closed_defensively() -> None:
    artifact_data = _artifact(
        "source-media",
        "source/lecture.mp4",
        digest=_SOURCE_DIGEST,
    ).model_dump(mode="python")
    artifact_data["byte_size"] = -1

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        EvidenceArtifact.model_validate(artifact_data)

    unchecked_artifact = EvidenceArtifact.model_construct(**artifact_data)
    unchecked = _envelope().model_copy(
        update={"input_artifacts": (unchecked_artifact,)},
    )
    assert unchecked.declared_metrics_are_internally_consistent is False


def test_source_media_metrics_require_bound_artifact_byte_count() -> None:
    source_artifact = _artifact(
        "source-media",
        "source/lecture.mp4",
        digest=_SOURCE_DIGEST,
        byte_size=128,
    )

    envelope = _envelope(input_artifacts=(source_artifact,))

    assert envelope.declared_metrics_are_internally_consistent is False


def test_declared_tolerance_never_becomes_publication_authority() -> None:
    timeline_artifact = _artifact("timeline", "analysis/timeline/universe.json")
    payload = TimelineClassificationEvidence(
        evidence_type="timeline-classification",
        timeline_artifact_id="timeline",
        expected_duration_seconds=600.0,
        classified_duration_seconds=1.0,
        duration_tolerance_seconds=599.0,
        interval_count=1,
        unresolved_interval_count=0,
        unresolved_source_span_count=0,
        classification_mismatch_count=0,
        gap_duration_seconds=599.0,
        overlap_duration_seconds=0.0,
        outside_media_duration_seconds=0.0,
        unreferenced_source_span_count=0,
        multiply_referenced_source_span_count=0,
        source_span_boundary_violation_count=0,
    )

    envelope = _envelope(
        gate_name=QualityGateName.TIMELINE_CLASSIFICATION,
        payload=payload,
        input_artifacts=(timeline_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is True
    assert not hasattr(envelope, "is_eligible_for_publication")


def test_timeline_declared_metrics_require_duration_algebra() -> None:
    timeline_artifact = _artifact("timeline", "analysis/timeline/universe.json")
    payload = TimelineClassificationEvidence(
        evidence_type="timeline-classification",
        timeline_artifact_id="timeline",
        expected_duration_seconds=100.0,
        classified_duration_seconds=101.0,
        duration_tolerance_seconds=1.0,
        interval_count=1,
        unresolved_interval_count=0,
        unresolved_source_span_count=0,
        classification_mismatch_count=0,
        gap_duration_seconds=1.0,
        overlap_duration_seconds=0.0,
        outside_media_duration_seconds=0.0,
        unreferenced_source_span_count=0,
        multiply_referenced_source_span_count=0,
        source_span_boundary_violation_count=0,
    )

    envelope = _envelope(
        gate_name=QualityGateName.TIMELINE_CLASSIFICATION,
        payload=payload,
        input_artifacts=(timeline_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is False


def test_timeline_declared_metrics_require_positive_internal_coverage() -> None:
    timeline_artifact = _artifact("timeline", "analysis/timeline/universe.json")
    payload = TimelineClassificationEvidence(
        evidence_type="timeline-classification",
        timeline_artifact_id="timeline",
        expected_duration_seconds=1.0,
        classified_duration_seconds=1.0,
        duration_tolerance_seconds=1.0,
        interval_count=1,
        unresolved_interval_count=0,
        unresolved_source_span_count=0,
        classification_mismatch_count=0,
        gap_duration_seconds=1.0,
        overlap_duration_seconds=0.0,
        outside_media_duration_seconds=0.0,
        unreferenced_source_span_count=0,
        multiply_referenced_source_span_count=0,
        source_span_boundary_violation_count=0,
    )

    envelope = _envelope(
        gate_name=QualityGateName.TIMELINE_CLASSIFICATION,
        payload=payload,
        input_artifacts=(timeline_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is False


def test_timeline_declared_metrics_reject_infeasible_overlap() -> None:
    timeline_artifact = _artifact("timeline", "analysis/timeline/universe.json")
    payload = TimelineClassificationEvidence(
        evidence_type="timeline-classification",
        timeline_artifact_id="timeline",
        expected_duration_seconds=1.0,
        classified_duration_seconds=2.0,
        duration_tolerance_seconds=1.0,
        interval_count=1,
        unresolved_interval_count=0,
        unresolved_source_span_count=0,
        classification_mismatch_count=0,
        gap_duration_seconds=0.0,
        overlap_duration_seconds=1.0,
        outside_media_duration_seconds=0.0,
        unreferenced_source_span_count=0,
        multiply_referenced_source_span_count=0,
        source_span_boundary_violation_count=0,
    )

    envelope = _envelope(
        gate_name=QualityGateName.TIMELINE_CLASSIFICATION,
        payload=payload,
        input_artifacts=(timeline_artifact,),
    )

    assert envelope.declared_metrics_are_internally_consistent is False


def test_timeline_evidence_rejects_non_serializable_interval_count() -> None:
    with pytest.raises(ValidationError):
        TimelineClassificationEvidence(
            evidence_type="timeline-classification",
            timeline_artifact_id="timeline",
            expected_duration_seconds=1.0,
            classified_duration_seconds=1.0,
            duration_tolerance_seconds=0.0,
            interval_count=1 << 20_000,
            unresolved_interval_count=0,
            unresolved_source_span_count=0,
            classification_mismatch_count=0,
            gap_duration_seconds=0.0,
            overlap_duration_seconds=0.0,
            outside_media_duration_seconds=0.0,
            unreferenced_source_span_count=0,
            multiply_referenced_source_span_count=0,
            source_span_boundary_violation_count=0,
        )


def test_gate_evidence_schema_version_rejects_boolean_alias() -> None:
    data = _envelope().model_dump(mode="python")
    data["schema_version"] = True

    with pytest.raises(ValidationError, match="schema_version"):
        GateEvidenceEnvelope.model_validate(data)


def test_canonical_bytes_and_hash_are_deterministic() -> None:
    envelope = _envelope()

    first = canonical_evidence_bytes(envelope)
    second = canonical_evidence_bytes(
        GateEvidenceEnvelope.model_validate_json(envelope.model_dump_json())
    )

    assert first == second
    assert json.loads(first) == envelope.model_dump(mode="json")
    assert canonical_evidence_sha256(envelope) == hashlib.sha256(first).hexdigest()
