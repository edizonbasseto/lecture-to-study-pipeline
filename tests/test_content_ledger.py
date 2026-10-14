from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from lecture_study.domain.content_ledger import (
    AssertionPolarity,
    AtomicContentLedger,
    AtomicContentType,
    AtomicProposition,
    CertaintyLevel,
    ClauseUtf8ByteRange,
    ContentDimension,
    ContentImportance,
    DisagreementStatus,
    EvidenceDisagreement,
    EvidenceExcerpt,
    NumericBindingComponentState,
    NumericBoundaryKind,
    NumericImplicitJustification,
    NumericQuantity,
    NumericSourceBinding,
    NumericSourceComponentBinding,
    PropositionRelation,
    PropositionRelationType,
    QuantityComparator,
    QuantityRole,
    RecommendationStrength,
    ReviewStatus,
)
from lecture_study.domain.semantic_observation import (
    SourceClause,
    SourceClauseInventory,
    SourcePartitionKind,
    SourceTextExtent,
    SourceVisualExtent,
    TextPartitionSelector,
    VisualFullFrameSelector,
    VisualPartitionSelector,
    GeneratedObservationIdentity,
    canonical_visual_region_binding_sha256,
)
from lecture_study.domain.source_universe import (
    ConceptualLearningUnit,
    EvidenceModality,
    SourceEvidenceSpan,
    SourceSpanClassification,
    SourceUniverse,
    TimelineClassification,
    TimelineInterval,
)
from lecture_study.quality.content_ledger import (
    AtomicContentLedgerAuditReport,
    AtomicContentLedgerAuditor,
    canonical_source_universe_sha256,
)

_CONTENT_DIGEST = "a" * 64
_SPAN_TEXT_BY_ID = {
    "span-001": "Bradycardia is clinically relevant.",
    "span-002": "Tachycardia requires evaluation.",
}


def _implicit_numeric_component(
    component_name: str,
    *,
    source_clause_id: str = "clause-span-001",
) -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(
        state=NumericBindingComponentState.IMPLICIT,
        implicit_justification=NumericImplicitJustification(
            justification_id=f"justification-{component_name}",
            source_clause_id=source_clause_id,
            rationale=f"The {component_name} component requires external review.",
        ),
    )


def _numeric_test_binding(
    *,
    range_quantity: bool = False,
    source_clause_id: str = "clause-span-001",
) -> NumericSourceBinding:
    absent = NumericSourceComponentBinding(state=NumericBindingComponentState.ABSENT)
    return NumericSourceBinding(
        source_clause_id=source_clause_id,
        exact_expression="10",
        expression_selector=NumericSourceComponentBinding(
            state=NumericBindingComponentState.EXPLICIT,
            selector=ClauseUtf8ByteRange(start_byte=0, end_byte=2),
        ),
        lower_value_selector=_implicit_numeric_component(
            "lower-value",
            source_clause_id=source_clause_id,
        ),
        upper_value_selector=(
            _implicit_numeric_component(
                "upper-value",
                source_clause_id=source_clause_id,
            )
            if range_quantity
            else absent
        ),
        comparator_selector=_implicit_numeric_component(
            "comparator",
            source_clause_id=source_clause_id,
        ),
        unit_selector=_implicit_numeric_component(
            "unit",
            source_clause_id=source_clause_id,
        ),
        denominator_selector=absent,
        semantic_role_selector=_implicit_numeric_component(
            "semantic-role",
            source_clause_id=source_clause_id,
        ),
        lower_boundary_selector=(
            _implicit_numeric_component(
                "lower-boundary",
                source_clause_id=source_clause_id,
            )
            if range_quantity
            else absent
        ),
        upper_boundary_selector=(
            _implicit_numeric_component(
                "upper-boundary",
                source_clause_id=source_clause_id,
            )
            if range_quantity
            else absent
        ),
    )


def _span(span_id: str, start: float, end: float) -> SourceEvidenceSpan:
    return SourceEvidenceSpan(
        schema_version=1,
        span_id=span_id,
        lecture_id="lecture-001",
        source_artifact_id="transcript",
        source_record_id=f"record-{span_id}",
        modality=EvidenceModality.TRANSCRIPT,
        start_time=start,
        end_time=end,
        classification=SourceSpanClassification.INSTRUCTIONAL,
        content_sha256=hashlib.sha256(
            _SPAN_TEXT_BY_ID[span_id].encode("utf-8")
        ).hexdigest(),
    )


def _source_universe() -> SourceUniverse:
    spans = (_span("span-001", 10.0, 20.0), _span("span-002", 310.0, 320.0))
    intervals = (
        TimelineInterval(
            schema_version=1,
            interval_id="interval-001",
            lecture_id="lecture-001",
            sequence=1,
            start_time=0.0,
            end_time=300.0,
            classification=TimelineClassification.INSTRUCTIONAL,
            evidence_span_ids=("span-001",),
        ),
        TimelineInterval(
            schema_version=1,
            interval_id="interval-002",
            lecture_id="lecture-001",
            sequence=2,
            start_time=300.0,
            end_time=600.0,
            classification=TimelineClassification.INSTRUCTIONAL,
            evidence_span_ids=("span-002",),
        ),
    )
    units = (
        ConceptualLearningUnit(
            schema_version=1,
            unit_id="unit-001",
            lecture_id="lecture-001",
            sequence=1,
            start_time=0.0,
            end_time=300.0,
            primary_source_span_ids=("span-001",),
        ),
        ConceptualLearningUnit(
            schema_version=1,
            unit_id="unit-002",
            lecture_id="lecture-001",
            sequence=2,
            start_time=300.0,
            end_time=600.0,
            primary_source_span_ids=("span-002",),
        ),
    )
    return SourceUniverse(
        schema_version=1,
        lecture_id="lecture-001",
        media_duration_seconds=600.0,
        timeline_intervals=intervals,
        evidence_spans=spans,
        learning_units=units,
    )


def _text_selector(text: str) -> TextPartitionSelector:
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return TextPartitionSelector(
        kind=SourcePartitionKind.TEXT_RANGE,
        start_byte=0,
        end_byte=len(text.encode("utf-8")),
        selected_sha256=text_sha256,
    )


def _source_clause(unit_id: str, span_id: str, text: str) -> SourceClause:
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SourceClause(
        schema_version=1,
        clause_id=f"clause-{span_id}",
        lecture_id="lecture-001",
        learning_unit_id=unit_id,
        sequence=1,
        source_span_id=span_id,
        source_content_sha256=text_sha256,
        modality=EvidenceModality.TRANSCRIPT,
        selector=_text_selector(text),
        verbatim_text=text,
        text_sha256=text_sha256,
    )


def _excerpt_from_clause(clause: SourceClause) -> EvidenceExcerpt:
    return EvidenceExcerpt(
        source_clause_id=clause.clause_id,
        source_span_id=clause.source_span_id,
        modality=clause.modality,
        source_content_sha256=clause.source_content_sha256,
        selector=clause.selector.model_dump(mode="json"),
        generated_observation_identity=(
            clause.generated_observation_identity.model_dump(mode="json")
            if clause.generated_observation_identity is not None
            else None
        ),
        verbatim_text=clause.verbatim_text,
        observed_text=clause.observed_text,
        text_sha256=clause.text_sha256,
    )


def _excerpt(unit_id: str, span_id: str, text: str) -> EvidenceExcerpt:
    return _excerpt_from_clause(_source_clause(unit_id, span_id, text))


def _proposition(
    sequence: int,
    proposition_id: str,
    unit_id: str,
    span_id: str,
    statement: str,
) -> AtomicProposition:
    return AtomicProposition(
        schema_version=1,
        proposition_id=proposition_id,
        lecture_id="lecture-001",
        learning_unit_id=unit_id,
        sequence=sequence,
        content_type=AtomicContentType.PRACTICAL_OBSERVATION,
        importance=ContentImportance.CORE,
        normalized_statement=statement,
        source_span_ids=(span_id,),
        evidence_excerpts=(_excerpt(unit_id, span_id, statement),),
        required_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        polarity=AssertionPolarity.AFFIRMED,
        certainty=CertaintyLevel.CERTAIN,
        recommendation_strength=RecommendationStrength.NOT_APPLICABLE,
        safety_critical=False,
        extraction_confidence=1.0,
        review_status=ReviewStatus.VERIFIED,
    )


def _valid_ledgers(universe: SourceUniverse) -> tuple[AtomicContentLedger, ...]:
    universe_sha256 = canonical_source_universe_sha256(universe)
    return (
        AtomicContentLedger(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            source_universe_sha256=universe_sha256,
            propositions=(
                _proposition(
                    1,
                    "proposition-001",
                    "unit-001",
                    "span-001",
                    "Bradycardia is clinically relevant.",
                ),
            ),
        ),
        AtomicContentLedger(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-002",
            source_universe_sha256=universe_sha256,
            propositions=(
                _proposition(
                    1,
                    "proposition-002",
                    "unit-002",
                    "span-002",
                    "Tachycardia requires evaluation.",
                ),
            ),
        ),
    )


def _valid_clause_inventories(
    universe: SourceUniverse,
) -> tuple[SourceClauseInventory, ...]:
    universe_sha256 = canonical_source_universe_sha256(universe)
    clauses = (
        _source_clause(
            "unit-001",
            "span-001",
            "Bradycardia is clinically relevant.",
        ),
        _source_clause(
            "unit-002",
            "span-002",
            "Tachycardia requires evaluation.",
        ),
    )
    return tuple(
        SourceClauseInventory(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id=clause.learning_unit_id,
            source_universe_sha256=universe_sha256,
            text_extents=(
                SourceTextExtent(
                    source_span_id=clause.source_span_id,
                    source_content_sha256=clause.source_content_sha256,
                    canonical_text=clause.verbatim_text,
                    byte_length=len(clause.verbatim_text.encode("utf-8")),
                ),
            ),
            clauses=(clause,),
        )
        for clause in clauses
        if clause.verbatim_text is not None
    )


def _audit(
    *,
    source_universe: SourceUniverse,
    ledgers: tuple[AtomicContentLedger, ...],
    clause_inventories: tuple[SourceClauseInventory, ...] | None = None,
) -> AtomicContentLedgerAuditReport:
    return AtomicContentLedgerAuditor().audit(
        source_universe=source_universe,
        clause_inventories=(
            clause_inventories
            if clause_inventories is not None
            else _valid_clause_inventories(source_universe)
        ),
        ledgers=ledgers,
    )


def _visual_processing_identity(
    implementation_digest: str = "b",
    configuration_digest: str = "c",
) -> GeneratedObservationIdentity:
    return GeneratedObservationIdentity(
        implementation_sha256=implementation_digest * 64,
        configuration_sha256=configuration_digest * 64,
    )


def _visual_selector(
    *,
    full_frame: bool,
) -> VisualFullFrameSelector | VisualPartitionSelector:
    if full_frame:
        return VisualFullFrameSelector(
            kind=SourcePartitionKind.VISUAL_FULL_FRAME,
            original_frame_sha256=_CONTENT_DIGEST,
        )
    hashing_identity = _visual_processing_identity("d", "e")
    coordinates = (0.1, 0.2, 0.8, 0.9)
    region_content_sha256 = "f" * 64
    return VisualPartitionSelector(
        kind=SourcePartitionKind.VISUAL_REGION,
        original_frame_sha256=_CONTENT_DIGEST,
        left=coordinates[0],
        top=coordinates[1],
        right=coordinates[2],
        bottom=coordinates[3],
        selected_sha256=region_content_sha256,
        hashing_identity=hashing_identity,
        region_binding_sha256=canonical_visual_region_binding_sha256(
            original_frame_sha256=_CONTENT_DIGEST,
            coordinates=coordinates,
            region_content_sha256=region_content_sha256,
            hashing_identity=hashing_identity,
        ),
    )


def _visual_audit_fixture(
    *,
    full_frame: bool,
) -> tuple[
    SourceUniverse,
    tuple[AtomicContentLedger, ...],
    tuple[SourceClauseInventory, ...],
]:
    universe = SourceUniverse(
        schema_version=1,
        lecture_id="lecture-001",
        media_duration_seconds=300.0,
        timeline_intervals=(
            TimelineInterval(
                schema_version=1,
                interval_id="interval-visual",
                lecture_id="lecture-001",
                sequence=1,
                start_time=0.0,
                end_time=300.0,
                classification=TimelineClassification.INSTRUCTIONAL,
                evidence_span_ids=("span-visual",),
            ),
        ),
        evidence_spans=(
            SourceEvidenceSpan(
                schema_version=1,
                span_id="span-visual",
                lecture_id="lecture-001",
                source_artifact_id="visual",
                source_record_id="record-visual",
                modality=EvidenceModality.VISUAL,
                start_time=1.0,
                end_time=1.0,
                classification=SourceSpanClassification.INSTRUCTIONAL,
                content_sha256=_CONTENT_DIGEST,
            ),
        ),
        learning_units=(
            ConceptualLearningUnit(
                schema_version=1,
                unit_id="unit-visual",
                lecture_id="lecture-001",
                sequence=1,
                start_time=0.0,
                end_time=300.0,
                primary_source_span_ids=("span-visual",),
            ),
        ),
    )
    observed_text = "The selected frame area shows a labeled cardiac structure."
    full_frame_clause = SourceClause(
        schema_version=1,
        clause_id="clause-full-frame",
        lecture_id="lecture-001",
        learning_unit_id="unit-visual",
        sequence=1,
        source_span_id="span-visual",
        source_content_sha256=_CONTENT_DIGEST,
        modality=EvidenceModality.VISUAL,
        selector=_visual_selector(full_frame=True),
        observed_text=observed_text,
        text_sha256=hashlib.sha256(observed_text.encode("utf-8")).hexdigest(),
        generated_observation_identity=_visual_processing_identity(),
    )
    region_clause = SourceClause(
        schema_version=1,
        clause_id="clause-region",
        lecture_id="lecture-001",
        learning_unit_id="unit-visual",
        sequence=2,
        source_span_id="span-visual",
        source_content_sha256=_CONTENT_DIGEST,
        modality=EvidenceModality.VISUAL,
        selector=_visual_selector(full_frame=False),
        observed_text=observed_text,
        text_sha256=hashlib.sha256(observed_text.encode("utf-8")).hexdigest(),
        generated_observation_identity=_visual_processing_identity(),
    )
    clauses = (full_frame_clause,) if full_frame else (full_frame_clause, region_clause)
    clause = full_frame_clause if full_frame else region_clause
    universe_sha256 = canonical_source_universe_sha256(universe)
    inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-visual",
        source_universe_sha256=universe_sha256,
        visual_extents=(
            SourceVisualExtent(
                source_span_id="span-visual",
                source_content_sha256=_CONTENT_DIGEST,
            ),
        ),
        clauses=clauses,
    )
    proposition = AtomicProposition(
        schema_version=1,
        proposition_id="proposition-visual",
        lecture_id="lecture-001",
        learning_unit_id="unit-visual",
        sequence=1,
        content_type=AtomicContentType.PRACTICAL_OBSERVATION,
        importance=ContentImportance.CORE,
        normalized_statement=observed_text,
        source_span_ids=("span-visual",),
        evidence_excerpts=(_excerpt_from_clause(clause),),
        required_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        polarity=AssertionPolarity.AFFIRMED,
        certainty=CertaintyLevel.CERTAIN,
        recommendation_strength=RecommendationStrength.NOT_APPLICABLE,
        safety_critical=False,
        extraction_confidence=1.0,
        review_status=ReviewStatus.VERIFIED,
    )
    ledger = AtomicContentLedger(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-visual",
        source_universe_sha256=universe_sha256,
        propositions=(proposition,),
    )
    return universe, (ledger,), (inventory,)


def _ocr_audit_fixture() -> tuple[
    SourceUniverse,
    tuple[AtomicContentLedger, ...],
    tuple[SourceClauseInventory, ...],
]:
    base_universe = _source_universe()
    ocr_span = base_universe.evidence_spans[0].model_copy(
        update={"modality": EvidenceModality.OCR}
    )
    universe = base_universe.model_copy(
        update={"evidence_spans": (ocr_span, base_universe.evidence_spans[1])}
    )
    universe_sha256 = canonical_source_universe_sha256(universe)
    ocr_text = "Bradycardia is clinically relevant."
    ocr_clause = SourceClause(
        schema_version=1,
        clause_id="clause-span-001",
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        sequence=1,
        source_span_id="span-001",
        source_content_sha256=hashlib.sha256(ocr_text.encode("utf-8")).hexdigest(),
        modality=EvidenceModality.OCR,
        selector=_text_selector(ocr_text),
        observed_text=ocr_text,
        text_sha256=hashlib.sha256(ocr_text.encode("utf-8")).hexdigest(),
        generated_observation_identity=_visual_processing_identity(),
    )
    transcript_text = "Tachycardia requires evaluation."
    transcript_clause = _source_clause("unit-002", "span-002", transcript_text)
    inventories = (
        SourceClauseInventory(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            source_universe_sha256=universe_sha256,
            text_extents=(
                SourceTextExtent(
                    source_span_id="span-001",
                    source_content_sha256=ocr_clause.source_content_sha256,
                    modality=EvidenceModality.OCR,
                    canonical_text=ocr_text,
                    byte_length=len(ocr_text.encode("utf-8")),
                ),
            ),
            clauses=(ocr_clause,),
        ),
        SourceClauseInventory(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-002",
            source_universe_sha256=universe_sha256,
            text_extents=(
                SourceTextExtent(
                    source_span_id="span-002",
                    source_content_sha256=transcript_clause.source_content_sha256,
                    canonical_text=transcript_text,
                    byte_length=len(transcript_text.encode("utf-8")),
                ),
            ),
            clauses=(transcript_clause,),
        ),
    )
    ledgers = _valid_ledgers(universe)
    ocr_proposition = (
        ledgers[0]
        .propositions[0]
        .model_copy(update={"evidence_excerpts": (_excerpt_from_clause(ocr_clause),)})
    )
    ocr_ledger = ledgers[0].model_copy(update={"propositions": (ocr_proposition,)})
    return universe, (ocr_ledger, ledgers[1]), inventories


def _ledgers_with_excerpt(
    ledgers: tuple[AtomicContentLedger, ...],
    excerpt: EvidenceExcerpt,
    *,
    source_span_ids: tuple[str, ...] | None = None,
) -> tuple[AtomicContentLedger, ...]:
    ledger = ledgers[0]
    proposition = ledger.propositions[0]
    updated_proposition = proposition.model_copy(
        update={
            "source_span_ids": source_span_ids or proposition.source_span_ids,
            "evidence_excerpts": (excerpt,),
        }
    )
    return (
        ledger.model_copy(update={"propositions": (updated_proposition,)}),
        *ledgers[1:],
    )


def test_atomic_content_ledger_passes_exact_structural_coverage() -> None:
    universe = _source_universe()

    report = _audit(
        source_universe=universe,
        ledgers=_valid_ledgers(universe),
    )

    assert report.structurally_complete is True
    assert report.ready_for_atomic_content_gate is False
    assert report.proposition_count == 2
    assert report.structural_source_span_recall == 1.0


def test_atomic_proposition_accepts_multiple_distinct_clauses_per_span() -> None:
    proposition = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        "Bradycardia is clinically relevant.",
    )
    first_excerpt = proposition.evidence_excerpts[0]
    second_excerpt = first_excerpt.model_copy(
        update={"source_clause_id": "clause-span-001-detail"}
    )

    validated = AtomicProposition.model_validate(
        {
            **proposition.model_dump(mode="python"),
            "evidence_excerpts": (first_excerpt, second_excerpt),
        }
    )

    assert len(validated.evidence_excerpts) == 2


def test_atomic_proposition_rejects_reused_clause_and_uncovered_span() -> None:
    proposition = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        "Bradycardia is clinically relevant.",
    )
    excerpt = proposition.evidence_excerpts[0]

    with pytest.raises(ValidationError, match="clause IDs must be unique"):
        AtomicProposition.model_validate(
            {
                **proposition.model_dump(mode="python"),
                "evidence_excerpts": (excerpt, excerpt),
            }
        )

    with pytest.raises(ValidationError, match="every declared source span"):
        AtomicProposition.model_validate(
            {
                **proposition.model_dump(mode="python"),
                "source_span_ids": ("span-001", "span-002"),
            }
        )


def test_evidence_excerpt_rejects_both_or_neither_text_field() -> None:
    excerpt = _excerpt(
        "unit-001",
        "span-001",
        "Bradycardia is clinically relevant.",
    )

    with pytest.raises(ValidationError, match="exactly one"):
        EvidenceExcerpt.model_validate(
            {
                **excerpt.model_dump(mode="python"),
                "observed_text": excerpt.verbatim_text,
            }
        )
    with pytest.raises(ValidationError, match="exactly one"):
        EvidenceExcerpt.model_validate(
            {
                **excerpt.model_dump(mode="python"),
                "verbatim_text": None,
            }
        )


def test_evidence_excerpt_rejects_observed_or_verbatim_modality_confusion() -> None:
    excerpt = _excerpt(
        "unit-001",
        "span-001",
        "Bradycardia is clinically relevant.",
    )

    with pytest.raises(ValidationError, match="transcript.*verbatim"):
        EvidenceExcerpt.model_validate(
            {
                **excerpt.model_dump(mode="python"),
                "verbatim_text": None,
                "observed_text": excerpt.verbatim_text,
            }
        )
    with pytest.raises(ValidationError, match="OCR.*verbatim"):
        EvidenceExcerpt.model_validate(
            {
                **excerpt.model_dump(mode="python"),
                "modality": EvidenceModality.OCR,
            }
        )
    with pytest.raises(ValidationError, match="visual.*verbatim"):
        EvidenceExcerpt.model_validate(
            {
                **excerpt.model_dump(mode="python"),
                "modality": EvidenceModality.VISUAL,
                "generated_observation_identity": _visual_processing_identity().model_dump(
                    mode="json"
                ),
            }
        )


def test_evidence_excerpt_accepts_ocr_as_observed_text() -> None:
    excerpt = _excerpt(
        "unit-001",
        "span-001",
        "Observed OCR text.",
    )
    generator_identity = _visual_processing_identity().model_dump(mode="json")

    observed = EvidenceExcerpt.model_validate(
        {
            **excerpt.model_dump(mode="python"),
            "modality": EvidenceModality.OCR,
            "generated_observation_identity": generator_identity,
            "verbatim_text": None,
            "observed_text": excerpt.verbatim_text,
        }
    )

    assert observed.verbatim_text is None
    assert observed.observed_text == "Observed OCR text."
    assert observed.generated_observation_identity == generator_identity


def test_evidence_excerpt_rejects_ocr_without_generator_identity() -> None:
    excerpt = _excerpt(
        "unit-001",
        "span-001",
        "Observed OCR text.",
    )

    with pytest.raises(ValidationError, match="OCR.*generator identity"):
        EvidenceExcerpt.model_validate(
            {
                **excerpt.model_dump(mode="python"),
                "modality": EvidenceModality.OCR,
                "verbatim_text": None,
                "observed_text": excerpt.verbatim_text,
            }
        )


@pytest.mark.parametrize("full_frame", [True, False])
def test_atomic_content_ledger_accepts_exact_visual_selector_and_generator(
    full_frame: bool,
) -> None:
    universe, ledgers, inventories = _visual_audit_fixture(full_frame=full_frame)

    report = _audit(
        source_universe=universe,
        ledgers=ledgers,
        clause_inventories=inventories,
    )

    assert report.structurally_complete is True
    assert report.ready_for_atomic_content_gate is False


def test_atomic_content_ledger_rejects_visual_span_recategorized_as_transcript() -> (
    None
):
    universe, ledgers, _ = _visual_audit_fixture(full_frame=True)
    fabricated_text = "Fabricated transcript representation for visual bytes."
    clause = _source_clause("unit-visual", "span-visual", fabricated_text)
    inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-visual",
        source_universe_sha256=canonical_source_universe_sha256(universe),
        text_extents=(
            SourceTextExtent(
                source_span_id="span-visual",
                source_content_sha256=clause.source_content_sha256,
                canonical_text=fabricated_text,
                byte_length=len(fabricated_text.encode("utf-8")),
            ),
        ),
        clauses=(clause,),
    )
    proposition = (
        ledgers[0]
        .propositions[0]
        .model_copy(
            update={
                "normalized_statement": fabricated_text,
                "evidence_excerpts": (_excerpt_from_clause(clause),),
            }
        )
    )
    ledger = ledgers[0].model_copy(update={"propositions": (proposition,)})

    report = _audit(
        source_universe=universe,
        ledgers=(ledger,),
        clause_inventories=(inventory,),
    )

    assert report.invalid_evidence_excerpt_count == 1
    assert report.represented_instructional_source_span_count == 0
    assert report.structural_source_span_recall == 0.0
    assert report.missing_instructional_source_span_ids == ("span-visual",)


def test_atomic_content_ledger_rejects_nonexistent_or_wrong_span_clause() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    excerpt = ledgers[0].propositions[0].evidence_excerpts[0]
    missing_clause = excerpt.model_copy(
        update={"source_clause_id": "clause-does-not-exist"}
    )

    missing_report = _audit(
        source_universe=universe,
        ledgers=_ledgers_with_excerpt(ledgers, missing_clause),
    )
    wrong_span_excerpt = excerpt.model_copy(update={"source_span_id": "span-unknown"})
    wrong_span_report = _audit(
        source_universe=universe,
        ledgers=_ledgers_with_excerpt(
            ledgers,
            wrong_span_excerpt,
            source_span_ids=("span-unknown",),
        ),
    )

    assert missing_report.invalid_evidence_excerpt_count == 1
    assert wrong_span_report.invalid_evidence_excerpt_count == 1


def test_atomic_content_ledger_accepts_exact_reused_source_clause() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    ledger = ledgers[0]
    proposition = ledger.propositions[0]
    duplicate_proposition = proposition.model_copy(
        update={"proposition_id": "proposition-duplicate", "sequence": 2}
    )
    duplicated_ledger = ledger.model_copy(
        update={"propositions": (proposition, duplicate_proposition)}
    )

    report = _audit(
        source_universe=universe,
        ledgers=(duplicated_ledger, ledgers[1]),
    )

    assert report.reused_source_clause_count == 0
    assert "reused_atomic_evidence_clauses" not in {
        finding.code for finding in report.findings
    }


def test_atomic_content_ledger_rejects_conflicting_source_clause_reuse() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    ledger = ledgers[0]
    proposition = ledger.propositions[0]
    excerpt = proposition.evidence_excerpts[0]
    conflicting_excerpt = excerpt.model_copy(
        update={
            "selector": {
                "kind": SourcePartitionKind.TEXT_RANGE,
                "start_offset": 1,
                "end_offset": len(excerpt.verbatim_text or ""),
            }
        }
    )
    duplicate_proposition = proposition.model_copy(
        update={
            "proposition_id": "proposition-duplicate",
            "sequence": 2,
            "evidence_excerpts": (conflicting_excerpt,),
        }
    )
    duplicated_ledger = ledger.model_copy(
        update={"propositions": (proposition, duplicate_proposition)}
    )

    report = _audit(
        source_universe=universe,
        ledgers=(duplicated_ledger, ledgers[1]),
    )

    assert report.invalid_evidence_excerpt_count == 1
    assert report.reused_source_clause_count == 1
    assert "reused_atomic_evidence_clauses" in {
        finding.code for finding in report.findings
    }


def test_atomic_content_ledger_rejects_modality_selector_and_text_substitution() -> (
    None
):
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    excerpt = ledgers[0].propositions[0].evidence_excerpts[0]
    substituted_text = "A substituted but internally hashed observation."
    substitutions = (
        excerpt.model_copy(
            update={
                "modality": EvidenceModality.OCR,
                "generated_observation_identity": _visual_processing_identity().model_dump(
                    mode="json"
                ),
                "verbatim_text": None,
                "observed_text": excerpt.verbatim_text,
            }
        ),
        excerpt.model_copy(
            update={
                "selector": {
                    **excerpt.selector,
                    "start_byte": 1,
                }
            }
        ),
        excerpt.model_copy(
            update={
                "verbatim_text": substituted_text,
                "text_sha256": hashlib.sha256(
                    substituted_text.encode("utf-8")
                ).hexdigest(),
            }
        ),
    )

    for substituted_excerpt in substitutions:
        report = _audit(
            source_universe=universe,
            ledgers=_ledgers_with_excerpt(ledgers, substituted_excerpt),
        )
        assert report.invalid_evidence_excerpt_count == 1
        assert report.represented_instructional_source_span_count == 1
        assert report.structural_source_span_recall == 0.5
        assert report.missing_instructional_source_span_ids == ("span-001",)


def test_atomic_content_ledger_rejects_visual_generator_and_region_substitution() -> (
    None
):
    universe, ledgers, inventories = _visual_audit_fixture(full_frame=False)
    excerpt = ledgers[0].propositions[0].evidence_excerpts[0]
    substitutions = (
        excerpt.model_copy(
            update={
                "generated_observation_identity": _visual_processing_identity(
                    "1",
                    "2",
                ).model_dump(mode="json")
            }
        ),
        excerpt.model_copy(
            update={
                "selector": {
                    **excerpt.selector,
                    "selected_sha256": "1" * 64,
                }
            }
        ),
        excerpt.model_copy(
            update={
                "selector": {
                    **excerpt.selector,
                    "left": -0.1,
                }
            }
        ),
    )

    for substituted_excerpt in substitutions:
        report = _audit(
            source_universe=universe,
            ledgers=_ledgers_with_excerpt(ledgers, substituted_excerpt),
            clause_inventories=inventories,
        )
        assert report.invalid_evidence_excerpt_count == 1
        assert report.represented_instructional_source_span_count == 0
        assert report.structural_source_span_recall == 0.0
        assert report.missing_instructional_source_span_ids == ("span-visual",)


def test_atomic_content_ledger_rejects_substituted_ocr_generator() -> None:
    universe, ledgers, inventories = _ocr_audit_fixture()
    exact_report = _audit(
        source_universe=universe,
        ledgers=ledgers,
        clause_inventories=inventories,
    )
    assert exact_report.structurally_complete

    excerpt = ledgers[0].propositions[0].evidence_excerpts[0]
    substituted_excerpt = excerpt.model_copy(
        update={
            "generated_observation_identity": _visual_processing_identity(
                "1",
                "2",
            ).model_dump(mode="json")
        }
    )
    report = _audit(
        source_universe=universe,
        ledgers=_ledgers_with_excerpt(ledgers, substituted_excerpt),
        clause_inventories=inventories,
    )

    assert report.invalid_evidence_excerpt_count == 1
    assert report.represented_instructional_source_span_count == 1
    assert report.structural_source_span_recall == 0.5
    assert report.missing_instructional_source_span_ids == ("span-001",)


def test_atomic_content_ledger_rejects_missing_unit_and_source_span() -> None:
    universe = _source_universe()

    report = _audit(
        source_universe=universe,
        ledgers=_valid_ledgers(universe)[:1],
    )

    assert report.missing_instructional_source_span_ids == ("span-002",)
    assert {finding.code for finding in report.findings} >= {
        "missing_learning_unit_ledgers",
        "missing_instructional_source_span_inventory",
    }


def test_atomic_proposition_rejects_incomplete_evidence_mapping() -> None:
    proposition = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        "Bradycardia is clinically relevant.",
    )

    with pytest.raises(ValidationError, match="every declared source span"):
        AtomicProposition.model_validate(
            {
                **proposition.model_dump(mode="python"),
                "source_span_ids": ("span-001", "span-002"),
            }
        )


def test_evidence_excerpt_rejects_text_hash_mismatch() -> None:
    excerpt = _excerpt(
        "unit-001",
        "span-001",
        "Changed evidence.",
    )

    with pytest.raises(ValidationError, match="must match"):
        EvidenceExcerpt.model_validate(
            {
                **excerpt.model_dump(mode="python"),
                "text_sha256": "b" * 64,
            }
        )


def test_atomic_content_ledger_rejects_context_only_and_unknown_evidence() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    invalid_proposition = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-002",
        "Evidence belongs to another unit.",
    )
    invalid_ledger = ledgers[0].model_copy(
        update={"propositions": (invalid_proposition,)}
    )

    report = _audit(
        source_universe=universe,
        ledgers=(invalid_ledger, ledgers[1]),
    )

    assert report.context_only_proposition_count == 1
    assert report.invalid_source_reference_count == 1
    assert {finding.code for finding in report.findings} >= {
        "context_only_atomic_propositions",
        "invalid_atomic_source_references",
        "missing_instructional_source_span_inventory",
    }


def test_atomic_content_ledger_blocks_numeric_review_and_disagreement_gaps() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    proposition = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        "Heart rate below 40 bpm is concerning.",
    ).model_copy(
        update={
            "review_status": ReviewStatus.PENDING,
            "disagreements": (
                EvidenceDisagreement(
                    disagreement_id="disagreement-001",
                    description="ASR and OCR disagree on the threshold.",
                    source_span_ids=("span-001",),
                    status=DisagreementStatus.UNRESOLVED,
                ),
            ),
        }
    )

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(update={"propositions": (proposition,)}),
            ledgers[1],
        ),
    )

    assert report.unstructured_numeric_proposition_count == 1
    assert report.unverified_proposition_count == 1
    assert report.unresolved_disagreement_count == 1
    assert {finding.code for finding in report.findings} >= {
        "unstructured_numeric_propositions",
        "unverified_atomic_propositions",
        "unresolved_atomic_disagreements",
    }


def test_atomic_content_ledger_revalidates_unchecked_contracts() -> None:
    universe = _source_universe()
    ledger = AtomicContentLedger.model_construct(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256="a" * 64,
        propositions=(),
        relations=(),
    )

    with pytest.raises(ValidationError):
        _audit(
            source_universe=universe,
            ledgers=(ledger,),
        )


def test_numeric_quantity_rejects_reversed_range() -> None:
    with pytest.raises(ValidationError, match="must not be below"):
        NumericQuantity(
            quantity_id="quantity-001",
            role=QuantityRole.RANGE,
            comparator=QuantityComparator.RANGE,
            lower_value="10",
            upper_value="5",
            lower_boundary=NumericBoundaryKind.INCLUSIVE,
            upper_boundary=NumericBoundaryKind.INCLUSIVE,
            unit_literal="mg",
            unit_normalized="mg",
            source_span_ids=("span-001",),
            source_binding=_numeric_test_binding(range_quantity=True),
        )


def test_atomic_content_ledger_rejects_partial_numeric_inventory() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    proposition = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        "Give 10 mg every 8 hours.",
    ).model_copy(
        update={
            "numeric_quantities": (
                NumericQuantity(
                    quantity_id="quantity-001",
                    role=QuantityRole.DOSE,
                    comparator=QuantityComparator.EXACT,
                    lower_value="10",
                    lower_boundary=NumericBoundaryKind.NOT_APPLICABLE,
                    upper_boundary=NumericBoundaryKind.NOT_APPLICABLE,
                    unit_literal="mg",
                    unit_normalized="mg",
                    source_span_ids=("span-001",),
                    source_binding=_numeric_test_binding(),
                ),
            )
        }
    )

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(update={"propositions": (proposition,)}),
            ledgers[1],
        ),
    )

    assert report.unstructured_numeric_proposition_count == 1
    assert "unstructured_numeric_propositions" in {
        finding.code for finding in report.findings
    }


def test_atomic_content_ledger_rejects_numeric_overlap_across_propositions() -> None:
    source_text = "10 mg dose."
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    universe = _source_universe()
    first_span = universe.evidence_spans[0].model_copy(
        update={"content_sha256": source_digest}
    )
    universe = universe.model_copy(
        update={"evidence_spans": (first_span, universe.evidence_spans[1])}
    )
    source_universe_sha256 = canonical_source_universe_sha256(universe)
    numeric_clause = _source_clause("unit-001", "span-001", source_text)
    inventories = _valid_clause_inventories(universe)
    numeric_inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=source_universe_sha256,
        text_extents=(
            SourceTextExtent(
                source_span_id="span-001",
                source_content_sha256=source_digest,
                canonical_text=source_text,
                byte_length=len(source_text.encode("utf-8")),
            ),
        ),
        clauses=(numeric_clause,),
    )
    quantity = NumericQuantity(
        quantity_id="quantity-001",
        role=QuantityRole.DOSE,
        comparator=QuantityComparator.EXACT,
        lower_value="10",
        lower_boundary=NumericBoundaryKind.NOT_APPLICABLE,
        upper_boundary=NumericBoundaryKind.NOT_APPLICABLE,
        unit_literal="mg",
        unit_normalized="mg",
        source_span_ids=("span-001",),
        source_binding=_numeric_test_binding(),
    )
    first = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        source_text,
    ).model_copy(update={"numeric_quantities": (quantity,)})
    second_quantity = quantity.model_copy(update={"quantity_id": "quantity-002"})
    second = _proposition(
        2,
        "proposition-002-overlap",
        "unit-001",
        "span-001",
        source_text,
    ).model_copy(update={"numeric_quantities": (second_quantity,)})
    ledgers = _valid_ledgers(universe)
    first_ledger = ledgers[0].model_copy(update={"propositions": (first, second)})

    report = _audit(
        source_universe=universe,
        clause_inventories=(numeric_inventory, inventories[1]),
        ledgers=(first_ledger, ledgers[1]),
    )

    assert report.invalid_numeric_source_binding_count == 2
    assert report.unstructured_numeric_proposition_count >= 2
    assert "invalid_numeric_source_bindings" in {
        finding.code for finding in report.findings
    }


def test_atomic_content_ledger_rejects_omitted_source_number_without_quantity() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    source_text = "Give exactly 10 mg dose."
    numeric_clause = _source_clause("unit-001", "span-001", source_text)
    inventories = _valid_clause_inventories(universe)
    numeric_inventory = inventories[0].model_copy(
        update={
            "clauses": (numeric_clause,),
            "text_extents": (
                SourceTextExtent(
                    source_span_id="span-001",
                    source_content_sha256=numeric_clause.source_content_sha256,
                    canonical_text=source_text,
                    byte_length=len(source_text.encode("utf-8")),
                ),
            ),
        }
    )
    omitted = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        source_text,
    ).model_copy(
        update={"normalized_statement": "Give dose.", "numeric_quantities": ()}
    )

    report = _audit(
        source_universe=universe,
        clause_inventories=(numeric_inventory, inventories[1]),
        ledgers=(
            ledgers[0].model_copy(update={"propositions": (omitted,)}),
            ledgers[1],
        ),
    )

    assert report.unstructured_numeric_proposition_count == 1
    assert report.unverifiable_numeric_source_binding_count == 1
    assert not report.structurally_complete
    assert {
        "unstructured_numeric_propositions",
        "unverifiable_numeric_source_bindings",
    }.issubset({finding.code for finding in report.findings})


def test_human_resolved_disagreement_requires_authenticated_resolution() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    proposition = (
        ledgers[0]
        .propositions[0]
        .model_copy(
            update={
                "disagreements": (
                    EvidenceDisagreement(
                        disagreement_id="disagreement-001",
                        description="ASR and OCR disagree.",
                        source_span_ids=("span-001",),
                        status=DisagreementStatus.HUMAN_RESOLVED,
                        chosen_interpretation="The transcript value is correct.",
                        review_artifact_id="review-001",
                    ),
                )
            }
        )
    )

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(update={"propositions": (proposition,)}),
            ledgers[1],
        ),
    )

    assert report.unauthenticated_resolution_count == 1
    assert "unauthenticated_disagreement_resolutions" in {
        finding.code for finding in report.findings
    }


def test_atomic_content_ledger_rejects_duplicate_and_misowned_relations() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    first = PropositionRelation(
        relation_id="relation-001",
        source_proposition_id="proposition-002",
        relation_type=PropositionRelationType.SUPPORTS,
        target_proposition_id="proposition-001",
        source_span_ids=("span-002",),
    )
    duplicate_edge = first.model_copy(update={"relation_id": "relation-002"})

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(update={"relations": (first, duplicate_edge)}),
            ledgers[1],
        ),
    )

    assert report.unresolved_relation_count == 2
    assert {finding.code for finding in report.findings} >= {
        "unresolved_atomic_relations",
        "duplicate_atomic_relation_edges",
    }


def test_atomic_relation_provenance_accepts_the_exact_endpoint_union() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    relation = PropositionRelation(
        relation_id="relation-001",
        source_proposition_id="proposition-001",
        relation_type=PropositionRelationType.SUPPORTS,
        target_proposition_id="proposition-002",
        source_span_ids=("span-001", "span-002"),
    )

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(update={"relations": (relation,)}),
            ledgers[1],
        ),
    )

    assert report.unresolved_relation_count == 0
    assert "unresolved_atomic_relations" not in {
        finding.code for finding in report.findings
    }


@pytest.mark.parametrize(
    "source_span_ids",
    [
        ("span-001",),
        ("span-002",),
    ],
    ids=("source-endpoint-only", "target-endpoint-only"),
)
def test_atomic_relation_provenance_rejects_endpoint_subsets(
    source_span_ids: tuple[str, ...],
) -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    relation = PropositionRelation(
        relation_id="relation-001",
        source_proposition_id="proposition-001",
        relation_type=PropositionRelationType.SUPPORTS,
        target_proposition_id="proposition-002",
        source_span_ids=source_span_ids,
    )

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(update={"relations": (relation,)}),
            ledgers[1],
        ),
    )

    assert report.unresolved_relation_count == 1
    assert "unresolved_atomic_relations" in {
        finding.code for finding in report.findings
    }


def test_atomic_relation_provenance_rejects_spans_outside_endpoint_union() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    relation = PropositionRelation(
        relation_id="relation-001",
        source_proposition_id="proposition-001",
        relation_type=PropositionRelationType.SUPPORTS,
        target_proposition_id="proposition-002",
        source_span_ids=("span-unrelated",),
    )

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(update={"relations": (relation,)}),
            ledgers[1],
        ),
    )

    assert report.unresolved_relation_count == 1
    assert "unresolved_atomic_relations" in {
        finding.code for finding in report.findings
    }


def test_atomic_content_ledger_rejects_stale_source_content_hash() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    proposition = ledgers[0].propositions[0]
    stale_excerpt = proposition.evidence_excerpts[0].model_copy(
        update={"source_content_sha256": "b" * 64}
    )

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(
                update={
                    "propositions": (
                        proposition.model_copy(
                            update={"evidence_excerpts": (stale_excerpt,)}
                        ),
                    )
                }
            ),
            ledgers[1],
        ),
    )

    assert report.source_content_hash_mismatch_count == 1
    assert "source_content_hash_mismatch" in {
        finding.code for finding in report.findings
    }


def test_atomic_contracts_reject_unbounded_decimal_and_sequence_values() -> None:
    with pytest.raises(ValidationError):
        NumericQuantity(
            quantity_id="quantity-001",
            role=QuantityRole.DOSE,
            comparator=QuantityComparator.EXACT,
            lower_value="1e99999",
            lower_boundary=NumericBoundaryKind.NOT_APPLICABLE,
            upper_boundary=NumericBoundaryKind.NOT_APPLICABLE,
            unit_literal="mg",
            unit_normalized="mg",
            source_span_ids=("span-001",),
            source_binding=_numeric_test_binding(),
        )

    proposition = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        "Bradycardia is clinically relevant.",
    )
    with pytest.raises(ValidationError):
        AtomicProposition.model_validate(
            {**proposition.model_dump(mode="python"), "sequence": 1 << 20_000}
        )


def test_atomic_content_ledger_fails_closed_for_unsafe_statement_number() -> None:
    universe = _source_universe()
    ledgers = _valid_ledgers(universe)
    unsafe_statement = "Observed value 1e999999999999999999999999999999999999."
    proposition = _proposition(
        1,
        "proposition-001",
        "unit-001",
        "span-001",
        unsafe_statement,
    )

    report = _audit(
        source_universe=universe,
        ledgers=(
            ledgers[0].model_copy(update={"propositions": (proposition,)}),
            ledgers[1],
        ),
    )

    assert report.unstructured_numeric_proposition_count == 1
    assert "unstructured_numeric_propositions" in {
        finding.code for finding in report.findings
    }
