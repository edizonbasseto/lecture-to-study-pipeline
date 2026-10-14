"""Immutable evidence contracts for non-authoritative semantic calibration."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from lecture_study.domain.clause_reconciliation import (
    ClauseReconciliationOutcome,
    FacetOutcome,
    SemanticFacet,
)
from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.semantic_observation import CandidateProducerIdentity
from lecture_study.domain.versioning import SchemaVersionOne

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class CalibrationArtifactIdentities(BaseModel):
    """Complete semantic artifact identities bound to one reference case."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    source_manifest_sha256: _Digest
    source_content_aggregation_sha256: _Digest
    source_universe_sha256: _Digest
    clause_inventory_sha256: _Digest
    candidate_batches_sha256: _Digest
    atomic_ledger_sha256: _Digest
    reconciliations_sha256: _Digest


class ReferenceFacetExpectation(BaseModel):
    """Expected disposition for one independently evaluated semantic facet."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    facet: SemanticFacet
    expected_outcome: FacetOutcome


class ReferenceCorpusCase(BaseModel):
    """One versioned reference case without an implied expert authority."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    case_id: SafeIdentifier
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    source_clause_id: SafeIdentifier
    artifact_identities: CalibrationArtifactIdentities
    reference_annotation_sha256: _Digest
    expected_outcome: ClauseReconciliationOutcome
    facet_expectations: tuple[ReferenceFacetExpectation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_facet_expectations(self) -> Self:
        facet_values = tuple(
            expectation.facet.value for expectation in self.facet_expectations
        )
        if len(facet_values) != len(set(facet_values)):
            raise ValueError("reference facet expectations must be unique")
        if facet_values != tuple(sorted(facet_values)):
            raise ValueError("reference facet expectations must use canonical order")
        return self


class ReferenceCorpusManifest(BaseModel):
    """Exact versioned case set used by one deterministic calibration run."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    corpus_id: SafeIdentifier
    corpus_version: SafeIdentifier
    annotation_schema_sha256: _Digest
    rubric_sha256: _Digest
    cases: tuple[ReferenceCorpusCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_cases(self) -> Self:
        case_ids = tuple(str(case.case_id) for case in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("reference corpus case IDs must be unique")
        if case_ids != tuple(sorted(case_ids)):
            raise ValueError("reference corpus cases must use canonical order")

        case_scopes = tuple(
            (
                str(case.lecture_id),
                str(case.learning_unit_id),
                str(case.source_clause_id),
            )
            for case in self.cases
        )
        if len(case_scopes) != len(set(case_scopes)):
            raise ValueError("reference corpus case scopes must be unique")
        return self


class CalibrationFacetResult(BaseModel):
    """Observed evaluator result for one reference facet."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    facet: SemanticFacet
    observed_outcome: FacetOutcome


class CalibrationCaseResult(BaseModel):
    """One independently generated and evaluated calibration observation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    corpus_id: SafeIdentifier
    corpus_version: SafeIdentifier
    corpus_manifest_sha256: _Digest
    reference_case_sha256: _Digest
    case_id: SafeIdentifier
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    source_clause_id: SafeIdentifier
    artifact_identities: CalibrationArtifactIdentities
    evaluator_output_sha256: _Digest
    generator: CandidateProducerIdentity
    evaluator: CandidateProducerIdentity
    observed_outcome: ClauseReconciliationOutcome
    facet_results: tuple[CalibrationFacetResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_result(self) -> Self:
        _require_independent_producers(self.generator, self.evaluator)
        facet_values = tuple(result.facet.value for result in self.facet_results)
        if len(facet_values) != len(set(facet_values)):
            raise ValueError("calibration facet results must be unique")
        if facet_values != tuple(sorted(facet_values)):
            raise ValueError("calibration facet results must use canonical order")
        return self


class CalibrationRunEvidence(BaseModel):
    """Complete deterministic evidence for one non-authoritative calibration run."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    run_id: SafeIdentifier
    corpus_id: SafeIdentifier
    corpus_version: SafeIdentifier
    corpus_manifest_sha256: _Digest
    generator: CandidateProducerIdentity
    evaluator: CandidateProducerIdentity
    case_results: tuple[CalibrationCaseResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_run(self) -> Self:
        _require_independent_producers(self.generator, self.evaluator)
        case_ids = tuple(str(result.case_id) for result in self.case_results)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("calibration case result IDs must be unique")
        if case_ids != tuple(sorted(case_ids)):
            raise ValueError("calibration case results must use canonical order")
        return self


def _require_independent_producers(
    generator: CandidateProducerIdentity,
    evaluator: CandidateProducerIdentity,
) -> None:
    if generator.producer_id == evaluator.producer_id:
        raise ValueError("calibration generator and evaluator must be independent")
    if generator.model_sha256 == evaluator.model_sha256:
        raise ValueError(
            "calibration generator and evaluator must use distinct model identities"
        )
