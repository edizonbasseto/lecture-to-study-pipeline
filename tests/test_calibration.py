"""Deterministic adversarial tests for non-authoritative calibration evidence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lecture_study.domain.calibration import (
    CalibrationArtifactIdentities,
    CalibrationCaseResult,
    CalibrationFacetResult,
    CalibrationRunEvidence,
    ReferenceCorpusCase,
    ReferenceCorpusManifest,
    ReferenceFacetExpectation,
)
from lecture_study.domain.clause_reconciliation import (
    ClauseReconciliationOutcome,
    FacetOutcome,
    SemanticFacet,
)
from lecture_study.domain.semantic_observation import CandidateProducerIdentity
from lecture_study.quality.calibration import (
    CalibrationCurrentStateUnavailable,
    CalibrationEvidenceAuditor,
    CalibrationScopeState,
    FacetDisagreementKind,
    canonical_calibration_run_evidence_sha256,
    canonical_reference_corpus_case_sha256,
    canonical_reference_corpus_manifest_sha256,
)


def _digest(label: str) -> str:
    import hashlib

    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _producer(name: str) -> CandidateProducerIdentity:
    return CandidateProducerIdentity(
        producer_id=name,
        manifest_sha256=_digest(f"{name}-manifest"),
        model_sha256=_digest(f"{name}-model"),
        prompt_sha256=_digest(f"{name}-prompt"),
        schema_sha256=_digest(f"{name}-schema"),
        implementation_sha256=_digest(f"{name}-implementation"),
        configuration_sha256=_digest(f"{name}-configuration"),
        engine_version="engine-1",
    )


def _identities(seed: str = "case-1") -> CalibrationArtifactIdentities:
    return CalibrationArtifactIdentities(
        source_manifest_sha256=_digest(f"{seed}-source-manifest"),
        source_content_aggregation_sha256=_digest(f"{seed}-source-content"),
        source_universe_sha256=_digest(f"{seed}-source-universe"),
        clause_inventory_sha256=_digest(f"{seed}-clause-inventory"),
        candidate_batches_sha256=_digest(f"{seed}-candidate-batches"),
        atomic_ledger_sha256=_digest(f"{seed}-atomic-ledger"),
        reconciliations_sha256=_digest(f"{seed}-reconciliations"),
    )


def _case(case_id: str = "case-1") -> ReferenceCorpusCase:
    return ReferenceCorpusCase(
        schema_version=1,
        case_id=case_id,
        lecture_id="lecture-1",
        learning_unit_id="unit-1",
        source_clause_id=f"clause-{case_id}",
        artifact_identities=_identities(case_id),
        reference_annotation_sha256=_digest(f"{case_id}-reference-annotation"),
        expected_outcome=ClauseReconciliationOutcome.REPRESENTED,
        facet_expectations=(
            ReferenceFacetExpectation(
                facet=SemanticFacet.CERTAINTY,
                expected_outcome=FacetOutcome.MATCHED,
            ),
            ReferenceFacetExpectation(
                facet=SemanticFacet.POLARITY,
                expected_outcome=FacetOutcome.MATCHED,
            ),
        ),
    )


def _manifest(*cases: ReferenceCorpusCase) -> ReferenceCorpusManifest:
    selected_cases = cases or (_case(),)
    return ReferenceCorpusManifest(
        schema_version=1,
        corpus_id="corpus-1",
        corpus_version="version-1",
        annotation_schema_sha256=_digest("annotation-schema"),
        rubric_sha256=_digest("rubric"),
        cases=selected_cases,
    )


def _case_result(
    manifest: ReferenceCorpusManifest,
    case: ReferenceCorpusCase,
    *,
    generator: CandidateProducerIdentity | None = None,
    evaluator: CandidateProducerIdentity | None = None,
    observed_outcome: ClauseReconciliationOutcome = (
        ClauseReconciliationOutcome.REPRESENTED
    ),
    facet_results: tuple[CalibrationFacetResult, ...] | None = None,
    artifact_identities: CalibrationArtifactIdentities | None = None,
) -> CalibrationCaseResult:
    return CalibrationCaseResult(
        schema_version=1,
        corpus_id=manifest.corpus_id,
        corpus_version=manifest.corpus_version,
        corpus_manifest_sha256=canonical_reference_corpus_manifest_sha256(manifest),
        reference_case_sha256=canonical_reference_corpus_case_sha256(case),
        case_id=case.case_id,
        lecture_id=case.lecture_id,
        learning_unit_id=case.learning_unit_id,
        source_clause_id=case.source_clause_id,
        artifact_identities=artifact_identities or case.artifact_identities,
        evaluator_output_sha256=_digest(f"{case.case_id}-evaluator-output"),
        generator=generator or _producer("generator"),
        evaluator=evaluator or _producer("evaluator"),
        observed_outcome=observed_outcome,
        facet_results=facet_results
        or (
            CalibrationFacetResult(
                facet=SemanticFacet.CERTAINTY,
                observed_outcome=FacetOutcome.MATCHED,
            ),
            CalibrationFacetResult(
                facet=SemanticFacet.POLARITY,
                observed_outcome=FacetOutcome.MATCHED,
            ),
        ),
    )


def _run(
    manifest: ReferenceCorpusManifest,
    *results: CalibrationCaseResult,
    generator: CandidateProducerIdentity | None = None,
    evaluator: CandidateProducerIdentity | None = None,
) -> CalibrationRunEvidence:
    selected_generator = generator or _producer("generator")
    selected_evaluator = evaluator or _producer("evaluator")
    selected_results = results or (
        _case_result(
            manifest,
            manifest.cases[0],
            generator=selected_generator,
            evaluator=selected_evaluator,
        ),
    )
    return CalibrationRunEvidence(
        schema_version=1,
        run_id="run-1",
        corpus_id=manifest.corpus_id,
        corpus_version=manifest.corpus_version,
        corpus_manifest_sha256=canonical_reference_corpus_manifest_sha256(manifest),
        generator=selected_generator,
        evaluator=selected_evaluator,
        case_results=selected_results,
    )


class _CurrentState:
    def require_current(
        self,
        evidence: CalibrationRunEvidence,
        *,
        manifest_sha256: str,
        evidence_sha256: str,
    ) -> None:
        assert len(manifest_sha256) == 64
        assert evidence_sha256 == canonical_calibration_run_evidence_sha256(evidence)


class _UnavailableState:
    def require_current(
        self,
        evidence: CalibrationRunEvidence,
        *,
        manifest_sha256: str,
        evidence_sha256: str,
    ) -> None:
        del evidence, manifest_sha256, evidence_sha256
        raise CalibrationCurrentStateUnavailable("state unavailable")


def test_complete_evidence_is_structural_only_and_never_authoritative() -> None:
    manifest = _manifest()
    report = CalibrationEvidenceAuditor(_CurrentState()).audit(
        manifest,
        _run(manifest),
    )

    assert report.structurally_complete
    assert not report.calibrated
    assert not report.publication_ready
    assert report.scope_state is CalibrationScopeState.NEEDS_REVIEW
    assert report.current_state_error is None
    assert report.expected_case_count == 1
    assert report.evaluated_case_count == 1
    assert report.exact_identity_ratio == 1.0
    assert report.outcome_match_ratio == 1.0
    assert report.facet_match_ratio == 1.0


def test_unavailable_current_state_fails_closed_without_hiding_structure() -> None:
    manifest = _manifest()

    default_report = CalibrationEvidenceAuditor().audit(manifest, _run(manifest))
    explicit_report = CalibrationEvidenceAuditor(_UnavailableState()).audit(
        manifest,
        _run(manifest),
    )

    assert default_report.structurally_complete
    assert default_report.current_state_error is not None
    assert explicit_report.current_state_error == "state unavailable"
    assert explicit_report.scope_state is CalibrationScopeState.NEEDS_REVIEW


def test_reference_manifest_rejects_duplicate_and_noncanonical_cases() -> None:
    first = _case("case-1")
    second = _case("case-2")

    with pytest.raises(ValidationError, match="case IDs must be unique"):
        _manifest(first, first)
    with pytest.raises(ValidationError, match="canonical order"):
        _manifest(second, first)


def test_reference_case_rejects_duplicate_and_noncanonical_facets() -> None:
    payload = _case().model_dump()
    payload["facet_expectations"] = (
        ReferenceFacetExpectation(
            facet=SemanticFacet.POLARITY,
            expected_outcome=FacetOutcome.MATCHED,
        ),
        ReferenceFacetExpectation(
            facet=SemanticFacet.CERTAINTY,
            expected_outcome=FacetOutcome.MATCHED,
        ),
    )
    with pytest.raises(ValidationError, match="canonical order"):
        ReferenceCorpusCase.model_validate(payload)

    payload["facet_expectations"] = (
        payload["facet_expectations"][0],
        payload["facet_expectations"][0],
    )
    with pytest.raises(ValidationError, match="must be unique"):
        ReferenceCorpusCase.model_validate(payload)


def test_run_rejects_duplicate_and_noncanonical_results() -> None:
    first = _case("case-1")
    second = _case("case-2")
    manifest = _manifest(first, second)
    first_result = _case_result(manifest, first)
    second_result = _case_result(manifest, second)

    with pytest.raises(ValidationError, match="result IDs must be unique"):
        _run(manifest, first_result, first_result)
    with pytest.raises(ValidationError, match="canonical order"):
        _run(manifest, second_result, first_result)


def test_generator_cannot_approve_itself_or_reuse_its_model_identity() -> None:
    manifest = _manifest()
    generator = _producer("generator")
    same_model = _producer("other-evaluator").model_copy(
        update={"model_sha256": generator.model_sha256}
    )

    with pytest.raises(ValidationError, match="must be independent"):
        _case_result(
            manifest, manifest.cases[0], generator=generator, evaluator=generator
        )
    with pytest.raises(ValidationError, match="distinct model identities"):
        _case_result(
            manifest,
            manifest.cases[0],
            generator=generator,
            evaluator=same_model,
        )


def test_auditor_recomputes_missing_and_additional_case_sets() -> None:
    first = _case("case-1")
    second = _case("case-2")
    manifest = _manifest(first, second)
    additional = _case("case-3")
    additional_result = _case_result(_manifest(additional), additional).model_copy(
        update={
            "corpus_manifest_sha256": canonical_reference_corpus_manifest_sha256(
                manifest
            )
        }
    )
    run = _run(manifest, _case_result(manifest, first), additional_result)

    report = CalibrationEvidenceAuditor(_CurrentState()).audit(manifest, run)

    assert report.missing_case_ids == ("case-2",)
    assert report.additional_case_ids == ("case-3",)
    assert not report.structurally_complete
    assert report.scope_state is CalibrationScopeState.NEEDS_REVIEW


def test_case_substitution_is_quarantined_even_when_case_hash_is_replayed() -> None:
    manifest = _manifest()
    case = manifest.cases[0]
    substituted = _case_result(
        manifest,
        case,
        artifact_identities=_identities("substituted"),
    )

    report = CalibrationEvidenceAuditor(_CurrentState()).audit(
        manifest,
        _run(manifest, substituted),
    )

    assert report.substituted_case_ids == ("case-1",)
    assert report.scope_state is CalibrationScopeState.QUARANTINE
    assert not report.structurally_complete


def test_manifest_substitution_and_replay_are_quarantined() -> None:
    manifest = _manifest()
    run = _run(manifest).model_copy(
        update={"corpus_manifest_sha256": _digest("substituted-manifest")}
    )

    report = CalibrationEvidenceAuditor(_CurrentState()).audit(manifest, run)

    assert report.manifest_identity_mismatch
    assert report.scope_state is CalibrationScopeState.QUARANTINE


def test_identity_confusion_between_run_and_case_is_quarantined() -> None:
    manifest = _manifest()
    result = _case_result(
        manifest,
        manifest.cases[0],
        generator=_producer("case-generator"),
        evaluator=_producer("case-evaluator"),
    )
    run = _run(
        manifest,
        result,
        generator=_producer("run-generator"),
        evaluator=_producer("run-evaluator"),
    )

    report = CalibrationEvidenceAuditor(_CurrentState()).audit(manifest, run)

    assert report.generator_identity_confusion_case_ids == ("case-1",)
    assert report.evaluator_identity_confusion_case_ids == ("case-1",)
    assert report.scope_state is CalibrationScopeState.QUARANTINE


def test_outcome_and_facet_disagreements_are_preserved_without_threshold() -> None:
    manifest = _manifest()
    result = _case_result(
        manifest,
        manifest.cases[0],
        observed_outcome=ClauseReconciliationOutcome.CONTRADICTED,
        facet_results=(
            CalibrationFacetResult(
                facet=SemanticFacet.CERTAINTY,
                observed_outcome=FacetOutcome.CONTRADICTED,
            ),
            CalibrationFacetResult(
                facet=SemanticFacet.CORE_STATEMENT,
                observed_outcome=FacetOutcome.MATCHED,
            ),
        ),
    )

    report = CalibrationEvidenceAuditor(_CurrentState()).audit(
        manifest,
        _run(manifest, result),
    )

    assert report.outcome_disagreement_case_ids == ("case-1",)
    assert {(item.facet, item.kind) for item in report.facet_disagreements} == {
        (SemanticFacet.CERTAINTY, FacetDisagreementKind.OUTCOME),
        (SemanticFacet.CORE_STATEMENT, FacetDisagreementKind.ADDITIONAL),
        (SemanticFacet.POLARITY, FacetDisagreementKind.MISSING),
    }
    assert report.structurally_complete
    assert not report.calibrated
    assert report.outcome_match_ratio == 0.0
    assert report.facet_match_ratio == 0.0


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("lecture_id", "lecture-2"),
        ("learning_unit_id", "unit-2"),
        ("source_clause_id", "clause-other"),
        ("corpus_id", "corpus-2"),
        ("corpus_version", "version-2"),
        ("reference_case_sha256", _digest("other-reference-case")),
    ),
)
def test_case_identity_mutations_are_quarantined(
    field_name: str,
    replacement: str,
) -> None:
    manifest = _manifest()
    result = _case_result(manifest, manifest.cases[0]).model_copy(
        update={field_name: replacement}
    )

    report = CalibrationEvidenceAuditor(_CurrentState()).audit(
        manifest,
        _run(manifest, result),
    )

    assert report.substituted_case_ids == ("case-1",)
    assert report.scope_state is CalibrationScopeState.QUARANTINE


def test_canonical_identities_change_on_omission_and_substitution() -> None:
    first = _case("case-1")
    second = _case("case-2")
    complete = _manifest(first, second)
    omitted = _manifest(first)
    substituted = ReferenceCorpusManifest(
        schema_version=1,
        corpus_id=complete.corpus_id,
        corpus_version=complete.corpus_version,
        annotation_schema_sha256=complete.annotation_schema_sha256,
        rubric_sha256=complete.rubric_sha256,
        cases=(
            first,
            second.model_copy(
                update={"artifact_identities": _identities("substituted")}
            ),
        ),
    )

    assert canonical_reference_corpus_manifest_sha256(complete) != (
        canonical_reference_corpus_manifest_sha256(omitted)
    )
    assert canonical_reference_corpus_manifest_sha256(complete) != (
        canonical_reference_corpus_manifest_sha256(substituted)
    )


def test_annotation_rubric_and_evaluator_output_identities_are_bound() -> None:
    manifest = _manifest()
    run = _run(manifest)

    changed_annotation_schema = manifest.model_copy(
        update={"annotation_schema_sha256": _digest("other-annotation-schema")}
    )
    changed_rubric = manifest.model_copy(
        update={"rubric_sha256": _digest("other-rubric")}
    )
    changed_result = run.case_results[0].model_copy(
        update={"evaluator_output_sha256": _digest("other-evaluator-output")}
    )
    changed_run = run.model_copy(update={"case_results": (changed_result,)})

    assert canonical_reference_corpus_manifest_sha256(manifest) != (
        canonical_reference_corpus_manifest_sha256(changed_annotation_schema)
    )
    assert canonical_reference_corpus_manifest_sha256(manifest) != (
        canonical_reference_corpus_manifest_sha256(changed_rubric)
    )
    assert canonical_calibration_run_evidence_sha256(run) != (
        canonical_calibration_run_evidence_sha256(changed_run)
    )


def test_frozen_report_cannot_be_rewritten_as_calibrated() -> None:
    manifest = _manifest()
    report = CalibrationEvidenceAuditor(_CurrentState()).audit(
        manifest,
        _run(manifest),
    )

    with pytest.raises(AttributeError):
        report.scope_state = CalibrationScopeState.QUARANTINE  # type: ignore[misc]


def test_auditor_revalidates_copied_duplicate_results() -> None:
    manifest = _manifest()
    result = _case_result(manifest, manifest.cases[0])
    bypassed = _run(manifest, result).model_copy(
        update={"case_results": (result, result)}
    )

    with pytest.raises(ValidationError, match="result IDs must be unique"):
        CalibrationEvidenceAuditor(_CurrentState()).audit(manifest, bypassed)


def test_auditor_revalidates_constructed_duplicate_results() -> None:
    manifest = _manifest()
    valid = _run(manifest)
    payload = valid.model_dump(mode="python")
    payload["case_results"] = (
        payload["case_results"][0],
        payload["case_results"][0],
    )
    bypassed = CalibrationRunEvidence.model_construct(**payload)

    with pytest.raises(ValidationError, match="result IDs must be unique"):
        CalibrationEvidenceAuditor(_CurrentState()).audit(manifest, bypassed)


def test_auditor_revalidates_copied_generator_evaluator_model_confusion() -> None:
    manifest = _manifest()
    valid = _run(manifest)
    copied_evaluator = valid.evaluator.model_copy(
        update={"model_sha256": valid.generator.model_sha256}
    )
    bypassed = valid.model_copy(update={"evaluator": copied_evaluator})

    with pytest.raises(ValidationError, match="distinct model identities"):
        CalibrationEvidenceAuditor(_CurrentState()).audit(manifest, bypassed)


def test_auditor_revalidates_nested_copied_case_result() -> None:
    manifest = _manifest()
    valid = _run(manifest)
    result = valid.case_results[0]
    duplicated_facet = result.model_copy(
        update={"facet_results": (result.facet_results[0],) * 2}
    )
    bypassed = valid.model_copy(update={"case_results": (duplicated_facet,)})

    with pytest.raises(ValidationError, match="facet results must be unique"):
        CalibrationEvidenceAuditor(_CurrentState()).audit(manifest, bypassed)


def test_canonical_manifest_identity_rejects_copied_duplicate_cases() -> None:
    manifest = _manifest()
    bypassed = manifest.model_copy(
        update={"cases": (manifest.cases[0], manifest.cases[0])}
    )

    with pytest.raises(ValidationError, match="case IDs must be unique"):
        canonical_reference_corpus_manifest_sha256(bypassed)
