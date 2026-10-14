"""Deterministic structural audit for non-authoritative calibration evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from lecture_study.domain.calibration import (
    CalibrationCaseResult,
    CalibrationRunEvidence,
    ReferenceCorpusCase,
    ReferenceCorpusManifest,
)
from lecture_study.domain.clause_reconciliation import SemanticFacet
from lecture_study.quality.trust import canonical_json_bytes


class CalibrationCurrentStateUnavailable(RuntimeError):
    """Raised when calibration freshness cannot be established."""


class CalibrationCurrentStatePort(Protocol):
    """Inward port for a separately trusted calibration current-state store."""

    def require_current(
        self,
        evidence: CalibrationRunEvidence,
        *,
        manifest_sha256: str,
        evidence_sha256: str,
    ) -> None:
        """Reject stale, replayed, substituted, or unavailable evidence."""


class UnavailableCalibrationCurrentState:
    """Fail-closed state port used until an explicit adapter is composed."""

    def require_current(
        self,
        evidence: CalibrationRunEvidence,
        *,
        manifest_sha256: str,
        evidence_sha256: str,
    ) -> None:
        del evidence, manifest_sha256, evidence_sha256
        raise CalibrationCurrentStateUnavailable(
            "calibration current-state adapter is not configured"
        )


class CalibrationScopeState(StrEnum):
    """Scope-local state produced without publication authority."""

    NEEDS_REVIEW = "needs_review"
    QUARANTINE = "quarantine"


class FacetDisagreementKind(StrEnum):
    """Exact-set disagreement for one reference facet."""

    MISSING = "missing"
    ADDITIONAL = "additional"
    OUTCOME = "outcome"


@dataclass(frozen=True, slots=True)
class CalibrationFacetDisagreement:
    """One deterministic facet-level mismatch."""

    case_id: str
    facet: SemanticFacet
    kind: FacetDisagreementKind


@dataclass(frozen=True, slots=True)
class CalibrationAuditReport:
    """Non-authoritative structural result with permanent publication blockers."""

    corpus_manifest_sha256: str
    run_evidence_sha256: str
    missing_case_ids: tuple[str, ...]
    additional_case_ids: tuple[str, ...]
    substituted_case_ids: tuple[str, ...]
    outcome_disagreement_case_ids: tuple[str, ...]
    facet_disagreements: tuple[CalibrationFacetDisagreement, ...]
    generator_identity_confusion_case_ids: tuple[str, ...]
    evaluator_identity_confusion_case_ids: tuple[str, ...]
    manifest_identity_mismatch: bool
    current_state_error: str | None
    scope_state: CalibrationScopeState
    expected_case_count: int
    evaluated_case_count: int
    exact_identity_case_count: int
    outcome_match_case_count: int
    expected_facet_count: int
    matched_facet_count: int

    @property
    def structurally_complete(self) -> bool:
        """Report whether the exact case set and identities are structurally intact."""

        return (
            not any(
                (
                    self.missing_case_ids,
                    self.additional_case_ids,
                    self.substituted_case_ids,
                    self.generator_identity_confusion_case_ids,
                    self.evaluator_identity_confusion_case_ids,
                )
            )
            and not self.manifest_identity_mismatch
        )

    @property
    def calibrated(self) -> bool:
        """Remain false until expert provenance and acceptance policy exist."""

        return False

    @property
    def exact_identity_ratio(self) -> float:
        """Return an identity ratio without selecting an acceptance threshold."""

        return self.exact_identity_case_count / self.expected_case_count

    @property
    def outcome_match_ratio(self) -> float:
        """Return an outcome ratio without granting calibration authority."""

        return self.outcome_match_case_count / self.expected_case_count

    @property
    def facet_match_ratio(self) -> float:
        """Return a facet ratio without granting calibration authority."""

        return self.matched_facet_count / self.expected_facet_count

    @property
    def publication_ready(self) -> bool:
        """Calibration evidence never grants publication authority."""

        return False


class CalibrationEvidenceAuditor:
    """Recompute exact-set evidence without inferring calibration thresholds."""

    def __init__(
        self,
        current_state: CalibrationCurrentStatePort | None = None,
    ) -> None:
        self._current_state = current_state or UnavailableCalibrationCurrentState()

    def audit(
        self,
        manifest: ReferenceCorpusManifest,
        evidence: CalibrationRunEvidence,
    ) -> CalibrationAuditReport:
        """Audit all cases and preserve mismatches as diagnostic evidence."""

        manifest = _revalidate_model(manifest, ReferenceCorpusManifest)
        evidence = _revalidate_model(evidence, CalibrationRunEvidence)
        manifest_sha256 = canonical_reference_corpus_manifest_sha256(manifest)
        evidence_sha256 = canonical_calibration_run_evidence_sha256(evidence)
        expected_by_id = {str(case.case_id): case for case in manifest.cases}
        observed_by_id = {
            str(result.case_id): result for result in evidence.case_results
        }
        expected_ids = set(expected_by_id)
        observed_ids = set(observed_by_id)
        common_ids = tuple(sorted(expected_ids & observed_ids))

        manifest_identity_mismatch = self._manifest_identity_mismatch(
            manifest,
            evidence,
            manifest_sha256,
        )
        substituted = tuple(
            case_id
            for case_id in common_ids
            if not self._result_matches_case(
                manifest,
                manifest_sha256,
                expected_by_id[case_id],
                observed_by_id[case_id],
            )
        )
        substituted_ids = set(substituted)
        exact_identity_ids = tuple(
            case_id for case_id in common_ids if case_id not in substituted_ids
        )
        outcome_disagreements = tuple(
            case_id
            for case_id in exact_identity_ids
            if expected_by_id[case_id].expected_outcome
            is not observed_by_id[case_id].observed_outcome
        )
        facet_disagreements = tuple(
            disagreement
            for case_id in exact_identity_ids
            for disagreement in self._facet_disagreements(
                expected_by_id[case_id],
                observed_by_id[case_id],
            )
        )
        matched_facet_count = sum(
            self._matched_facet_count(
                expected_by_id[case_id],
                observed_by_id[case_id],
            )
            for case_id in exact_identity_ids
        )
        generator_confusion = tuple(
            case_id
            for case_id in common_ids
            if observed_by_id[case_id].generator != evidence.generator
        )
        evaluator_confusion = tuple(
            case_id
            for case_id in common_ids
            if observed_by_id[case_id].evaluator != evidence.evaluator
        )
        current_state_error = self._current_state_error(
            evidence,
            manifest_sha256,
            evidence_sha256,
        )

        quarantined = any(
            (
                manifest_identity_mismatch,
                substituted,
                generator_confusion,
                evaluator_confusion,
            )
        )
        return CalibrationAuditReport(
            corpus_manifest_sha256=manifest_sha256,
            run_evidence_sha256=evidence_sha256,
            missing_case_ids=tuple(sorted(expected_ids - observed_ids)),
            additional_case_ids=tuple(sorted(observed_ids - expected_ids)),
            substituted_case_ids=substituted,
            outcome_disagreement_case_ids=outcome_disagreements,
            facet_disagreements=facet_disagreements,
            generator_identity_confusion_case_ids=generator_confusion,
            evaluator_identity_confusion_case_ids=evaluator_confusion,
            manifest_identity_mismatch=manifest_identity_mismatch,
            current_state_error=current_state_error,
            scope_state=(
                CalibrationScopeState.QUARANTINE
                if quarantined
                else CalibrationScopeState.NEEDS_REVIEW
            ),
            expected_case_count=len(expected_ids),
            evaluated_case_count=len(observed_ids),
            exact_identity_case_count=len(exact_identity_ids),
            outcome_match_case_count=(
                len(exact_identity_ids) - len(outcome_disagreements)
            ),
            expected_facet_count=sum(
                len(case.facet_expectations) for case in manifest.cases
            ),
            matched_facet_count=matched_facet_count,
        )

    @staticmethod
    def _manifest_identity_mismatch(
        manifest: ReferenceCorpusManifest,
        evidence: CalibrationRunEvidence,
        manifest_sha256: str,
    ) -> bool:
        return (
            evidence.corpus_id != manifest.corpus_id
            or evidence.corpus_version != manifest.corpus_version
            or evidence.corpus_manifest_sha256 != manifest_sha256
        )

    @staticmethod
    def _result_matches_case(
        manifest: ReferenceCorpusManifest,
        manifest_sha256: str,
        case: ReferenceCorpusCase,
        result: CalibrationCaseResult,
    ) -> bool:
        return (
            result.corpus_id == manifest.corpus_id
            and result.corpus_version == manifest.corpus_version
            and result.corpus_manifest_sha256 == manifest_sha256
            and result.reference_case_sha256
            == canonical_reference_corpus_case_sha256(case)
            and result.case_id == case.case_id
            and result.lecture_id == case.lecture_id
            and result.learning_unit_id == case.learning_unit_id
            and result.source_clause_id == case.source_clause_id
            and result.artifact_identities == case.artifact_identities
        )

    @staticmethod
    def _facet_disagreements(
        case: ReferenceCorpusCase,
        result: CalibrationCaseResult,
    ) -> tuple[CalibrationFacetDisagreement, ...]:
        expected = {
            expectation.facet: expectation.expected_outcome
            for expectation in case.facet_expectations
        }
        observed = {
            facet_result.facet: facet_result.observed_outcome
            for facet_result in result.facet_results
        }
        missing = (
            CalibrationFacetDisagreement(
                case_id=str(case.case_id),
                facet=facet,
                kind=FacetDisagreementKind.MISSING,
            )
            for facet in sorted(
                expected.keys() - observed.keys(), key=lambda item: item.value
            )
        )
        additional = (
            CalibrationFacetDisagreement(
                case_id=str(case.case_id),
                facet=facet,
                kind=FacetDisagreementKind.ADDITIONAL,
            )
            for facet in sorted(
                observed.keys() - expected.keys(), key=lambda item: item.value
            )
        )
        outcomes = (
            CalibrationFacetDisagreement(
                case_id=str(case.case_id),
                facet=facet,
                kind=FacetDisagreementKind.OUTCOME,
            )
            for facet in sorted(
                expected.keys() & observed.keys(), key=lambda item: item.value
            )
            if expected[facet] is not observed[facet]
        )
        return (*missing, *additional, *outcomes)

    @staticmethod
    def _matched_facet_count(
        case: ReferenceCorpusCase,
        result: CalibrationCaseResult,
    ) -> int:
        observed = {
            facet_result.facet: facet_result.observed_outcome
            for facet_result in result.facet_results
        }
        return sum(
            observed.get(expectation.facet) is expectation.expected_outcome
            for expectation in case.facet_expectations
        )

    def _current_state_error(
        self,
        evidence: CalibrationRunEvidence,
        manifest_sha256: str,
        evidence_sha256: str,
    ) -> str | None:
        try:
            self._current_state.require_current(
                evidence,
                manifest_sha256=manifest_sha256,
                evidence_sha256=evidence_sha256,
            )
        except CalibrationCurrentStateUnavailable as error:
            return str(error)
        return None


def canonical_reference_corpus_case_sha256(case: ReferenceCorpusCase) -> str:
    """Return the deterministic identity of one complete reference case."""

    case = _revalidate_model(case, ReferenceCorpusCase)
    return _canonical_model_sha256(case.model_dump(mode="json"))


def canonical_reference_corpus_manifest_sha256(
    manifest: ReferenceCorpusManifest,
) -> str:
    """Return the deterministic identity of one complete corpus manifest."""

    manifest = _revalidate_model(manifest, ReferenceCorpusManifest)
    return _canonical_model_sha256(manifest.model_dump(mode="json"))


def canonical_calibration_run_evidence_sha256(
    evidence: CalibrationRunEvidence,
) -> str:
    """Return the deterministic identity of one complete calibration run."""

    evidence = _revalidate_model(evidence, CalibrationRunEvidence)
    return _canonical_model_sha256(evidence.model_dump(mode="json"))


def _canonical_model_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _revalidate_model[Model: BaseModel](
    value: Model,
    model_type: type[Model],
) -> Model:
    return model_type.model_validate(
        value.model_dump(mode="python", warnings=False),
    )
