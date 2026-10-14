"""Immutable contracts for the atomic source-content ledger."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.source_universe import EvidenceModality
from lecture_study.domain.versioning import (
    PersistedSequenceNumber,
    SchemaVersionOne,
)

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_EvidenceText = Annotated[str, StringConstraints(min_length=1)]
_DecimalText = Annotated[
    str,
    StringConstraints(
        max_length=128,
        pattern=r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d{1,4})?$",
    ),
]
_Confidence = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class ContentDimension(StrEnum):
    """Analytical dimensions required by ledger and material coverage."""

    CORE_STATEMENT = "core_statement"
    DEFINITION = "definition"
    MECHANISM = "mechanism"
    RELATIONSHIPS = "relationships"
    EXCEPTIONS = "exceptions"
    EXAMPLES = "examples"
    CLINICAL_APPLICATION = "clinical_application"
    SAFETY = "safety"
    UNCERTAINTY = "uncertainty"


class AtomicContentType(StrEnum):
    """Closed educational-content taxonomy defined by ADR 0023."""

    DEFINITION = "definition"
    MECHANISM = "mechanism"
    CAUSAL_RELATION = "causal-relation"
    DIAGNOSIS = "diagnosis"
    DIFFERENTIAL_DIAGNOSIS = "differential-diagnosis"
    SIGN = "sign"
    EXAMINATION = "examination"
    TREATMENT = "treatment"
    PROCEDURE = "procedure"
    DOSE = "dose"
    CONTRAINDICATION = "contraindication"
    RED_FLAG = "red-flag"
    PROGNOSIS = "prognosis"
    COMPARISON = "comparison"
    EXCEPTION = "exception"
    EXAMPLE = "example"
    PRACTICAL_OBSERVATION = "practical-observation"
    UNCERTAINTY = "uncertainty"
    CONCLUSION = "conclusion"


class ContentImportance(StrEnum):
    """Instructional importance without using it to permit omission."""

    CORE = "core"
    SUPPORTING = "supporting"
    EXAMPLE = "example"


class AssertionPolarity(StrEnum):
    """Explicit proposition polarity and conditional scope."""

    AFFIRMED = "affirmed"
    NEGATED = "negated"
    CONDITIONAL = "conditional"


class CertaintyLevel(StrEnum):
    """Epistemic strength asserted by the source."""

    CERTAIN = "certain"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    UNCERTAIN = "uncertain"
    DISPUTED = "disputed"


class RecommendationStrength(StrEnum):
    """Normative force of a clinical recommendation."""

    NOT_APPLICABLE = "not-applicable"
    OPTIONAL = "optional"
    RECOMMENDED = "recommended"
    REQUIRED = "required"
    CONTRAINDICATED = "contraindicated"


class ReviewStatus(StrEnum):
    """Review state; only verified propositions can pass completeness audit."""

    PENDING = "pending"
    VERIFIED = "verified"
    DISPUTED = "disputed"


class ClinicalFieldType(StrEnum):
    """Structured veterinary fields that must retain their evidence."""

    SPECIES = "species"
    BREED = "breed"
    LIFE_STAGE = "life-stage"
    DISEASE = "disease"
    DIFFERENTIAL_DIAGNOSIS = "differential-diagnosis"
    SIGN = "sign"
    EXAMINATION = "examination"
    DRUG = "drug"
    PROCEDURE = "procedure"
    ROUTE = "route"
    FREQUENCY = "frequency"
    DURATION = "duration"
    CONTRAINDICATION = "contraindication"
    RED_FLAG = "red-flag"
    CLINICAL_CONDITION = "clinical-condition"


class QuantityRole(StrEnum):
    """Clinical meaning carried by an exact numeric expression."""

    DOSE = "dose"
    THRESHOLD = "threshold"
    RANGE = "range"
    INTERVAL = "interval"
    DURATION = "duration"
    FREQUENCY = "frequency"
    RATE = "rate"
    REFERENCE_VALUE = "reference-value"
    MEASUREMENT = "measurement"


class QuantityComparator(StrEnum):
    """Comparison direction preserved independently from the value."""

    EXACT = "exact"
    APPROXIMATE = "approximate"
    LESS_THAN = "less-than"
    LESS_THAN_OR_EQUAL = "less-than-or-equal"
    GREATER_THAN = "greater-than"
    GREATER_THAN_OR_EQUAL = "greater-than-or-equal"
    RANGE = "range"


class NumericBoundaryKind(StrEnum):
    """Structural boundary meaning for exact numeric intervals."""

    INCLUSIVE = "inclusive"
    EXCLUSIVE = "exclusive"
    UNBOUNDED = "unbounded"
    NOT_APPLICABLE = "not_applicable"


class NumericBindingComponentState(StrEnum):
    """Evidence state for one component of a numeric source binding."""

    EXPLICIT = "explicit"
    IMPLICIT = "implicit"
    ABSENT = "absent"


class ClauseUtf8ByteRange(BaseModel):
    """Half-open UTF-8 byte range relative to one canonical source clause."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)

    @model_validator(mode="after")
    def _validate_range(self) -> ClauseUtf8ByteRange:
        if self.end_byte <= self.start_byte:
            raise ValueError("byte range end must be greater than start")
        return self


class NumericImplicitJustification(BaseModel):
    """Traceable rationale for a component that is not explicit in source text."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    justification_id: SafeIdentifier
    source_clause_id: SafeIdentifier
    rationale: _NonBlankText


class NumericSourceComponentBinding(BaseModel):
    """State, selector, and justification for one numeric expression component."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    state: NumericBindingComponentState
    selector: ClauseUtf8ByteRange | None = None
    implicit_justification: NumericImplicitJustification | None = None

    @model_validator(mode="after")
    def _validate_component(self) -> NumericSourceComponentBinding:
        if self.state is NumericBindingComponentState.EXPLICIT:
            if self.selector is None:
                raise ValueError("explicit numeric component requires a selector")
            if self.implicit_justification is not None:
                raise ValueError(
                    "explicit numeric component forbids implicit justification"
                )
        elif self.state is NumericBindingComponentState.IMPLICIT:
            if self.selector is not None:
                raise ValueError("implicit numeric component cannot select source text")
            if self.implicit_justification is None:
                raise ValueError("implicit numeric component requires a justification")
        elif self.selector is not None or self.implicit_justification is not None:
            raise ValueError(
                "absent numeric component forbids selector and justification"
            )
        return self


class NumericSourceBinding(BaseModel):
    """Clause-relative proof for every semantic component of one quantity."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    source_clause_id: SafeIdentifier
    exact_expression: _EvidenceText
    expression_selector: NumericSourceComponentBinding
    lower_value_selector: NumericSourceComponentBinding
    upper_value_selector: NumericSourceComponentBinding
    comparator_selector: NumericSourceComponentBinding
    unit_selector: NumericSourceComponentBinding
    denominator_selector: NumericSourceComponentBinding
    semantic_role_selector: NumericSourceComponentBinding
    lower_boundary_selector: NumericSourceComponentBinding
    upper_boundary_selector: NumericSourceComponentBinding

    @model_validator(mode="after")
    def _validate_binding(self) -> NumericSourceBinding:
        if not self.exact_expression.strip():
            raise ValueError("numeric exact_expression must not be blank")
        if self.expression_selector.state is not NumericBindingComponentState.EXPLICIT:
            raise ValueError("numeric expression selector must be explicit")
        expression_range = self.expression_selector.selector
        if expression_range is None:
            raise ValueError("numeric expression selector is required")

        component_bindings = self._component_bindings()
        for component_name, component in component_bindings.items():
            justification = component.implicit_justification
            if (
                justification is not None
                and justification.source_clause_id != self.source_clause_id
            ):
                raise ValueError(
                    f"{component_name} implicit justification must bind the same clause"
                )
            selector = component.selector
            if selector is not None and not self._contains(expression_range, selector):
                raise ValueError(
                    f"{component_name} selector must be inside expression_selector"
                )

        explicit_components = tuple(
            (name, component.selector)
            for name, component in component_bindings.items()
            if component.selector is not None
        )
        for index, (left_name, left_selector) in enumerate(explicit_components):
            if left_selector is None:
                continue
            for right_name, right_selector in explicit_components[index + 1 :]:
                if right_selector is None or not self._overlaps(
                    left_selector, right_selector
                ):
                    continue
                if self._is_allowed_component_overlap(
                    left_name,
                    left_selector,
                    right_name,
                    right_selector,
                ):
                    continue
                raise ValueError(
                    f"numeric selectors {left_name} and {right_name} overlap"
                )
        return self

    def _component_bindings(self) -> dict[str, NumericSourceComponentBinding]:
        return {
            "lower_value": self.lower_value_selector,
            "upper_value": self.upper_value_selector,
            "comparator": self.comparator_selector,
            "unit": self.unit_selector,
            "denominator": self.denominator_selector,
            "semantic_role": self.semantic_role_selector,
            "lower_boundary": self.lower_boundary_selector,
            "upper_boundary": self.upper_boundary_selector,
        }

    @staticmethod
    def _contains(
        outer: ClauseUtf8ByteRange,
        inner: ClauseUtf8ByteRange,
    ) -> bool:
        return outer.start_byte <= inner.start_byte and inner.end_byte <= outer.end_byte

    @staticmethod
    def _overlaps(
        left: ClauseUtf8ByteRange,
        right: ClauseUtf8ByteRange,
    ) -> bool:
        return left.start_byte < right.end_byte and right.start_byte < left.end_byte

    @classmethod
    def _is_allowed_component_overlap(
        cls,
        left_name: str,
        left: ClauseUtf8ByteRange,
        right_name: str,
        right: ClauseUtf8ByteRange,
    ) -> bool:
        names = {left_name, right_name}
        if names != {"unit", "denominator"}:
            return False
        unit = left if left_name == "unit" else right
        denominator = left if left_name == "denominator" else right
        return cls._contains(unit, denominator)


class DisagreementStatus(StrEnum):
    """Resolution state for conflicting source or evaluator observations."""

    UNRESOLVED = "unresolved"
    HUMAN_RESOLVED = "human-resolved"


class PropositionRelationType(StrEnum):
    """Typed relation between independently traceable propositions."""

    SUPPORTS = "supports"
    CAUSES = "causes"
    CONTRADICTS = "contradicts"
    COMPARES_WITH = "compares-with"
    EXCEPTION_TO = "exception-to"
    CONDITION_FOR = "condition-for"
    CORRECTS = "corrects"


class EvidenceExcerpt(BaseModel):
    """Immutable clause evidence projected into the atomic ledger."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    source_clause_id: SafeIdentifier
    source_span_id: SafeIdentifier
    modality: EvidenceModality
    source_content_sha256: _Digest
    selector: dict[str, JsonValue] = Field(min_length=1)
    generated_observation_identity: dict[str, JsonValue] | None = None
    verbatim_text: _EvidenceText | None = None
    observed_text: _EvidenceText | None = None
    text_sha256: _Digest

    @model_validator(mode="after")
    def _validate_excerpt(self) -> EvidenceExcerpt:
        populated_texts = sum(
            text is not None for text in (self.verbatim_text, self.observed_text)
        )
        if populated_texts != 1:
            raise ValueError(
                "evidence excerpts require exactly one of verbatim_text or "
                "observed_text"
            )
        if self.modality is EvidenceModality.TRANSCRIPT:
            self._validate_transcript()
            text = self.verbatim_text
        elif self.modality is EvidenceModality.OCR:
            self._validate_ocr()
            text = self.observed_text
        else:
            self._validate_visual()
            text = self.observed_text
        if text is None or not text.strip():
            raise ValueError("evidence excerpt text must not be blank")
        observed = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if observed != self.text_sha256:
            raise ValueError("text_sha256 must match evidence excerpt text")
        return self

    def _validate_transcript(self) -> None:
        if self.verbatim_text is None or self.observed_text is not None:
            raise ValueError(
                "transcript evidence requires verbatim_text and forbids observed_text"
            )
        if self.generated_observation_identity is not None:
            raise ValueError("transcript evidence cannot declare a generator identity")
        if self.selector.get("kind") != "text-range":
            raise ValueError("transcript evidence requires a text-range selector")

    def _validate_ocr(self) -> None:
        if self.verbatim_text is not None or self.observed_text is None:
            raise ValueError(
                "OCR evidence requires observed_text and forbids verbatim_text"
            )
        if self.generated_observation_identity is None:
            raise ValueError("OCR evidence requires a generator identity")
        if self.selector.get("kind") != "text-range":
            raise ValueError("OCR evidence requires a text-range selector")

    def _validate_visual(self) -> None:
        if self.verbatim_text is not None or self.observed_text is None:
            raise ValueError(
                "visual evidence requires observed_text and forbids verbatim_text"
            )
        if self.generated_observation_identity is None:
            raise ValueError("visual evidence requires a generator identity")
        if self.selector.get("kind") not in {"visual-full-frame", "visual-region"}:
            raise ValueError("visual evidence requires a full-frame or region selector")


class ClinicalField(BaseModel):
    """One normalized clinical field with exact proposition-local provenance."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    field_type: ClinicalFieldType
    normalized_value: _NonBlankText
    source_span_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_unique_sources(self) -> ClinicalField:
        if len(self.source_span_ids) != len(set(self.source_span_ids)):
            raise ValueError("clinical field source_span_ids must be unique")
        return self


class NumericQuantity(BaseModel):
    """Exact decimal quantity with clause-relative semantic evidence."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    quantity_id: SafeIdentifier
    role: QuantityRole
    comparator: QuantityComparator
    lower_value: _DecimalText
    upper_value: _DecimalText | None = None
    lower_boundary: NumericBoundaryKind
    upper_boundary: NumericBoundaryKind
    unit_literal: _NonBlankText
    unit_normalized: _NonBlankText
    denominator: _NonBlankText | None = None
    source_span_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    source_binding: NumericSourceBinding

    @model_validator(mode="after")
    def _validate_quantity(self) -> NumericQuantity:
        is_range = self.comparator is QuantityComparator.RANGE
        if is_range != (self.upper_value is not None):
            raise ValueError("upper_value is required exactly for range quantities")
        if self.upper_value is not None and Decimal(self.upper_value) < Decimal(
            self.lower_value
        ):
            raise ValueError("range upper_value must not be below lower_value")
        if len(self.source_span_ids) != len(set(self.source_span_ids)):
            raise ValueError("numeric quantity source_span_ids must be unique")

        self._require_semantic_component(
            "lower_value", self.source_binding.lower_value_selector
        )
        self._require_semantic_component(
            "comparator", self.source_binding.comparator_selector
        )
        self._require_semantic_component("unit", self.source_binding.unit_selector)
        self._require_semantic_component(
            "semantic_role", self.source_binding.semantic_role_selector
        )
        self._validate_optional_component(
            "upper_value",
            self.upper_value,
            self.source_binding.upper_value_selector,
        )
        self._validate_optional_component(
            "denominator",
            self.denominator,
            self.source_binding.denominator_selector,
        )
        self._validate_boundaries()
        return self

    @staticmethod
    def _require_semantic_component(
        name: str,
        component: NumericSourceComponentBinding,
    ) -> None:
        if component.state is NumericBindingComponentState.ABSENT:
            raise ValueError(f"numeric {name} component cannot be absent")

    @staticmethod
    def _validate_optional_component(
        name: str,
        value: str | None,
        component: NumericSourceComponentBinding,
    ) -> None:
        component_is_absent = component.state is NumericBindingComponentState.ABSENT
        if (value is None) != component_is_absent:
            raise ValueError(
                f"numeric {name} value and component state must agree on absence"
            )

    def _validate_boundaries(self) -> None:
        if self.comparator is QuantityComparator.RANGE:
            self._validate_finite_range_boundaries()
            return
        unilateral_boundaries = {
            QuantityComparator.LESS_THAN: (
                NumericBoundaryKind.UNBOUNDED,
                NumericBoundaryKind.EXCLUSIVE,
            ),
            QuantityComparator.LESS_THAN_OR_EQUAL: (
                NumericBoundaryKind.UNBOUNDED,
                NumericBoundaryKind.INCLUSIVE,
            ),
            QuantityComparator.GREATER_THAN: (
                NumericBoundaryKind.EXCLUSIVE,
                NumericBoundaryKind.UNBOUNDED,
            ),
            QuantityComparator.GREATER_THAN_OR_EQUAL: (
                NumericBoundaryKind.INCLUSIVE,
                NumericBoundaryKind.UNBOUNDED,
            ),
        }
        expected = unilateral_boundaries.get(self.comparator)
        if expected is not None:
            if (self.lower_boundary, self.upper_boundary) != expected:
                raise ValueError(
                    "unilateral comparator boundaries do not preserve direction"
                )
            self._require_absent_boundary_selectors()
            return
        if (
            self.lower_boundary is not NumericBoundaryKind.NOT_APPLICABLE
            or self.upper_boundary is not NumericBoundaryKind.NOT_APPLICABLE
        ):
            raise ValueError(
                "exact and approximate quantities require not_applicable boundaries"
            )
        self._require_absent_boundary_selectors()

    def _validate_finite_range_boundaries(self) -> None:
        allowed = {
            NumericBoundaryKind.INCLUSIVE,
            NumericBoundaryKind.EXCLUSIVE,
        }
        if self.lower_boundary not in allowed or self.upper_boundary not in allowed:
            raise ValueError(
                "finite range quantities require inclusive or exclusive boundaries"
            )
        for name, component in (
            ("lower_boundary", self.source_binding.lower_boundary_selector),
            ("upper_boundary", self.source_binding.upper_boundary_selector),
        ):
            if component.state is NumericBindingComponentState.ABSENT:
                raise ValueError(f"range {name} component cannot be absent")

    def _require_absent_boundary_selectors(self) -> None:
        for name, component in (
            ("lower_boundary", self.source_binding.lower_boundary_selector),
            ("upper_boundary", self.source_binding.upper_boundary_selector),
        ):
            if component.state is not NumericBindingComponentState.ABSENT:
                raise ValueError(f"non-range {name} selector component must be absent")


class EvidenceDisagreement(BaseModel):
    """Preserved conflict that cannot be silently collapsed during extraction."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    disagreement_id: SafeIdentifier
    description: _NonBlankText
    source_span_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    status: DisagreementStatus
    chosen_interpretation: _NonBlankText | None = None
    review_artifact_id: SafeIdentifier | None = None

    @model_validator(mode="after")
    def _validate_resolution(self) -> EvidenceDisagreement:
        if len(self.source_span_ids) != len(set(self.source_span_ids)):
            raise ValueError("disagreement source_span_ids must be unique")
        resolution_fields = (
            self.chosen_interpretation,
            self.review_artifact_id,
        )
        if self.status is DisagreementStatus.UNRESOLVED and any(
            value is not None for value in resolution_fields
        ):
            raise ValueError("unresolved disagreements cannot declare a resolution")
        if self.status is DisagreementStatus.HUMAN_RESOLVED and any(
            value is None for value in resolution_fields
        ):
            raise ValueError(
                "human-resolved disagreements require an interpretation and review "
                "artifact"
            )
        return self


class AtomicProposition(BaseModel):
    """One independently traceable educational assertion."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    proposition_id: SafeIdentifier
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    sequence: PersistedSequenceNumber
    content_type: AtomicContentType
    importance: ContentImportance
    normalized_statement: _NonBlankText
    source_span_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    evidence_excerpts: tuple[EvidenceExcerpt, ...] = Field(min_length=1)
    clinical_fields: tuple[ClinicalField, ...] = ()
    numeric_quantities: tuple[NumericQuantity, ...] = ()
    required_dimensions: frozenset[ContentDimension] = Field(min_length=1)
    polarity: AssertionPolarity
    certainty: CertaintyLevel
    recommendation_strength: RecommendationStrength
    safety_critical: bool
    extraction_confidence: _Confidence
    disagreements: tuple[EvidenceDisagreement, ...] = ()
    review_status: ReviewStatus

    @model_validator(mode="after")
    def _validate_proposition(self) -> AtomicProposition:
        source_ids = set(self.source_span_ids)
        if len(source_ids) != len(self.source_span_ids):
            raise ValueError("source_span_ids must be unique")
        excerpt_clause_ids = tuple(
            excerpt.source_clause_id for excerpt in self.evidence_excerpts
        )
        if len(excerpt_clause_ids) != len(set(excerpt_clause_ids)):
            raise ValueError(
                "evidence excerpt clause IDs must be unique per proposition"
            )
        excerpt_span_ids = {
            excerpt.source_span_id for excerpt in self.evidence_excerpts
        }
        if excerpt_span_ids != source_ids:
            raise ValueError(
                "evidence excerpts must cover every declared source span and no others"
            )
        clinical_source_ids = (
            span_id
            for field in self.clinical_fields
            for span_id in field.source_span_ids
        )
        quantity_source_ids = (
            span_id
            for quantity in self.numeric_quantities
            for span_id in quantity.source_span_ids
        )
        if any(
            span_id not in source_ids
            for span_id in (*clinical_source_ids, *quantity_source_ids)
        ):
            raise ValueError(
                "structured fields must reference proposition source spans"
            )
        disagreement_source_ids = (
            span_id
            for disagreement in self.disagreements
            for span_id in disagreement.source_span_ids
        )
        if any(span_id not in source_ids for span_id in disagreement_source_ids):
            raise ValueError("disagreements must reference proposition source spans")
        quantity_ids = tuple(
            quantity.quantity_id for quantity in self.numeric_quantities
        )
        if len(quantity_ids) != len(set(quantity_ids)):
            raise ValueError("numeric quantity IDs must be unique per proposition")
        disagreement_ids = tuple(
            disagreement.disagreement_id for disagreement in self.disagreements
        )
        if len(disagreement_ids) != len(set(disagreement_ids)):
            raise ValueError("disagreement IDs must be unique per proposition")
        if ContentDimension.CORE_STATEMENT not in self.required_dimensions:
            raise ValueError("required_dimensions must include core_statement")
        critical_types = {
            AtomicContentType.DOSE,
            AtomicContentType.CONTRAINDICATION,
            AtomicContentType.RED_FLAG,
        }
        if self.content_type in critical_types and not self.safety_critical:
            raise ValueError("dose, contraindication, and red-flag content is critical")
        if (
            self.safety_critical
            and ContentDimension.SAFETY not in self.required_dimensions
        ):
            raise ValueError(
                "safety-critical content must require the safety dimension"
            )
        if self.content_type is AtomicContentType.DOSE and not any(
            quantity.role is QuantityRole.DOSE for quantity in self.numeric_quantities
        ):
            raise ValueError("dose content requires a dose-role numeric quantity")
        if (
            self.content_type is AtomicContentType.UNCERTAINTY
            and self.certainty is CertaintyLevel.CERTAIN
        ):
            raise ValueError("uncertainty content cannot declare certain certainty")
        return self


class PropositionRelation(BaseModel):
    """Typed edge between two propositions in the lecture inventory."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    relation_id: SafeIdentifier
    source_proposition_id: SafeIdentifier
    relation_type: PropositionRelationType
    target_proposition_id: SafeIdentifier
    source_span_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _reject_self_relation(self) -> PropositionRelation:
        if self.source_proposition_id == self.target_proposition_id:
            raise ValueError("a proposition relation cannot target itself")
        if len(self.source_span_ids) != len(set(self.source_span_ids)):
            raise ValueError("relation source_span_ids must be unique")
        return self


class AtomicContentLedger(BaseModel):
    """Complete atomic inventory for one conceptual Learning Unit."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    source_universe_sha256: _Digest
    propositions: tuple[AtomicProposition, ...] = Field(min_length=1)
    relations: tuple[PropositionRelation, ...] = ()
