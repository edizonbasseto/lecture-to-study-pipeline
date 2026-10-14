from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from lecture_study.domain.content_ledger import (
    AssertionPolarity,
    AtomicContentType,
    CertaintyLevel,
    ClauseUtf8ByteRange,
    ClinicalFieldType,
    ContentDimension,
    ContentImportance,
    NumericBindingComponentState,
    NumericBoundaryKind,
    NumericImplicitJustification,
    NumericQuantity,
    NumericSourceBinding,
    NumericSourceComponentBinding,
    PropositionRelationType,
    QuantityComparator,
    QuantityRole,
    RecommendationStrength,
)
from lecture_study.domain.semantic_observation import (
    CandidateAtomicObservation,
    CandidateBatch,
    CandidateClauseObservation,
    CandidateComparison,
    CandidateDoseRegimen,
    CandidatePredicate,
    CandidateProducerIdentity,
    CandidateRelationObservation,
    CaptureState,
    ClauseDisposition,
    ClinicalValueObservation,
    ComparisonDirection,
    QuantityObservation,
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
from lecture_study.domain.source_universe import EvidenceModality

_UNIVERSE_DIGEST = "b" * 64
_INVENTORY_DIGEST = "c" * 64
_DOSE_CLAUSE_TEXT = "Administer drug A at 0.5 mg/kg orally every 12 h for 7 d."
_SOURCE_TEXT = "Low-salience example. " + _DOSE_CLAUSE_TEXT
_SOURCE_DIGEST = hashlib.sha256(_SOURCE_TEXT.encode("utf-8")).hexdigest()


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clause(
    clause_id: str,
    sequence: int,
    start_byte: int,
    text: str,
) -> SourceClause:
    end_byte = start_byte + len(text.encode("utf-8"))
    digest = _text_digest(text)
    return SourceClause(
        schema_version=1,
        clause_id=clause_id,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        sequence=sequence,
        source_span_id="span-001",
        source_content_sha256=_SOURCE_DIGEST,
        modality=EvidenceModality.TRANSCRIPT,
        selector=TextPartitionSelector(
            kind=SourcePartitionKind.TEXT_RANGE,
            start_byte=start_byte,
            end_byte=end_byte,
            selected_sha256=digest,
        ),
        verbatim_text=text,
        text_sha256=digest,
    )


def _inventory() -> SourceClauseInventory:
    first_text = "Low-salience example. "
    second_text = _DOSE_CLAUSE_TEXT
    clauses = (
        _clause("clause-001", 1, 0, first_text),
        _clause(
            "clause-002",
            2,
            len(first_text.encode("utf-8")),
            second_text,
        ),
    )
    return SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=_UNIVERSE_DIGEST,
        text_extents=(
            SourceTextExtent(
                source_span_id="span-001",
                source_content_sha256=_SOURCE_DIGEST,
                canonical_text=first_text + second_text,
                byte_length=len((first_text + second_text).encode("utf-8")),
            ),
        ),
        clauses=clauses,
    )


def _producer(model_character: str, producer_id: str) -> CandidateProducerIdentity:
    return CandidateProducerIdentity(
        producer_id=producer_id,
        manifest_sha256="d" * 64,
        model_sha256=model_character * 64,
        prompt_sha256="e" * 64,
        schema_sha256="f" * 64,
        implementation_sha256="1" * 64,
        configuration_sha256="2" * 64,
        engine_version="engine-1",
    )


def _clinical_value(
    value_id: str,
    field_type: ClinicalFieldType,
    value: str,
) -> ClinicalValueObservation:
    return ClinicalValueObservation(
        value_id=value_id,
        field_type=field_type,
        state=CaptureState.CAPTURED,
        literal_value=value,
        normalized_value=value.casefold(),
        source_clause_ids=("clause-002",),
    )


def _quantity(
    quantity_id: str,
    role: QuantityRole,
    value: str,
    unit: str,
) -> QuantityObservation:
    value_and_unit = f"{value} {unit}"
    role_token = {
        "0.5 mg/kg": "at",
        "12 h": "every",
        "7 d": "for",
    }[value_and_unit]
    exact_expression = f"{role_token} {value_and_unit}"
    expression_selector = _clause_byte_range(exact_expression)
    denominator = "kg" if unit == "mg/kg" else None
    return QuantityObservation(
        observation_id=f"observed-{quantity_id}",
        state=CaptureState.CAPTURED,
        quantity=NumericQuantity(
            quantity_id=quantity_id,
            role=role,
            comparator=QuantityComparator.EXACT,
            lower_value=value,
            lower_boundary=NumericBoundaryKind.NOT_APPLICABLE,
            upper_boundary=NumericBoundaryKind.NOT_APPLICABLE,
            unit_literal=unit,
            unit_normalized=unit,
            denominator=denominator,
            source_span_ids=("span-001",),
            source_binding=NumericSourceBinding(
                source_clause_id="clause-002",
                exact_expression=exact_expression,
                expression_selector=_explicit_component(expression_selector),
                lower_value_selector=_explicit_component(
                    _clause_byte_range(value, expression_selector.start_byte)
                ),
                upper_value_selector=_absent_component(),
                comparator_selector=_implicit_exact_comparator(),
                unit_selector=_explicit_component(
                    _clause_byte_range(unit, expression_selector.start_byte)
                ),
                denominator_selector=(
                    _explicit_component(
                        _clause_byte_range(
                            denominator,
                            expression_selector.start_byte,
                        )
                    )
                    if denominator is not None
                    else _absent_component()
                ),
                semantic_role_selector=_explicit_component(
                    _clause_byte_range(role_token)
                ),
                lower_boundary_selector=_absent_component(),
                upper_boundary_selector=_absent_component(),
            ),
        ),
        source_clause_ids=("clause-002",),
    )


def _clause_byte_range(
    selected_text: str,
    start_byte: int = 0,
) -> ClauseUtf8ByteRange:
    clause_bytes = _DOSE_CLAUSE_TEXT.encode("utf-8")
    selected_bytes = selected_text.encode("utf-8")
    selected_start = clause_bytes.index(selected_bytes, start_byte)
    return ClauseUtf8ByteRange(
        start_byte=selected_start,
        end_byte=selected_start + len(selected_bytes),
    )


def _explicit_component(
    selector: ClauseUtf8ByteRange,
) -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(
        state=NumericBindingComponentState.EXPLICIT,
        selector=selector,
    )


def _absent_component() -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(
        state=NumericBindingComponentState.ABSENT,
    )


def _implicit_exact_comparator() -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(
        state=NumericBindingComponentState.IMPLICIT,
        implicit_justification=NumericImplicitJustification(
            justification_id="implicit-exact-comparator",
            source_clause_id="clause-002",
            rationale="No comparator token is present in the exact source expression.",
        ),
    )


def _captured_candidate(
    candidate_id: str = "candidate-001",
) -> CandidateAtomicObservation:
    dose = _quantity("dose-001", QuantityRole.DOSE, "0.5", "mg/kg")
    interval = _quantity("interval-001", QuantityRole.INTERVAL, "12", "h")
    duration = _quantity("duration-001", QuantityRole.DURATION, "7", "d")
    medication = _clinical_value(
        "drug-001",
        ClinicalFieldType.DRUG,
        "drug A",
    )
    route = _clinical_value(
        "route-001",
        ClinicalFieldType.ROUTE,
        "oral",
    )
    return CandidateAtomicObservation(
        candidate_id=candidate_id,
        sequence=1,
        content_type=AtomicContentType.DOSE,
        importance=ContentImportance.CORE,
        normalized_statement="Administer drug A at 0.5 mg/kg orally every 12 h for 7 d.",
        predicate=CandidatePredicate(
            subject="drug A",
            predicate="administer",
            object_value="patient",
            source_clause_ids=("clause-002",),
        ),
        source_clause_ids=("clause-002",),
        clinical_values=(medication, route),
        quantities=(dose, interval, duration),
        dose_regimen=CandidateDoseRegimen(
            regimen_id="regimen-001",
            medication=medication,
            dose=dose,
            route=route,
            interval_or_frequency=interval,
            duration=duration,
            condition_candidate_ids=(),
        ),
        required_dimensions=frozenset(
            {ContentDimension.CORE_STATEMENT, ContentDimension.SAFETY}
        ),
        polarity=AssertionPolarity.AFFIRMED,
        certainty=CertaintyLevel.CERTAIN,
        recommendation_strength=RecommendationStrength.RECOMMENDED,
        safety_critical=True,
        extraction_confidence=1.0,
    )


def _batch(
    producer: CandidateProducerIdentity,
    candidate: CandidateAtomicObservation | None = None,
) -> CandidateBatch:
    proposition = candidate or _captured_candidate()
    return CandidateBatch(
        schema_version=1,
        batch_id=f"batch-{producer.producer_id}",
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=_UNIVERSE_DIGEST,
        clause_inventory_sha256=_INVENTORY_DIGEST,
        producer=producer,
        observations=(
            CandidateClauseObservation(
                observation_id=f"observation-{producer.producer_id}-001",
                sequence=1,
                clause_id="clause-001",
                producer_id=producer.producer_id,
                disposition=ClauseDisposition.PROPOSITIONAL,
                candidates=(
                    CandidateAtomicObservation(
                        candidate_id=f"low-salience-{producer.producer_id}",
                        sequence=1,
                        content_type=AtomicContentType.EXAMPLE,
                        importance=ContentImportance.EXAMPLE,
                        normalized_statement="The lecturer gives a low-salience example.",
                        predicate=CandidatePredicate(
                            subject="lecturer",
                            predicate="gives",
                            object_value="example",
                            source_clause_ids=("clause-001",),
                        ),
                        source_clause_ids=("clause-001",),
                        required_dimensions=frozenset(
                            {
                                ContentDimension.CORE_STATEMENT,
                                ContentDimension.EXAMPLES,
                            }
                        ),
                        polarity=AssertionPolarity.AFFIRMED,
                        certainty=CertaintyLevel.CERTAIN,
                        recommendation_strength=(RecommendationStrength.NOT_APPLICABLE),
                        safety_critical=False,
                        extraction_confidence=1.0,
                    ),
                ),
                extraction_confidence=1.0,
            ),
            CandidateClauseObservation(
                observation_id=f"observation-{producer.producer_id}-002",
                sequence=2,
                clause_id="clause-002",
                producer_id=producer.producer_id,
                disposition=ClauseDisposition.PROPOSITIONAL,
                candidates=(proposition,),
                extraction_confidence=1.0,
            ),
        ),
    )


def test_source_clause_inventory_requires_exact_text_partition() -> None:
    inventory = _inventory()

    assert len(inventory.clauses) == 2

    with pytest.raises(ValidationError, match="partition"):
        SourceClauseInventory.model_validate(
            {
                **inventory.model_dump(mode="python"),
                "clauses": (
                    inventory.clauses[0],
                    inventory.clauses[1].model_copy(
                        update={
                            "selector": inventory.clauses[1].selector.model_copy(
                                update={"start_byte": 25}
                            )
                        }
                    ),
                ),
            }
        )


def test_source_clause_inventory_rejects_coordinated_same_length_text_substitution() -> (
    None
):
    inventory = _inventory()
    first_selector = inventory.clauses[0].selector
    second_selector = inventory.clauses[1].selector
    assert isinstance(first_selector, TextPartitionSelector)
    assert isinstance(second_selector, TextPartitionSelector)
    first_length = first_selector.end_byte
    second_length = second_selector.end_byte - second_selector.start_byte
    substituted_clauses = (
        _clause("clause-001", 1, 0, "X" * first_length),
        _clause("clause-002", 2, first_length, "Y" * second_length),
    )

    with pytest.raises(
        ValidationError,
        match="source clause text must match authenticated canonical bytes",
    ):
        SourceClauseInventory.model_validate(
            {
                **inventory.model_dump(mode="python"),
                "clauses": substituted_clauses,
            }
        )


@pytest.mark.parametrize(
    "state,literal_value,normalized_value",
    [
        (CaptureState.CAPTURED, None, None),
        (CaptureState.NOT_STATED, "oral", "oral"),
        (CaptureState.AMBIGUOUS, "oral", None),
    ],
)
def test_capture_state_cannot_hide_or_invent_a_value(
    state: CaptureState,
    literal_value: str | None,
    normalized_value: str | None,
) -> None:
    with pytest.raises(ValidationError, match="captured"):
        ClinicalValueObservation(
            value_id="route-001",
            field_type=ClinicalFieldType.ROUTE,
            state=state,
            literal_value=literal_value,
            normalized_value=normalized_value,
            source_clause_ids=("clause-002",),
        )


def test_dose_regimen_is_indivisible_and_role_checked() -> None:
    candidate = _captured_candidate()

    assert candidate.dose_regimen is not None

    with pytest.raises(ValidationError, match="duration"):
        CandidateDoseRegimen.model_validate(
            {
                **candidate.dose_regimen.model_dump(mode="python"),
                "duration": _quantity(
                    "wrong-duration",
                    QuantityRole.INTERVAL,
                    "7",
                    "d",
                ),
            }
        )

    with pytest.raises(ValidationError, match="dose regimen"):
        CandidateAtomicObservation.model_validate(
            {
                **candidate.model_dump(mode="python"),
                "dose_regimen": None,
            }
        )


def test_dose_regimen_components_must_be_accounted_by_the_candidate() -> None:
    candidate = _captured_candidate()
    assert candidate.dose_regimen is not None
    unaccounted_route = _clinical_value(
        "unaccounted-route",
        ClinicalFieldType.ROUTE,
        "intravenous",
    )

    with pytest.raises(ValidationError, match="candidate clinical_values"):
        CandidateAtomicObservation.model_validate(
            {
                **candidate.model_dump(mode="python"),
                "dose_regimen": candidate.dose_regimen.model_copy(
                    update={"route": unaccounted_route}
                ),
            }
        )


def test_dose_regimen_condition_requires_an_exact_relation() -> None:
    producer = _producer("3", "producer-a")
    batch = _batch(producer)
    dose_candidate = batch.observations[1].candidates[0]
    assert dose_candidate.dose_regimen is not None
    conditioned_regimen = dose_candidate.dose_regimen.model_copy(
        update={"condition_candidate_ids": (f"low-salience-{producer.producer_id}",)}
    )
    conditioned_dose = dose_candidate.model_copy(
        update={"dose_regimen": conditioned_regimen}
    )
    conditioned_observation = batch.observations[1].model_copy(
        update={"candidates": (conditioned_dose,)}
    )
    payload = {
        **batch.model_dump(mode="python"),
        "observations": (batch.observations[0], conditioned_observation),
    }

    with pytest.raises(ValidationError, match="condition IDs require"):
        CandidateBatch.model_validate(payload)

    condition_relation = CandidateRelationObservation(
        relation_id="condition-relation",
        source_candidate_id=f"low-salience-{producer.producer_id}",
        relation_type=PropositionRelationType.CONDITION_FOR,
        target_candidate_id=conditioned_dose.candidate_id,
        source_clause_ids=("clause-001", "clause-002"),
    )
    linked = CandidateBatch.model_validate(
        {**payload, "relations": (condition_relation,)}
    )

    assert linked.relations == (condition_relation,)


def test_comparison_preserves_metric_and_direction() -> None:
    comparison = CandidateComparison(
        comparison_id="comparison-001",
        left_value="test A",
        right_value="test B",
        metric="sensitivity",
        direction=ComparisonDirection.HIGHER,
        source_clause_ids=("clause-001",),
    )
    candidate = CandidateAtomicObservation(
        candidate_id="comparison-candidate",
        sequence=1,
        content_type=AtomicContentType.COMPARISON,
        importance=ContentImportance.CORE,
        normalized_statement="Test A has higher sensitivity than test B.",
        predicate=CandidatePredicate(
            subject="test A",
            predicate="compare sensitivity",
            object_value="test B",
            source_clause_ids=("clause-001",),
        ),
        source_clause_ids=("clause-001",),
        comparisons=(comparison,),
        required_dimensions=frozenset(
            {ContentDimension.CORE_STATEMENT, ContentDimension.RELATIONSHIPS}
        ),
        polarity=AssertionPolarity.AFFIRMED,
        certainty=CertaintyLevel.CERTAIN,
        recommendation_strength=RecommendationStrength.NOT_APPLICABLE,
        safety_critical=False,
        extraction_confidence=1.0,
    )

    assert candidate.comparisons == (comparison,)

    with pytest.raises(ValidationError, match="comparison content"):
        CandidateAtomicObservation.model_validate(
            {
                **candidate.model_dump(mode="python"),
                "comparisons": (),
            }
        )


def test_conditions_exceptions_and_corrections_are_linked_propositions() -> None:
    producer = _producer("3", "producer-a")
    batch = _batch(producer)
    relation = CandidateRelationObservation(
        relation_id="relation-001",
        source_candidate_id="candidate-001",
        relation_type=PropositionRelationType.CONDITION_FOR,
        target_candidate_id=f"low-salience-{producer.producer_id}",
        source_clause_ids=("clause-001", "clause-002"),
    )

    linked = batch.model_copy(update={"relations": (relation,)})

    assert linked.relations[0].relation_type is PropositionRelationType.CONDITION_FOR


def test_candidate_batch_rejects_unknown_relation_endpoints() -> None:
    batch = _batch(_producer("3", "producer-a"))
    invalid_relation = CandidateRelationObservation(
        relation_id="relation-001",
        source_candidate_id="candidate-001",
        relation_type=PropositionRelationType.CAUSES,
        target_candidate_id="missing-candidate",
        source_clause_ids=("clause-002",),
    )

    with pytest.raises(ValidationError, match="known candidate"):
        CandidateBatch.model_validate(
            {
                **batch.model_dump(mode="python"),
                "relations": (invalid_relation,),
            }
        )


def _visual_processing_identity(
    implementation_character: str = "7",
    configuration_character: str = "8",
) -> GeneratedObservationIdentity:
    return GeneratedObservationIdentity(
        implementation_sha256=implementation_character * 64,
        configuration_sha256=configuration_character * 64,
    )


def _full_frame_visual_clause(
    *,
    clause_id: str = "visual-full-frame",
    sequence: int = 1,
    observation_text: str = "The frame shows a labeled cardiac diagram.",
    generated_observation_identity: GeneratedObservationIdentity | None = None,
) -> SourceClause:
    observation_identity = (
        generated_observation_identity
        if generated_observation_identity is not None
        else _visual_processing_identity()
    )
    return SourceClause(
        schema_version=1,
        clause_id=clause_id,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        sequence=sequence,
        source_span_id="visual-span-001",
        source_content_sha256=_SOURCE_DIGEST,
        modality=EvidenceModality.VISUAL,
        selector=VisualFullFrameSelector(
            kind=SourcePartitionKind.VISUAL_FULL_FRAME,
            original_frame_sha256=_SOURCE_DIGEST,
        ),
        observed_text=observation_text,
        text_sha256=_text_digest(observation_text),
        generated_observation_identity=observation_identity,
    )


def _region_selector() -> VisualPartitionSelector:
    hashing_identity = _visual_processing_identity("9", "a")
    coordinates = (0.1, 0.2, 0.8, 0.9)
    region_content_sha256 = "d" * 64
    return VisualPartitionSelector(
        kind=SourcePartitionKind.VISUAL_REGION,
        original_frame_sha256=_SOURCE_DIGEST,
        left=coordinates[0],
        top=coordinates[1],
        right=coordinates[2],
        bottom=coordinates[3],
        selected_sha256=region_content_sha256,
        hashing_identity=hashing_identity,
        region_binding_sha256=canonical_visual_region_binding_sha256(
            original_frame_sha256=_SOURCE_DIGEST,
            coordinates=coordinates,
            region_content_sha256=region_content_sha256,
            hashing_identity=hashing_identity,
        ),
    )


def _region_visual_clause(
    *,
    clause_id: str = "visual-region-001",
    sequence: int = 2,
    selector: VisualPartitionSelector | None = None,
) -> SourceClause:
    observation_text = "The highlighted region contains the left atrium label."
    return SourceClause(
        schema_version=1,
        clause_id=clause_id,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        sequence=sequence,
        source_span_id="visual-span-001",
        source_content_sha256=_SOURCE_DIGEST,
        modality=EvidenceModality.VISUAL,
        selector=selector or _region_selector(),
        observed_text=observation_text,
        text_sha256=_text_digest(observation_text),
        generated_observation_identity=_visual_processing_identity(),
    )


def _visual_extent() -> SourceVisualExtent:
    return SourceVisualExtent(
        source_span_id="visual-span-001",
        source_content_sha256=_SOURCE_DIGEST,
    )


def test_every_visual_extent_requires_at_least_one_full_frame_observation() -> None:
    with pytest.raises(ValidationError, match="full-frame"):
        SourceClauseInventory(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            source_universe_sha256=_UNIVERSE_DIGEST,
            visual_extents=(_visual_extent(),),
            clauses=(_region_visual_clause(sequence=1),),
        )


def test_visual_extent_accepts_multiple_full_frame_observations() -> None:
    first_full_frame = _full_frame_visual_clause()
    second_full_frame = _full_frame_visual_clause(
        clause_id="visual-full-frame-second",
        sequence=2,
        observation_text="A second generated observation of the full frame.",
        generated_observation_identity=_visual_processing_identity("9", "a"),
    )

    inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=_UNIVERSE_DIGEST,
        visual_extents=(_visual_extent(),),
        clauses=(first_full_frame, second_full_frame),
    )

    assert inventory.clauses == (first_full_frame, second_full_frame)
    assert (
        first_full_frame.generated_observation_identity
        != second_full_frame.generated_observation_identity
    )


def test_visual_observation_cannot_omit_its_declared_extent() -> None:
    with pytest.raises(ValidationError, match="visual extent"):
        SourceClauseInventory(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            source_universe_sha256=_UNIVERSE_DIGEST,
            clauses=(_full_frame_visual_clause(),),
        )


def test_visual_extent_cannot_be_reclassified_as_text_evidence() -> None:
    with pytest.raises(ValidationError, match="visual"):
        SourceVisualExtent.model_validate(
            {
                "source_span_id": "visual-span-001",
                "source_content_sha256": _SOURCE_DIGEST,
                "modality": EvidenceModality.TRANSCRIPT,
            }
        )


@pytest.mark.parametrize(
    "replacement",
    [
        {"left": 0.15},
        {"selected_sha256": "e" * 64},
        {"hashing_identity": _visual_processing_identity("b", "c")},
        {"original_frame_sha256": "f" * 64},
    ],
)
def test_visual_region_binding_rejects_substitution(
    replacement: dict[str, object],
) -> None:
    selector = _region_selector()

    with pytest.raises(ValidationError, match="region binding"):
        VisualPartitionSelector.model_validate(
            {**selector.model_dump(mode="python"), **replacement}
        )


def test_visual_frame_hash_substitution_is_rejected() -> None:
    clause = _full_frame_visual_clause()

    with pytest.raises(ValidationError, match="original frame"):
        SourceClause.model_validate(
            {
                **clause.model_dump(mode="python"),
                "source_content_sha256": "f" * 64,
            }
        )


def test_generated_visual_and_ocr_text_are_never_verbatim() -> None:
    visual_clause = _full_frame_visual_clause()
    with pytest.raises(ValidationError, match="visual.*verbatim"):
        SourceClause.model_validate(
            {
                **visual_clause.model_dump(mode="python"),
                "verbatim_text": visual_clause.observed_text,
            }
        )

    ocr_text = "Observed OCR label"
    with pytest.raises(ValidationError, match="OCR.*verbatim"):
        SourceClause(
            schema_version=1,
            clause_id="ocr-clause",
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            sequence=1,
            source_span_id="ocr-span",
            source_content_sha256=_SOURCE_DIGEST,
            modality=EvidenceModality.OCR,
            selector=TextPartitionSelector(
                kind=SourcePartitionKind.TEXT_RANGE,
                start_byte=0,
                end_byte=len(ocr_text.encode("utf-8")),
                selected_sha256=_text_digest(ocr_text),
            ),
            verbatim_text=ocr_text,
            observed_text=ocr_text,
            text_sha256=_text_digest(ocr_text),
            generated_observation_identity=_visual_processing_identity(),
        )


def test_ocr_observation_is_explicitly_distinct_from_transcript_verbatim() -> None:
    ocr_text = "Observed OCR label"
    generator_identity = _visual_processing_identity()
    clause = SourceClause(
        schema_version=1,
        clause_id="ocr-clause",
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        sequence=1,
        source_span_id="ocr-span",
        source_content_sha256=_SOURCE_DIGEST,
        modality=EvidenceModality.OCR,
        selector=TextPartitionSelector(
            kind=SourcePartitionKind.TEXT_RANGE,
            start_byte=0,
            end_byte=len(ocr_text.encode("utf-8")),
            selected_sha256=_text_digest(ocr_text),
        ),
        observed_text=ocr_text,
        text_sha256=_text_digest(ocr_text),
        generated_observation_identity=generator_identity,
    )

    assert clause.verbatim_text is None
    assert clause.observed_text == ocr_text
    assert clause.generated_observation_identity == generator_identity


def test_ocr_observation_requires_generator_identity() -> None:
    ocr_text = "Observed OCR label"

    with pytest.raises(ValidationError, match="OCR.*generated observation identity"):
        SourceClause(
            schema_version=1,
            clause_id="ocr-clause",
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            sequence=1,
            source_span_id="ocr-span",
            source_content_sha256=_SOURCE_DIGEST,
            modality=EvidenceModality.OCR,
            selector=TextPartitionSelector(
                kind=SourcePartitionKind.TEXT_RANGE,
                start_byte=0,
                end_byte=len(ocr_text.encode("utf-8")),
                selected_sha256=_text_digest(ocr_text),
            ),
            observed_text=ocr_text,
            text_sha256=_text_digest(ocr_text),
        )


def test_ocr_extent_cannot_be_reclassified_as_transcript_verbatim() -> None:
    ocr_text = "Observed OCR label"
    ocr_digest = _text_digest(ocr_text)
    clause = SourceClause(
        schema_version=1,
        clause_id="ocr-clause",
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        sequence=1,
        source_span_id="ocr-span",
        source_content_sha256=ocr_digest,
        modality=EvidenceModality.OCR,
        selector=TextPartitionSelector(
            kind=SourcePartitionKind.TEXT_RANGE,
            start_byte=0,
            end_byte=len(ocr_text.encode("utf-8")),
            selected_sha256=_text_digest(ocr_text),
        ),
        observed_text=ocr_text,
        text_sha256=_text_digest(ocr_text),
        generated_observation_identity=_visual_processing_identity(),
    )

    with pytest.raises(ValidationError, match="modality"):
        SourceClauseInventory(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            source_universe_sha256=_UNIVERSE_DIGEST,
            text_extents=(
                SourceTextExtent(
                    source_span_id="ocr-span",
                    source_content_sha256=ocr_digest,
                    canonical_text=ocr_text,
                    byte_length=len(ocr_text.encode("utf-8")),
                ),
            ),
            clauses=(clause,),
        )

    inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=_UNIVERSE_DIGEST,
        text_extents=(
            SourceTextExtent(
                source_span_id="ocr-span",
                source_content_sha256=ocr_digest,
                modality=EvidenceModality.OCR,
                canonical_text=ocr_text,
                byte_length=len(ocr_text.encode("utf-8")),
            ),
        ),
        clauses=(clause,),
    )

    assert inventory.text_extents[0].modality is EvidenceModality.OCR


def test_duplicate_visual_regions_are_rejected() -> None:
    duplicate_region = _region_visual_clause(
        clause_id="visual-region-duplicate",
        sequence=3,
    )

    with pytest.raises(ValidationError, match="visual region selectors"):
        SourceClauseInventory(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            source_universe_sha256=_UNIVERSE_DIGEST,
            visual_extents=(_visual_extent(),),
            clauses=(
                _full_frame_visual_clause(),
                _region_visual_clause(),
                duplicate_region,
            ),
        )


def test_duplicate_visual_region_coordinates_cannot_hide_behind_new_hashes() -> None:
    coordinates = (0.1, 0.2, 0.8, 0.9)
    alternate_identity = _visual_processing_identity("e", "f")
    alternate_region_hash = "1" * 64
    substituted_selector = VisualPartitionSelector(
        kind=SourcePartitionKind.VISUAL_REGION,
        original_frame_sha256=_SOURCE_DIGEST,
        left=coordinates[0],
        top=coordinates[1],
        right=coordinates[2],
        bottom=coordinates[3],
        selected_sha256=alternate_region_hash,
        hashing_identity=alternate_identity,
        region_binding_sha256=canonical_visual_region_binding_sha256(
            original_frame_sha256=_SOURCE_DIGEST,
            coordinates=coordinates,
            region_content_sha256=alternate_region_hash,
            hashing_identity=alternate_identity,
        ),
    )
    duplicate_coordinates = _region_visual_clause(
        clause_id="visual-region-substituted",
        sequence=3,
        selector=substituted_selector,
    )

    with pytest.raises(ValidationError, match="visual region selectors"):
        SourceClauseInventory(
            schema_version=1,
            lecture_id="lecture-001",
            learning_unit_id="unit-001",
            source_universe_sha256=_UNIVERSE_DIGEST,
            visual_extents=(_visual_extent(),),
            clauses=(
                _full_frame_visual_clause(),
                _region_visual_clause(),
                duplicate_coordinates,
            ),
        )


def test_visual_extent_accepts_unique_optional_region_observations() -> None:
    inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=_UNIVERSE_DIGEST,
        visual_extents=(_visual_extent(),),
        clauses=(_full_frame_visual_clause(), _region_visual_clause()),
    )

    assert len(inventory.clauses) == 2
    assert inventory.clauses[1].verbatim_text is None


def test_visual_observation_is_compatible_with_semantic_candidate_accounting() -> None:
    visual_clause = _full_frame_visual_clause()
    assert visual_clause.observed_text is not None
    inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=_UNIVERSE_DIGEST,
        visual_extents=(_visual_extent(),),
        clauses=(visual_clause,),
    )
    candidate = CandidateAtomicObservation(
        candidate_id="visual-candidate",
        sequence=1,
        content_type=AtomicContentType.PRACTICAL_OBSERVATION,
        importance=ContentImportance.CORE,
        normalized_statement=visual_clause.observed_text,
        predicate=CandidatePredicate(
            subject="cardiac diagram",
            predicate="shows",
            object_value="labeled anatomy",
            source_clause_ids=(visual_clause.clause_id,),
        ),
        source_clause_ids=(visual_clause.clause_id,),
        required_dimensions=frozenset(
            {ContentDimension.CORE_STATEMENT, ContentDimension.RELATIONSHIPS}
        ),
        polarity=AssertionPolarity.AFFIRMED,
        certainty=CertaintyLevel.CERTAIN,
        recommendation_strength=RecommendationStrength.NOT_APPLICABLE,
        safety_critical=False,
        extraction_confidence=1.0,
    )
    producer = _producer("3", "visual-producer")
    batch = CandidateBatch(
        schema_version=1,
        batch_id="visual-batch",
        lecture_id=inventory.lecture_id,
        learning_unit_id=inventory.learning_unit_id,
        source_universe_sha256=inventory.source_universe_sha256,
        clause_inventory_sha256=_INVENTORY_DIGEST,
        producer=producer,
        observations=(
            CandidateClauseObservation(
                observation_id="visual-observation",
                sequence=1,
                clause_id=visual_clause.clause_id,
                producer_id=producer.producer_id,
                disposition=ClauseDisposition.PROPOSITIONAL,
                candidates=(candidate,),
                extraction_confidence=1.0,
            ),
        ),
    )

    assert batch.observations[0].clause_id == visual_clause.clause_id
    assert visual_clause.verbatim_text is None
