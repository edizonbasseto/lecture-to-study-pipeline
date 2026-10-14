"""Immutable clause-level semantic reconciliation contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from lecture_study.domain.content_ledger import DisagreementStatus
from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.versioning import SchemaVersionOne

_NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ClauseReconciliationOutcome(StrEnum):
    """Fail-closed source-clause outcome against the reconciled ledger."""

    REPRESENTED = "represented"
    PARTIAL = "partial"
    CONTRADICTED = "contradicted"
    MISSING = "missing"
    UNVERIFIABLE = "unverifiable"


class FacetOutcome(StrEnum):
    """Comparison result for one semantically material clause facet."""

    MATCHED = "matched"
    MISSING = "missing"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class SemanticFacet(StrEnum):
    """Closed clinical facets that reconciliation must preserve independently."""

    DISPOSITION = "disposition"
    PROPOSITION_COUNT = "proposition-count"
    CONTENT_TYPE = "content-type"
    CORE_STATEMENT = "core-statement"
    POLARITY = "polarity"
    CERTAINTY = "certainty"
    RECOMMENDATION_STRENGTH = "recommendation-strength"
    COMPARISON_DIRECTION = "comparison-direction"
    CAUSAL_DIRECTION = "causal-direction"
    CONDITION = "condition"
    EXCEPTION = "exception"
    CORRECTION = "correction"
    NUMERIC_VALUE = "numeric-value"
    NUMERIC_UNIT = "numeric-unit"
    NUMERIC_COMPARATOR = "numeric-comparator"
    NUMERIC_DENOMINATOR = "numeric-denominator"
    NUMERIC_ROLE = "numeric-role"
    NUMERIC_BOUNDARY = "numeric-boundary"
    NUMERIC_SOURCE_BINDING = "numeric-source-binding"
    DOSE_MEDICATION = "dose-medication"
    DOSE_AMOUNT = "dose-amount"
    DOSE_ROUTE = "dose-route"
    DOSE_INTERVAL = "dose-interval"
    DOSE_DURATION = "dose-duration"
    SAFETY_CLASSIFICATION = "safety-classification"
    CLINICAL_CONTEXT = "clinical-context"


class PropositionReferenceRole(StrEnum):
    """Accounting role of a final proposition for one reconciled clause."""

    PRIMARY = "primary"
    SUPPORTING = "supporting"


class FacetAssessment(BaseModel):
    """Declared result for one exact semantic facet."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    facet: SemanticFacet
    outcome: FacetOutcome
    detail: _NonBlankText | None = None


class DetailedCandidateDisagreement(BaseModel):
    """Candidate values retained independently until authenticated resolution."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    disagreement_id: SafeIdentifier
    clause_id: SafeIdentifier
    facet: SemanticFacet
    observation_ids: tuple[SafeIdentifier, ...] = Field(min_length=2)
    observed_values: tuple[_NonBlankText, ...] = Field(min_length=2)
    status: DisagreementStatus
    chosen_observation_id: SafeIdentifier | None = None
    review_artifact_id: SafeIdentifier | None = None

    @model_validator(mode="after")
    def _validate_disagreement(self) -> DetailedCandidateDisagreement:
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("disagreement observation IDs must be unique")
        if len(self.observation_ids) != len(self.observed_values):
            raise ValueError("each disagreement observation requires an observed value")
        if len(set(self.observed_values)) < 2:
            raise ValueError("a disagreement requires at least two distinct values")
        resolution_fields = (self.chosen_observation_id, self.review_artifact_id)
        if self.status is DisagreementStatus.UNRESOLVED and any(
            value is not None for value in resolution_fields
        ):
            raise ValueError("unresolved disagreement cannot declare a resolution")
        if self.status is DisagreementStatus.HUMAN_RESOLVED:
            if any(value is None for value in resolution_fields):
                raise ValueError(
                    "human-resolved disagreement requires a choice and review artifact"
                )
            if self.chosen_observation_id not in self.observation_ids:
                raise ValueError(
                    "chosen disagreement observation must be one of the candidates"
                )
        return self


class HumanReviewRequirement(BaseModel):
    """Port-facing human-review requirement without authenticating the artifact."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    required: bool
    reason_facets: tuple[SemanticFacet, ...] = ()
    review_artifact_id: SafeIdentifier | None = None

    @model_validator(mode="after")
    def _validate_requirement(self) -> HumanReviewRequirement:
        if len(self.reason_facets) != len(set(self.reason_facets)):
            raise ValueError("human-review reason facets must be unique")
        if self.required and not self.reason_facets:
            raise ValueError(
                "required human review must name at least one reason facet"
            )
        if not self.required and (
            self.reason_facets or self.review_artifact_id is not None
        ):
            raise ValueError("non-required human review cannot declare review data")
        return self


class ReconciledPropositionReference(BaseModel):
    """Final-ledger proposition reference with exact accounting ownership."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    proposition_id: SafeIdentifier
    role: PropositionReferenceRole


class ClauseReconciliation(BaseModel):
    """One complete reconciliation from independent candidates to final ledger."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    reconciliation_id: SafeIdentifier
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    clause_id: SafeIdentifier
    candidate_observation_ids: tuple[SafeIdentifier, ...] = Field(min_length=2)
    candidate_proposition_ids: tuple[SafeIdentifier, ...] = ()
    source_proposition_count: int = Field(ge=0, le=(1 << 63) - 1)
    ledger_proposition_references: tuple[ReconciledPropositionReference, ...] = ()
    facet_assessments: tuple[FacetAssessment, ...] = Field(min_length=1)
    outcome: ClauseReconciliationOutcome
    disagreements: tuple[DetailedCandidateDisagreement, ...] = ()
    human_review: HumanReviewRequirement

    @model_validator(mode="after")
    def _validate_reconciliation(self) -> ClauseReconciliation:
        self._require_unique_accounting()
        facet_outcomes = {assessment.outcome for assessment in self.facet_assessments}
        unresolved_disagreement = any(
            disagreement.status is DisagreementStatus.UNRESOLVED
            for disagreement in self.disagreements
        )
        if unresolved_disagreement and (
            self.outcome is not ClauseReconciliationOutcome.UNVERIFIABLE
        ):
            raise ValueError("unresolved disagreement requires unverifiable outcome")
        self._validate_outcome(facet_outcomes)
        return self

    def _require_unique_accounting(self) -> None:
        accounting_groups = (
            ("candidate observation", self.candidate_observation_ids),
            ("candidate proposition", self.candidate_proposition_ids),
            (
                "ledger proposition",
                tuple(
                    reference.proposition_id
                    for reference in self.ledger_proposition_references
                ),
            ),
            (
                "facet",
                tuple(assessment.facet.value for assessment in self.facet_assessments),
            ),
            (
                "disagreement",
                tuple(
                    disagreement.disagreement_id for disagreement in self.disagreements
                ),
            ),
        )
        for label, identifiers in accounting_groups:
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} accounting values must be unique")
        if any(
            disagreement.clause_id != self.clause_id
            or not set(disagreement.observation_ids).issubset(
                set(self.candidate_observation_ids)
            )
            for disagreement in self.disagreements
        ):
            raise ValueError(
                "disagreements must belong to and use observations from this clause"
            )

    def _validate_outcome(self, facet_outcomes: set[FacetOutcome]) -> None:
        primary_count = sum(
            reference.role is PropositionReferenceRole.PRIMARY
            for reference in self.ledger_proposition_references
        )
        if self.outcome is ClauseReconciliationOutcome.REPRESENTED:
            if facet_outcomes != {FacetOutcome.MATCHED}:
                raise ValueError("represented outcome requires every facet to match")
            if primary_count != self.source_proposition_count:
                raise ValueError(
                    "represented outcome requires exact primary proposition accounting"
                )
            return
        if self.outcome is ClauseReconciliationOutcome.PARTIAL:
            if (
                FacetOutcome.MATCHED not in facet_outcomes
                or FacetOutcome.MISSING not in facet_outcomes
                or primary_count == 0
            ):
                raise ValueError(
                    "partial outcome requires matched and missing facets plus a "
                    "represented proposition"
                )
            return
        if self.outcome is ClauseReconciliationOutcome.CONTRADICTED:
            if FacetOutcome.CONTRADICTED not in facet_outcomes or primary_count == 0:
                raise ValueError(
                    "contradicted outcome requires a contradiction and proposition"
                )
            return
        if self.outcome is ClauseReconciliationOutcome.MISSING:
            if self.ledger_proposition_references:
                raise ValueError("missing outcome cannot reference ledger propositions")
            if FacetOutcome.MISSING not in facet_outcomes:
                raise ValueError("missing outcome requires a missing facet")
            return
        if FacetOutcome.UNVERIFIABLE not in facet_outcomes:
            raise ValueError("unverifiable outcome requires an unverifiable facet")
