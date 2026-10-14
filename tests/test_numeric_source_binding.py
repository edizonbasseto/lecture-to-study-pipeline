from __future__ import annotations

import hashlib
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from lecture_study.domain.content_ledger import (
    ClauseUtf8ByteRange,
    EvidenceExcerpt,
    NumericBindingComponentState,
    NumericBoundaryKind,
    NumericImplicitJustification,
    NumericQuantity,
    NumericSourceBinding,
    NumericSourceComponentBinding,
    QuantityComparator,
    QuantityRole,
)
from lecture_study.domain.semantic_observation import (
    GeneratedObservationIdentity,
    SourceClause,
    SourcePartitionKind,
    TextPartitionSelector,
    VisualFullFrameSelector,
)
from lecture_study.domain.source_universe import EvidenceModality
from lecture_study.quality.numeric_source_binding import (
    NumericBindingDisposition,
    NumericBindingScopeState,
    NumericSourceBindingAuditReport,
    NumericSourceBindingAuditor,
)

_SOURCE_CONTENT_SHA256 = "a" * 64
_OBSERVATION_IMPLEMENTATION_SHA256 = "b" * 64
_OBSERVATION_CONFIGURATION_SHA256 = "c" * 64


def _byte_range(
    text: str,
    fragment: str,
    *,
    occurrence: int = 1,
) -> ClauseUtf8ByteRange:
    character_start = -1
    search_start = 0
    for _ in range(occurrence):
        character_start = text.index(fragment, search_start)
        search_start = character_start + len(fragment)
    start_byte = len(text[:character_start].encode("utf-8"))
    return ClauseUtf8ByteRange(
        start_byte=start_byte,
        end_byte=start_byte + len(fragment.encode("utf-8")),
    )


def _byte_range_within(
    text: str,
    outer: ClauseUtf8ByteRange,
    fragment: str,
    *,
    occurrence: int = 1,
) -> ClauseUtf8ByteRange:
    text_bytes = text.encode("utf-8")
    selected_text = text_bytes[outer.start_byte : outer.end_byte].decode("utf-8")
    relative = _byte_range(selected_text, fragment, occurrence=occurrence)
    return ClauseUtf8ByteRange(
        start_byte=outer.start_byte + relative.start_byte,
        end_byte=outer.start_byte + relative.end_byte,
    )


def _explicit(selector: ClauseUtf8ByteRange) -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(
        state=NumericBindingComponentState.EXPLICIT,
        selector=selector,
    )


def _absent() -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(state=NumericBindingComponentState.ABSENT)


def _implicit(
    component_name: str,
    *,
    source_clause_id: str = "clause-001",
) -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(
        state=NumericBindingComponentState.IMPLICIT,
        implicit_justification=NumericImplicitJustification(
            justification_id=f"justification-{component_name}",
            source_clause_id=source_clause_id,
            rationale=f"The {component_name} component requires authenticated review.",
        ),
    )


def _component_inside_expression(
    text: str,
    expression_selector: ClauseUtf8ByteRange,
    fragment: str | None,
    component_name: str,
    *,
    absent_when_none: bool = False,
    occurrence: int = 1,
) -> NumericSourceComponentBinding:
    if fragment is None:
        return _absent() if absent_when_none else _implicit(component_name)
    return _explicit(
        _byte_range_within(
            text,
            expression_selector,
            fragment,
            occurrence=occurrence,
        )
    )


def _source_binding(
    *,
    text: str,
    expression: str,
    lower_fragment: str | None,
    comparator_fragment: str | None,
    unit_fragment: str | None,
    semantic_role_fragment: str | None,
    upper_fragment: str | None = None,
    denominator_fragment: str | None = None,
    lower_boundary_fragment: str | None = None,
    upper_boundary_fragment: str | None = None,
    upper_is_absent: bool = True,
    denominator_is_absent: bool = True,
    boundaries_are_absent: bool = True,
    expression_occurrence: int = 1,
    lower_occurrence: int = 1,
    upper_occurrence: int = 1,
    unit_occurrence: int = 1,
    source_clause_id: str = "clause-001",
) -> NumericSourceBinding:
    requested_expression_selector = _byte_range(
        text,
        expression,
        occurrence=expression_occurrence,
    )
    expression_selector = requested_expression_selector
    if semantic_role_fragment is not None:
        role_selector = _byte_range(text, semantic_role_fragment)
        expression_selector = ClauseUtf8ByteRange(
            start_byte=min(
                requested_expression_selector.start_byte,
                role_selector.start_byte,
            ),
            end_byte=max(
                requested_expression_selector.end_byte,
                role_selector.end_byte,
            ),
        )
    source_bytes = text.encode("utf-8")
    exact_expression = source_bytes[
        expression_selector.start_byte : expression_selector.end_byte
    ].decode("utf-8")
    role_component = _component_inside_expression(
        text,
        expression_selector,
        semantic_role_fragment,
        "semantic-role",
    )
    return NumericSourceBinding(
        source_clause_id=source_clause_id,
        exact_expression=exact_expression,
        expression_selector=_explicit(expression_selector),
        lower_value_selector=_component_inside_expression(
            text,
            expression_selector,
            lower_fragment,
            "lower-value",
            occurrence=lower_occurrence,
        ),
        upper_value_selector=_component_inside_expression(
            text,
            expression_selector,
            upper_fragment,
            "upper-value",
            absent_when_none=upper_is_absent,
            occurrence=upper_occurrence,
        ),
        comparator_selector=_component_inside_expression(
            text,
            expression_selector,
            comparator_fragment,
            "comparator",
        ),
        unit_selector=_component_inside_expression(
            text,
            expression_selector,
            unit_fragment,
            "unit",
            occurrence=unit_occurrence,
        ),
        denominator_selector=_component_inside_expression(
            text,
            expression_selector,
            denominator_fragment,
            "denominator",
            absent_when_none=denominator_is_absent,
        ),
        semantic_role_selector=role_component,
        lower_boundary_selector=_component_inside_expression(
            text,
            expression_selector,
            lower_boundary_fragment,
            "lower-boundary",
            absent_when_none=boundaries_are_absent,
        ),
        upper_boundary_selector=_component_inside_expression(
            text,
            expression_selector,
            upper_boundary_fragment,
            "upper-boundary",
            absent_when_none=boundaries_are_absent,
        ),
    )


def _quantity(
    *,
    text: str = "Give exactly 10 mg/kg dose.",
    expression: str = "Give exactly 10 mg/kg",
    role: QuantityRole = QuantityRole.DOSE,
    comparator: QuantityComparator = QuantityComparator.EXACT,
    lower_value: str = "10",
    upper_value: str | None = None,
    lower_boundary: NumericBoundaryKind = NumericBoundaryKind.NOT_APPLICABLE,
    upper_boundary: NumericBoundaryKind = NumericBoundaryKind.NOT_APPLICABLE,
    unit_literal: str = "mg/kg",
    unit_normalized: str | None = None,
    denominator: str | None = "kg",
    lower_fragment: str | None = "10",
    upper_fragment: str | None = None,
    comparator_fragment: str | None = "exactly",
    unit_fragment: str | None = "mg/kg",
    denominator_fragment: str | None = "kg",
    semantic_role_fragment: str | None = "Give",
    lower_boundary_fragment: str | None = None,
    upper_boundary_fragment: str | None = None,
    expression_occurrence: int = 1,
    lower_occurrence: int = 1,
    upper_occurrence: int = 1,
    unit_occurrence: int = 1,
    source_clause_id: str = "clause-001",
    source_span_ids: tuple[str, ...] = ("span-001",),
) -> NumericQuantity:
    source_binding = _source_binding(
        text=text,
        expression=expression,
        lower_fragment=lower_fragment,
        upper_fragment=upper_fragment,
        comparator_fragment=comparator_fragment,
        unit_fragment=unit_fragment,
        denominator_fragment=denominator_fragment,
        semantic_role_fragment=semantic_role_fragment,
        lower_boundary_fragment=lower_boundary_fragment,
        upper_boundary_fragment=upper_boundary_fragment,
        upper_is_absent=upper_value is None,
        denominator_is_absent=denominator is None,
        boundaries_are_absent=comparator is not QuantityComparator.RANGE,
        expression_occurrence=expression_occurrence,
        lower_occurrence=lower_occurrence,
        upper_occurrence=upper_occurrence,
        unit_occurrence=unit_occurrence,
        source_clause_id=source_clause_id,
    )
    return NumericQuantity(
        quantity_id="quantity-001",
        role=role,
        comparator=comparator,
        lower_value=lower_value,
        upper_value=upper_value,
        lower_boundary=lower_boundary,
        upper_boundary=upper_boundary,
        unit_literal=unit_literal,
        unit_normalized=unit_normalized or unit_literal,
        denominator=denominator,
        source_span_ids=source_span_ids,
        source_binding=source_binding,
    )


def _range_quantity(
    *,
    left: str = "[",
    right: str = "]",
    lower_value: str = "10",
    upper_value: str = "20",
    structured_lower_value: str | None = None,
    structured_upper_value: str | None = None,
    lower_boundary: NumericBoundaryKind = NumericBoundaryKind.INCLUSIVE,
    upper_boundary: NumericBoundaryKind = NumericBoundaryKind.INCLUSIVE,
) -> tuple[str, NumericQuantity]:
    text = f"Range {left}{lower_value}, {upper_value}{right} mg."
    expression = f"{left}{lower_value}, {upper_value}{right} mg"
    return (
        text,
        _quantity(
            text=text,
            expression=expression,
            role=QuantityRole.RANGE,
            comparator=QuantityComparator.RANGE,
            lower_value=structured_lower_value or lower_value,
            upper_value=structured_upper_value or upper_value,
            lower_boundary=lower_boundary,
            upper_boundary=upper_boundary,
            unit_literal="mg",
            denominator=None,
            lower_fragment=lower_value,
            upper_fragment=upper_value,
            comparator_fragment=",",
            unit_fragment="mg",
            denominator_fragment=None,
            semantic_role_fragment="Range",
            lower_boundary_fragment=left,
            upper_boundary_fragment=right,
        ),
    )


def _observation_identity() -> GeneratedObservationIdentity:
    return GeneratedObservationIdentity(
        implementation_sha256=_OBSERVATION_IMPLEMENTATION_SHA256,
        configuration_sha256=_OBSERVATION_CONFIGURATION_SHA256,
    )


def _clause(
    text: str,
    *,
    modality: EvidenceModality = EvidenceModality.TRANSCRIPT,
    source_clause_id: str = "clause-001",
    source_span_id: str = "span-001",
    source_content_sha256: str = _SOURCE_CONTENT_SHA256,
) -> SourceClause:
    text_bytes = text.encode("utf-8")
    text_sha256 = hashlib.sha256(text_bytes).hexdigest()
    generated_identity = (
        None if modality is EvidenceModality.TRANSCRIPT else _observation_identity()
    )
    selector = (
        VisualFullFrameSelector(
            kind=SourcePartitionKind.VISUAL_FULL_FRAME,
            original_frame_sha256=source_content_sha256,
        )
        if modality is EvidenceModality.VISUAL
        else TextPartitionSelector(
            kind=SourcePartitionKind.TEXT_RANGE,
            start_byte=0,
            end_byte=len(text_bytes),
            selected_sha256=text_sha256,
        )
    )
    return SourceClause(
        schema_version=1,
        clause_id=source_clause_id,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        sequence=1,
        source_span_id=source_span_id,
        source_content_sha256=source_content_sha256,
        modality=modality,
        selector=selector,
        generated_observation_identity=generated_identity,
        verbatim_text=text if modality is EvidenceModality.TRANSCRIPT else None,
        observed_text=text if modality is not EvidenceModality.TRANSCRIPT else None,
        text_sha256=text_sha256,
    )


def _excerpt(clause: SourceClause) -> EvidenceExcerpt:
    return EvidenceExcerpt(
        source_clause_id=clause.clause_id,
        source_span_id=clause.source_span_id,
        modality=clause.modality,
        source_content_sha256=clause.source_content_sha256,
        selector=clause.selector.model_dump(mode="json", warnings=False),
        generated_observation_identity=(
            clause.generated_observation_identity.model_dump(
                mode="json",
                warnings=False,
            )
            if clause.generated_observation_identity is not None
            else None
        ),
        verbatim_text=clause.verbatim_text,
        observed_text=clause.observed_text,
        text_sha256=clause.text_sha256,
    )


def _audit(
    quantity: NumericQuantity,
    *,
    text: str = "Give exactly 10 mg/kg dose.",
    modality: EvidenceModality = EvidenceModality.TRANSCRIPT,
    clause: SourceClause | None = None,
    evidence_excerpts: Sequence[EvidenceExcerpt] | None = None,
    normalized_statement: str | None = None,
) -> NumericSourceBindingAuditReport:
    source_clause = clause or _clause(text, modality=modality)
    excerpts = (
        tuple(evidence_excerpts)
        if evidence_excerpts is not None
        else (_excerpt(source_clause),)
    )
    return NumericSourceBindingAuditor().audit(
        quantity=quantity,
        clauses_by_id={str(source_clause.clause_id): source_clause},
        evidence_excerpts=excerpts,
        normalized_statement=normalized_statement or text,
    )


def _violation_codes(report: NumericSourceBindingAuditReport) -> set[str]:
    return {violation.code for violation in report.violations}


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (
            {"state": NumericBindingComponentState.EXPLICIT},
            "requires a selector",
        ),
        (
            {
                "state": NumericBindingComponentState.EXPLICIT,
                "selector": ClauseUtf8ByteRange(start_byte=0, end_byte=1),
                "implicit_justification": NumericImplicitJustification(
                    justification_id="justification-explicit",
                    source_clause_id="clause-001",
                    rationale="This must be forbidden for explicit evidence.",
                ),
            },
            "forbids implicit justification",
        ),
        (
            {
                "state": NumericBindingComponentState.IMPLICIT,
                "selector": ClauseUtf8ByteRange(start_byte=0, end_byte=1),
                "implicit_justification": NumericImplicitJustification(
                    justification_id="justification-implicit-selector",
                    source_clause_id="clause-001",
                    rationale="Implicit evidence cannot select source bytes.",
                ),
            },
            "cannot select source text",
        ),
        (
            {"state": NumericBindingComponentState.IMPLICIT},
            "requires a justification",
        ),
        (
            {
                "state": NumericBindingComponentState.ABSENT,
                "selector": ClauseUtf8ByteRange(start_byte=0, end_byte=1),
            },
            "forbids selector and justification",
        ),
    ),
)
def test_component_state_contract_rejects_inconsistent_evidence(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        NumericSourceComponentBinding.model_validate(payload)


def test_component_selectors_use_nonempty_half_open_ranges() -> None:
    with pytest.raises(ValidationError):
        ClauseUtf8ByteRange(start_byte=1, end_byte=1)
    with pytest.raises(ValidationError):
        ClauseUtf8ByteRange(start_byte=2, end_byte=1)

    selected = _byte_range("Dose 10 mg.", "10")

    assert (selected.start_byte, selected.end_byte) == (5, 7)


def test_binding_rejects_implicit_justification_for_another_clause() -> None:
    quantity = _quantity()
    foreign_justification = _implicit(
        "comparator",
        source_clause_id="clause-foreign",
    )

    with pytest.raises(ValidationError, match="same clause"):
        NumericSourceBinding.model_validate(
            {
                **quantity.source_binding.model_dump(mode="python"),
                "comparator_selector": foreign_justification.model_dump(mode="python"),
            }
        )


def test_binding_rejects_avoidable_component_overlap() -> None:
    quantity = _quantity()
    overlapping_unit = _explicit(
        quantity.source_binding.lower_value_selector.selector
        or ClauseUtf8ByteRange(start_byte=0, end_byte=1)
    )

    with pytest.raises(ValidationError, match="overlap"):
        NumericSourceBinding.model_validate(
            {
                **quantity.source_binding.model_dump(mode="python"),
                "unit_selector": overlapping_unit.model_dump(mode="python"),
            }
        )


def test_required_comparator_component_cannot_be_marked_absent() -> None:
    quantity = _quantity()
    absent_comparator = quantity.source_binding.model_copy(
        update={"comparator_selector": _absent()}
    )

    with pytest.raises(ValidationError, match="comparator component cannot be absent"):
        NumericQuantity.model_validate(
            {
                **quantity.model_dump(mode="python"),
                "source_binding": absent_comparator.model_dump(mode="python"),
            }
        )


def test_unit_and_denominator_overlap_is_validated_as_inevitable() -> None:
    quantity = _quantity()

    report = _audit(quantity)

    assert report.disposition is NumericBindingDisposition.VERIFIED


def test_verified_transcript_binding_is_reconciliation_only() -> None:
    report = _audit(_quantity())

    assert report.disposition is NumericBindingDisposition.VERIFIED
    assert report.state is NumericBindingScopeState.SATISFIED
    assert report.ready_for_reconciliation
    assert not report.ready_for_certification
    assert not report.ready_for_publication
    assert not report.ready_for_derivatives


@pytest.mark.parametrize(
    "modality",
    (EvidenceModality.OCR, EvidenceModality.VISUAL),
)
def test_observed_modalities_preserve_observed_not_verbatim_evidence(
    modality: EvidenceModality,
) -> None:
    text = "Give exactly 10 mg/kg dose."
    clause = _clause(text, modality=modality)

    report = _audit(_quantity(), text=text, modality=modality, clause=clause)

    assert clause.observed_text == text
    assert clause.verbatim_text is None
    assert clause.generated_observation_identity is not None
    assert report.disposition is NumericBindingDisposition.VERIFIED


def test_implicit_component_is_unverifiable_and_needs_review() -> None:
    quantity = _quantity(comparator_fragment=None)

    report = _audit(quantity)

    assert report.disposition is NumericBindingDisposition.UNVERIFIABLE
    assert report.state is NumericBindingScopeState.NEEDS_REVIEW
    assert "numeric_component_implicit" in _violation_codes(report)
    assert not report.ready_for_reconciliation
    assert not report.ready_for_certification
    assert not report.ready_for_publication
    assert not report.ready_for_derivatives


@pytest.mark.parametrize(
    ("mutator", "expected_code"),
    (
        (
            lambda quantity: quantity.model_copy(
                update={"source_span_ids": ("span-foreign",)}
            ),
            "numeric_binding_span_mismatch",
        ),
        (
            lambda quantity: quantity.model_copy(
                update={
                    "source_binding": quantity.source_binding.model_copy(
                        update={"source_clause_id": "clause-foreign"}
                    )
                }
            ),
            "numeric_binding_clause_missing",
        ),
    ),
)
def test_wrong_clause_or_span_identity_is_quarantined(
    mutator: object,
    expected_code: str,
) -> None:
    quantity = mutator(_quantity())  # type: ignore[operator]

    report = _audit(quantity)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert report.state is NumericBindingScopeState.QUARANTINE
    assert expected_code in _violation_codes(report)


def test_missing_or_duplicate_evidence_excerpt_is_quarantined() -> None:
    quantity = _quantity()
    clause = _clause("Give exactly 10 mg/kg dose.")
    excerpt = _excerpt(clause)

    missing = _audit(quantity, clause=clause, evidence_excerpts=())
    duplicate = _audit(
        quantity,
        clause=clause,
        evidence_excerpts=(excerpt, excerpt),
    )

    assert missing.disposition is NumericBindingDisposition.INVALID
    assert duplicate.disposition is NumericBindingDisposition.INVALID
    assert "numeric_binding_evidence_cardinality" in _violation_codes(missing)
    assert "numeric_binding_evidence_cardinality" in _violation_codes(duplicate)


@pytest.mark.parametrize(
    "excerpt_update",
    (
        {"source_content_sha256": "d" * 64},
        {"modality": EvidenceModality.OCR},
        {
            "generated_observation_identity": {
                "implementation_sha256": "e" * 64,
                "configuration_sha256": "f" * 64,
            }
        },
        {
            "selector": {
                "kind": SourcePartitionKind.TEXT_RANGE,
                "start_byte": 1,
                "end_byte": 29,
                "selected_sha256": "1" * 64,
            }
        },
    ),
)
def test_substituted_hash_modality_generator_or_selector_is_quarantined(
    excerpt_update: dict[str, object],
) -> None:
    quantity = _quantity()
    clause = _clause("Give exactly 10 mg/kg dose.")
    substituted = _excerpt(clause).model_copy(update=excerpt_update)

    report = _audit(quantity, clause=clause, evidence_excerpts=(substituted,))

    assert report.disposition is NumericBindingDisposition.INVALID
    assert report.state is NumericBindingScopeState.QUARANTINE
    assert "numeric_binding_evidence_identity_mismatch" in _violation_codes(report)


def test_out_of_bounds_selector_is_quarantined() -> None:
    quantity = _quantity()
    invalid_expression = _explicit(ClauseUtf8ByteRange(start_byte=0, end_byte=500))
    mutated_binding = quantity.source_binding.model_copy(
        update={"expression_selector": invalid_expression}
    )

    report = _audit(quantity.model_copy(update={"source_binding": mutated_binding}))

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_selector_out_of_bounds" in _violation_codes(report)


def test_expression_selector_bytes_must_equal_exact_expression() -> None:
    quantity = _quantity()
    mutated_binding = quantity.source_binding.model_copy(
        update={"exact_expression": "exactly 10 mL"}
    )

    report = _audit(quantity.model_copy(update={"source_binding": mutated_binding}))

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_expression_mismatch" in _violation_codes(report)


def test_selector_that_splits_utf8_code_point_is_quarantined() -> None:
    text = "Give exactly 10 mg dose café."
    quantity = _quantity(
        text=text,
        expression="exactly 10 mg",
        unit_literal="mg",
        denominator=None,
        unit_fragment="mg",
        denominator_fragment=None,
    )
    cafe = _byte_range(text, "café")
    split_utf8 = _explicit(
        ClauseUtf8ByteRange(
            start_byte=cafe.end_byte - 1,
            end_byte=cafe.end_byte,
        )
    )
    mutated_binding = quantity.source_binding.model_copy(
        update={"expression_selector": split_utf8}
    )

    report = _audit(
        quantity.model_copy(update={"source_binding": mutated_binding}),
        text=text,
    )

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_selector_splits_utf8" in _violation_codes(report)


@pytest.mark.parametrize(
    ("quantity_update", "expected_disposition", "expected_code"),
    (
        (
            {"lower_value": "11"},
            NumericBindingDisposition.INVALID,
            "numeric_decimal_value_mismatch",
        ),
        (
            {"unit_literal": "mL", "unit_normalized": "mL"},
            NumericBindingDisposition.INVALID,
            "numeric_unit_mismatch",
        ),
        (
            {"denominator": "lb"},
            NumericBindingDisposition.INVALID,
            "numeric_denominator_mismatch",
        ),
        (
            {"unit_normalized": "g/kg"},
            NumericBindingDisposition.UNVERIFIABLE,
            "numeric_unit_normalization_unproven",
        ),
        (
            {
                "comparator": QuantityComparator.LESS_THAN,
                "lower_boundary": NumericBoundaryKind.UNBOUNDED,
                "upper_boundary": NumericBoundaryKind.EXCLUSIVE,
            },
            NumericBindingDisposition.INVALID,
            "numeric_comparator_mismatch",
        ),
        (
            {"role": QuantityRole.DURATION},
            NumericBindingDisposition.INVALID,
            "numeric_semantic_role_mismatch",
        ),
        (
            {"lower_value": "0.01"},
            NumericBindingDisposition.INVALID,
            "numeric_decimal_value_mismatch",
        ),
    ),
)
def test_numeric_semantic_mutations_never_verify(
    quantity_update: dict[str, object],
    expected_disposition: NumericBindingDisposition,
    expected_code: str,
) -> None:
    quantity = _quantity().model_copy(update=quantity_update)

    report = _audit(quantity)

    assert report.disposition is expected_disposition
    assert expected_code in _violation_codes(report)
    assert not report.ready_for_reconciliation
    assert not report.ready_for_publication


def test_removed_denominator_that_is_explicit_in_source_never_verifies() -> None:
    text = "Give exactly 10 mg/kg dose."
    quantity = _quantity(
        text=text,
        unit_literal="mg/kg",
        denominator=None,
        denominator_fragment=None,
    )

    report = _audit(quantity, text=text)

    assert report.disposition is not NumericBindingDisposition.VERIFIED
    assert not report.ready_for_reconciliation


def test_overlapping_truncated_expression_cannot_hide_denominator() -> None:
    text = "Give exactly 10 mg/kg dose."
    quantity = _quantity(
        text=text,
        expression="exactly 10 mg",
        unit_literal="mg",
        denominator=None,
        unit_fragment="mg",
        denominator_fragment=None,
    )

    report = _audit(quantity, text=text)

    assert report.disposition is not NumericBindingDisposition.VERIFIED
    assert not report.ready_for_reconciliation


def test_source_expression_missing_from_statement_requires_review() -> None:
    quantity = _quantity()

    report = _audit(
        quantity,
        normalized_statement="Give the prescribed dose.",
    )

    assert report.disposition is NumericBindingDisposition.UNVERIFIABLE
    assert report.state is NumericBindingScopeState.NEEDS_REVIEW
    assert "numeric_statement_context_transformation_unproven" in _violation_codes(
        report
    )


@pytest.mark.parametrize(
    (
        "comparator",
        "comparator_fragment",
        "lower_boundary",
        "upper_boundary",
    ),
    (
        (
            QuantityComparator.LESS_THAN,
            "<",
            NumericBoundaryKind.UNBOUNDED,
            NumericBoundaryKind.EXCLUSIVE,
        ),
        (
            QuantityComparator.LESS_THAN_OR_EQUAL,
            "<=",
            NumericBoundaryKind.UNBOUNDED,
            NumericBoundaryKind.INCLUSIVE,
        ),
        (
            QuantityComparator.GREATER_THAN,
            ">",
            NumericBoundaryKind.EXCLUSIVE,
            NumericBoundaryKind.UNBOUNDED,
        ),
        (
            QuantityComparator.GREATER_THAN_OR_EQUAL,
            ">=",
            NumericBoundaryKind.INCLUSIVE,
            NumericBoundaryKind.UNBOUNDED,
        ),
    ),
)
def test_unilateral_comparators_preserve_direction_and_unbounded_side(
    comparator: QuantityComparator,
    comparator_fragment: str,
    lower_boundary: NumericBoundaryKind,
    upper_boundary: NumericBoundaryKind,
) -> None:
    text = f"Threshold {comparator_fragment} 10 mg."
    expression = f"{comparator_fragment} 10 mg"
    quantity = _quantity(
        text=text,
        expression=expression,
        role=QuantityRole.THRESHOLD,
        comparator=comparator,
        lower_boundary=lower_boundary,
        upper_boundary=upper_boundary,
        unit_literal="mg",
        denominator=None,
        comparator_fragment=comparator_fragment,
        unit_fragment="mg",
        denominator_fragment=None,
        semantic_role_fragment="Threshold",
    )

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.VERIFIED


def test_unilateral_comparator_rejects_reversed_boundary_direction() -> None:
    text = "Threshold > 10 mg."

    with pytest.raises(ValidationError, match="preserve direction"):
        _quantity(
            text=text,
            expression="> 10 mg",
            role=QuantityRole.THRESHOLD,
            comparator=QuantityComparator.GREATER_THAN,
            lower_boundary=NumericBoundaryKind.UNBOUNDED,
            upper_boundary=NumericBoundaryKind.EXCLUSIVE,
            unit_literal="mg",
            denominator=None,
            comparator_fragment=">",
            unit_fragment="mg",
            denominator_fragment=None,
            semantic_role_fragment="Threshold",
        )


@pytest.mark.parametrize(
    (
        "source_comparator",
        "mutated_comparator",
        "mutated_lower_boundary",
        "mutated_upper_boundary",
    ),
    (
        (
            ">",
            QuantityComparator.LESS_THAN,
            NumericBoundaryKind.UNBOUNDED,
            NumericBoundaryKind.EXCLUSIVE,
        ),
        (
            ">=",
            QuantityComparator.LESS_THAN_OR_EQUAL,
            NumericBoundaryKind.UNBOUNDED,
            NumericBoundaryKind.INCLUSIVE,
        ),
    ),
)
def test_reversed_comparison_direction_is_quarantined(
    source_comparator: str,
    mutated_comparator: QuantityComparator,
    mutated_lower_boundary: NumericBoundaryKind,
    mutated_upper_boundary: NumericBoundaryKind,
) -> None:
    text = f"Threshold {source_comparator} 10 mg."
    quantity = _quantity(
        text=text,
        expression=f"{source_comparator} 10 mg",
        role=QuantityRole.THRESHOLD,
        comparator=(
            QuantityComparator.GREATER_THAN
            if source_comparator == ">"
            else QuantityComparator.GREATER_THAN_OR_EQUAL
        ),
        lower_boundary=(
            NumericBoundaryKind.EXCLUSIVE
            if source_comparator == ">"
            else NumericBoundaryKind.INCLUSIVE
        ),
        upper_boundary=NumericBoundaryKind.UNBOUNDED,
        unit_literal="mg",
        denominator=None,
        comparator_fragment=source_comparator,
        unit_fragment="mg",
        denominator_fragment=None,
        semantic_role_fragment="Threshold",
    ).model_copy(
        update={
            "comparator": mutated_comparator,
            "lower_boundary": mutated_lower_boundary,
            "upper_boundary": mutated_upper_boundary,
        }
    )

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_comparator_mismatch" in _violation_codes(report)


@pytest.mark.parametrize(
    ("left", "right", "lower_boundary", "upper_boundary"),
    (
        (
            "[",
            "]",
            NumericBoundaryKind.INCLUSIVE,
            NumericBoundaryKind.INCLUSIVE,
        ),
        (
            "[",
            ")",
            NumericBoundaryKind.INCLUSIVE,
            NumericBoundaryKind.EXCLUSIVE,
        ),
        (
            "(",
            "]",
            NumericBoundaryKind.EXCLUSIVE,
            NumericBoundaryKind.INCLUSIVE,
        ),
        (
            "(",
            ")",
            NumericBoundaryKind.EXCLUSIVE,
            NumericBoundaryKind.EXCLUSIVE,
        ),
    ),
)
def test_range_boundary_combinations_remain_semantically_distinct(
    left: str,
    right: str,
    lower_boundary: NumericBoundaryKind,
    upper_boundary: NumericBoundaryKind,
) -> None:
    text = f"Range {left}10, 20{right} mg."
    expression = f"{left}10, 20{right} mg"
    quantity = _quantity(
        text=text,
        expression=expression,
        role=QuantityRole.RANGE,
        comparator=QuantityComparator.RANGE,
        lower_value="10",
        upper_value="20",
        lower_boundary=lower_boundary,
        upper_boundary=upper_boundary,
        unit_literal="mg",
        denominator=None,
        lower_fragment="10",
        upper_fragment="20",
        comparator_fragment=",",
        unit_fragment="mg",
        denominator_fragment=None,
        semantic_role_fragment="Range",
        lower_boundary_fragment=left,
        upper_boundary_fragment=right,
    )

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.VERIFIED


def test_range_boundary_delimiter_mutation_is_quarantined() -> None:
    text = "Range [10, 20) mg."
    quantity = _quantity(
        text=text,
        expression="[10, 20) mg",
        role=QuantityRole.RANGE,
        comparator=QuantityComparator.RANGE,
        lower_value="10",
        upper_value="20",
        lower_boundary=NumericBoundaryKind.EXCLUSIVE,
        upper_boundary=NumericBoundaryKind.EXCLUSIVE,
        unit_literal="mg",
        denominator=None,
        lower_fragment="10",
        upper_fragment="20",
        comparator_fragment=",",
        unit_fragment="mg",
        denominator_fragment=None,
        semantic_role_fragment="Range",
        lower_boundary_fragment="[",
        upper_boundary_fragment=")",
    )

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert report.state is NumericBindingScopeState.QUARANTINE
    assert "numeric_boundary_direction_mismatch" in _violation_codes(report)


@pytest.mark.parametrize(
    ("structured_lower_value", "structured_upper_value", "expected_component"),
    (
        ("11", "20", "lower_value"),
        ("10", "21", "upper_value"),
    ),
)
def test_changed_range_limit_is_quarantined(
    structured_lower_value: str,
    structured_upper_value: str,
    expected_component: str,
) -> None:
    text, quantity = _range_quantity(
        structured_lower_value=structured_lower_value,
        structured_upper_value=structured_upper_value,
    )

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert any(
        violation.code == "numeric_decimal_value_mismatch"
        and violation.component == expected_component
        for violation in report.violations
    )


@pytest.mark.parametrize(
    (
        "text",
        "expression",
        "role",
        "comparator",
        "lower_value",
        "unit",
        "denominator",
        "lower_fragment",
        "comparator_fragment",
        "role_fragment",
    ),
    (
        (
            "Give exactly 0.5 mg/kg dose.",
            "exactly 0.5 mg/kg",
            QuantityRole.DOSE,
            QuantityComparator.EXACT,
            "0.5",
            "mg/kg",
            "kg",
            "0.5",
            "exactly",
            "Give",
        ),
        (
            "Give exactly 0,5 mg/kg dose.",
            "exactly 0,5 mg/kg",
            QuantityRole.DOSE,
            QuantityComparator.EXACT,
            "0.5",
            "mg/kg",
            "kg",
            "0,5",
            "exactly",
            "Give",
        ),
        (
            "Give exactly 2 mg/m² dose.",
            "exactly 2 mg/m²",
            QuantityRole.DOSE,
            QuantityComparator.EXACT,
            "2",
            "mg/m²",
            "m²",
            "2",
            "exactly",
            "Give",
        ),
        (
            "Measurement exactly 12.5 %.",
            "exactly 12.5 %",
            QuantityRole.MEASUREMENT,
            QuantityComparator.EXACT,
            "12.5",
            "%",
            None,
            "12.5",
            "exactly",
            "Measurement",
        ),
        (
            "Frequency exactly 3 times/day.",
            "exactly 3 times/day",
            QuantityRole.FREQUENCY,
            QuantityComparator.EXACT,
            "3",
            "times/day",
            "day",
            "3",
            "exactly",
            "Frequency",
        ),
        (
            "Interval exactly 8 h.",
            "exactly 8 h",
            QuantityRole.INTERVAL,
            QuantityComparator.EXACT,
            "8",
            "h",
            None,
            "8",
            "exactly",
            "Interval",
        ),
        (
            "Duration exactly 7 d.",
            "exactly 7 d",
            QuantityRole.DURATION,
            QuantityComparator.EXACT,
            "7",
            "d",
            None,
            "7",
            "exactly",
            "Duration",
        ),
        (
            "Threshold at least 10 mg.",
            "at least 10 mg",
            QuantityRole.THRESHOLD,
            QuantityComparator.GREATER_THAN_OR_EQUAL,
            "10",
            "mg",
            None,
            "10",
            "at least",
            "Threshold",
        ),
    ),
)
def test_numeric_forms_preserve_value_unit_denominator_role_and_comparator(
    text: str,
    expression: str,
    role: QuantityRole,
    comparator: QuantityComparator,
    lower_value: str,
    unit: str,
    denominator: str | None,
    lower_fragment: str,
    comparator_fragment: str,
    role_fragment: str,
) -> None:
    if comparator is QuantityComparator.GREATER_THAN_OR_EQUAL:
        lower_boundary = NumericBoundaryKind.INCLUSIVE
        upper_boundary = NumericBoundaryKind.UNBOUNDED
    else:
        lower_boundary = NumericBoundaryKind.NOT_APPLICABLE
        upper_boundary = NumericBoundaryKind.NOT_APPLICABLE
    quantity = _quantity(
        text=text,
        expression=expression,
        role=role,
        comparator=comparator,
        lower_value=lower_value,
        lower_boundary=lower_boundary,
        upper_boundary=upper_boundary,
        unit_literal=unit,
        denominator=denominator,
        lower_fragment=lower_fragment,
        comparator_fragment=comparator_fragment,
        unit_fragment=unit,
        denominator_fragment=denominator,
        semantic_role_fragment=role_fragment,
    )

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.VERIFIED


def test_multiple_quantities_in_one_clause_have_independent_bindings() -> None:
    text = "Give exactly 10 mg dose for duration exactly 7 d."
    dose = _quantity(
        text=text,
        expression="exactly 10 mg",
        role=QuantityRole.DOSE,
        lower_value="10",
        unit_literal="mg",
        denominator=None,
        lower_fragment="10",
        unit_fragment="mg",
        denominator_fragment=None,
        semantic_role_fragment="Give",
    )
    duration = _quantity(
        text=text,
        expression="exactly 7 d",
        role=QuantityRole.DURATION,
        lower_value="7",
        unit_literal="d",
        denominator=None,
        lower_fragment="7",
        unit_fragment="d",
        denominator_fragment=None,
        semantic_role_fragment="duration",
        unit_occurrence=2,
    ).model_copy(update={"quantity_id": "quantity-002"})

    reports = (_audit(dose, text=text), _audit(duration, text=text))

    assert {report.quantity_id for report in reports} == {
        "quantity-001",
        "quantity-002",
    }
    assert all(
        report.disposition is NumericBindingDisposition.VERIFIED for report in reports
    )


def test_textual_equality_alone_cannot_approve_conflicting_semantics() -> None:
    text = "Give exactly 10 mg/kg dose."
    quantity = _quantity().model_copy(
        update={
            "role": QuantityRole.DURATION,
            "comparator": QuantityComparator.LESS_THAN_OR_EQUAL,
            "lower_boundary": NumericBoundaryKind.UNBOUNDED,
            "upper_boundary": NumericBoundaryKind.INCLUSIVE,
            "unit_literal": "mL",
            "unit_normalized": "mL",
            "denominator": None,
        }
    )

    report = _audit(quantity, text=text)

    assert report.disposition is not NumericBindingDisposition.VERIFIED
    assert not report.ready_for_reconciliation
    assert {
        "numeric_unit_mismatch",
        "numeric_comparator_mismatch",
        "numeric_semantic_role_mismatch",
    }.issubset(_violation_codes(report))


def test_clause_context_mutation_requires_review() -> None:
    text = "Give exactly 10 mg/kg dose to dogs."
    quantity = _quantity(text=text, expression="Give exactly 10 mg/kg")

    report = _audit(
        quantity,
        text=text,
        normalized_statement="Give exactly 10 mg/kg dose to cats.",
    )

    assert report.disposition is NumericBindingDisposition.UNVERIFIABLE
    assert "numeric_statement_context_transformation_unproven" in _violation_codes(
        report
    )
    assert not report.ready_for_reconciliation
    assert not report.ready_for_delivery


def test_semantic_role_selector_outside_expression_is_quarantined() -> None:
    text = "Give exactly 10 mg/kg dose."
    quantity = _quantity(text=text)
    binding = quantity.source_binding.model_copy(
        update={"semantic_role_selector": _explicit(_byte_range(text, "dose"))}
    )

    report = _audit(quantity.model_copy(update={"source_binding": binding}), text=text)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_component_outside_expression" in _violation_codes(report)
    assert not report.ready_for_reconciliation
    assert not report.ready_for_delivery


@pytest.mark.parametrize(
    ("text", "source_unit", "mutated_unit"),
    (
        ("Give exactly 10 mg/kg dose.", "mg/kg", "MG/KG"),
        ("Give exactly 10 mL dose.", "mL", "ML"),
        ("Give exactly 10 mM dose.", "mM", "MM"),
    ),
)
def test_unit_case_mutations_are_never_verified(
    text: str,
    source_unit: str,
    mutated_unit: str,
) -> None:
    quantity = _quantity(
        text=text,
        expression=f"Give exactly 10 {source_unit}",
        unit_literal=source_unit,
        unit_fragment=source_unit,
        denominator=None,
        denominator_fragment=None,
    ).model_copy(update={"unit_literal": mutated_unit, "unit_normalized": mutated_unit})

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_unit_mismatch" in _violation_codes(report)


def test_decimal_selector_with_surrounding_space_is_invalid() -> None:
    text = "Give exactly 10 mg dose."
    quantity = _quantity(
        text=text,
        expression="Give exactly 10 mg",
        unit_literal="mg",
        unit_fragment="mg",
        denominator=None,
        denominator_fragment=None,
    )
    lower = quantity.source_binding.lower_value_selector.selector
    assert lower is not None
    binding = quantity.source_binding.model_copy(
        update={
            "lower_value_selector": _explicit(
                ClauseUtf8ByteRange(
                    start_byte=lower.start_byte - 1,
                    end_byte=lower.end_byte,
                )
            )
        }
    )

    report = _audit(quantity.model_copy(update={"source_binding": binding}), text=text)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_decimal_value_mismatch" in _violation_codes(report)


def test_comparator_selector_with_surrounding_space_is_not_demonstrated() -> None:
    text = "Give exactly 10 mg dose."
    quantity = _quantity(
        text=text,
        expression="Give exactly 10 mg",
        unit_literal="mg",
        unit_fragment="mg",
        denominator=None,
        denominator_fragment=None,
    )
    comparator = quantity.source_binding.comparator_selector.selector
    assert comparator is not None
    binding = quantity.source_binding.model_copy(
        update={
            "comparator_selector": _explicit(
                ClauseUtf8ByteRange(
                    start_byte=comparator.start_byte - 1,
                    end_byte=comparator.end_byte,
                )
            )
        }
    )

    report = _audit(quantity.model_copy(update={"source_binding": binding}), text=text)

    assert report.disposition is NumericBindingDisposition.UNVERIFIABLE
    assert "numeric_comparator_not_demonstrated" in _violation_codes(report)


@pytest.mark.parametrize(
    (
        "text",
        "expression",
        "role",
        "comparator",
        "comparator_fragment",
        "semantic_role_fragment",
        "lower_boundary",
        "upper_boundary",
    ),
    (
        (
            "Administrar exatamente 10 mg dose.",
            "Administrar exatamente 10 mg",
            QuantityRole.DOSE,
            QuantityComparator.EXACT,
            "exatamente",
            "Administrar",
            NumericBoundaryKind.NOT_APPLICABLE,
            NumericBoundaryKind.NOT_APPLICABLE,
        ),
        (
            "Administrar aproximadamente 10 mg dose.",
            "Administrar aproximadamente 10 mg",
            QuantityRole.DOSE,
            QuantityComparator.APPROXIMATE,
            "aproximadamente",
            "Administrar",
            NumericBoundaryKind.NOT_APPLICABLE,
            NumericBoundaryKind.NOT_APPLICABLE,
        ),
        (
            "Manter menor que 10 mg limiar.",
            "Manter menor que 10 mg limiar",
            QuantityRole.THRESHOLD,
            QuantityComparator.LESS_THAN,
            "menor que",
            "limiar",
            NumericBoundaryKind.UNBOUNDED,
            NumericBoundaryKind.EXCLUSIVE,
        ),
        (
            "Medido maior ou igual a 10 mg medição.",
            "Medido maior ou igual a 10 mg medição",
            QuantityRole.MEASUREMENT,
            QuantityComparator.GREATER_THAN_OR_EQUAL,
            "maior ou igual a",
            "Medido",
            NumericBoundaryKind.INCLUSIVE,
            NumericBoundaryKind.UNBOUNDED,
        ),
        (
            "Administrar exatamente 8 h a cada intervalo.",
            "Administrar exatamente 8 h a cada",
            QuantityRole.INTERVAL,
            QuantityComparator.EXACT,
            "exatamente",
            "a cada",
            NumericBoundaryKind.NOT_APPLICABLE,
            NumericBoundaryKind.NOT_APPLICABLE,
        ),
        (
            "Administrar exatamente 7 dias durante duração.",
            "Administrar exatamente 7 dias durante",
            QuantityRole.DURATION,
            QuantityComparator.EXACT,
            "exatamente",
            "durante",
            NumericBoundaryKind.NOT_APPLICABLE,
            NumericBoundaryKind.NOT_APPLICABLE,
        ),
    ),
)
def test_versioned_ptbr_lexicon_preserves_numeric_semantics(
    text: str,
    expression: str,
    role: QuantityRole,
    comparator: QuantityComparator,
    comparator_fragment: str,
    semantic_role_fragment: str,
    lower_boundary: NumericBoundaryKind,
    upper_boundary: NumericBoundaryKind,
) -> None:
    quantity = _quantity(
        text=text,
        expression=expression,
        role=role,
        comparator=comparator,
        comparator_fragment=comparator_fragment,
        semantic_role_fragment=semantic_role_fragment,
        lower_boundary=lower_boundary,
        upper_boundary=upper_boundary,
        lower_value="8" if " 8 h" in text else "7" if " 7 dias" in text else "10",
        lower_fragment="8" if " 8 h" in text else "7" if " 7 dias" in text else "10",
        unit_literal="mg" if " mg" in text else "h" if " 8 h" in text else "dias",
        unit_fragment="mg" if " mg" in text else "h" if " 8 h" in text else "dias",
        denominator=None,
        denominator_fragment=None,
    )

    report = _audit(quantity, text=text)

    assert NumericSourceBindingAuditor.LEXICON_VERSION == (
        "numeric-source-binding-en-ptbr-v1"
    )
    assert report.disposition is NumericBindingDisposition.VERIFIED


def test_decimal_scale_mutation_is_quarantined() -> None:
    quantity = _quantity().model_copy(update={"lower_value": "10.0"})

    report = _audit(quantity)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_decimal_value_mismatch" in _violation_codes(report)


def test_inter_quantity_expression_overlap_is_fail_closed() -> None:
    text = "Give exactly 10 mg dose and exactly 20 mL dose."
    first = _quantity(
        text=text,
        expression="Give exactly 10 mg",
        unit_literal="mg",
        unit_fragment="mg",
        denominator=None,
        denominator_fragment=None,
    )
    second = _quantity(
        text=text,
        expression="exactly 20 mL dose",
        lower_value="20",
        lower_fragment="20",
        unit_literal="mL",
        unit_fragment="mL",
        denominator=None,
        denominator_fragment=None,
        semantic_role_fragment="dose",
    ).model_copy(update={"quantity_id": "quantity-002"})

    assert NumericSourceBindingAuditor.overlapping_quantity_ids((first, second)) == (
        frozenset()
    )

    overlapping_binding = second.source_binding.model_copy(
        update={"expression_selector": first.source_binding.expression_selector}
    )
    overlapping_second = second.model_copy(
        update={"source_binding": overlapping_binding}
    )

    assert NumericSourceBindingAuditor.overlapping_quantity_ids(
        (first, overlapping_second)
    ) == frozenset({"quantity-001", "quantity-002"})


def test_extra_unbound_source_span_is_quarantined() -> None:
    quantity = _quantity(source_span_ids=("span-001", "span-foreign"))

    report = _audit(quantity)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_binding_span_mismatch" in _violation_codes(report)
    assert report.state is NumericBindingScopeState.QUARANTINE


def test_post_construction_component_overlap_is_quarantined() -> None:
    text = "Give exactly 10 mg dose."
    quantity = _quantity(
        text=text,
        expression="Give exactly 10 mg",
        unit_literal="mg",
        unit_fragment="mg",
        denominator=None,
        denominator_fragment=None,
    )
    binding = quantity.source_binding.model_copy(
        update={
            "unit_selector": quantity.source_binding.lower_value_selector,
        }
    )
    mutated = quantity.model_copy(
        update={
            "unit_literal": "10",
            "unit_normalized": "10",
            "source_binding": binding,
        }
    )

    report = _audit(mutated, text=text)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_component_overlap" in _violation_codes(report)
    assert report.state is NumericBindingScopeState.QUARANTINE


@pytest.mark.parametrize("separator", ("per", "por"))
def test_textual_denominator_removal_is_quarantined(separator: str) -> None:
    text = f"Give exactly 10 mg {separator} kg dose."
    quantity = _quantity(
        text=text,
        expression=f"Give exactly 10 mg {separator} kg",
        unit_literal="mg",
        unit_fragment="mg",
        denominator=None,
        denominator_fragment=None,
    )

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.INVALID
    assert "numeric_denominator_omitted" in _violation_codes(report)


@pytest.mark.parametrize("separator", ("per", "por"))
def test_textual_composite_unit_preserves_denominator(separator: str) -> None:
    text = f"Give exactly 10 mg {separator} kg dose."
    composite_unit = f"mg {separator} kg"
    quantity = _quantity(
        text=text,
        expression=f"Give exactly 10 {composite_unit}",
        unit_literal=composite_unit,
        unit_fragment=composite_unit,
        denominator="kg",
        denominator_fragment="kg",
    )

    report = _audit(quantity, text=text)

    assert report.disposition is NumericBindingDisposition.VERIFIED


def test_source_numeric_token_without_explicit_value_binding_is_detected() -> None:
    text = "Give exactly 10 mg dose."
    clause = _clause(text)

    assert NumericSourceBindingAuditor.clauses_with_unbound_numeric_tokens(
        clauses_by_id={str(clause.clause_id): clause},
        quantities=(),
    ) == frozenset({"clause-001"})

    assert (
        NumericSourceBindingAuditor.clauses_with_unbound_numeric_tokens(
            clauses_by_id={str(clause.clause_id): clause},
            quantities=(
                _quantity(
                    text=text,
                    expression="Give exactly 10 mg",
                    unit_literal="mg",
                    unit_fragment="mg",
                    denominator=None,
                    denominator_fragment=None,
                ),
            ),
        )
        == frozenset()
    )
