"""Domain model validation tests."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.models import (
    CanonicalFrame,
    CanonicalLectureDocument,
    CriticReview,
    EvidencePack,
    LectureExtractionResult,
    LectureGlobalIndex,
    LectureMetadata,
    LearningUnitDraft,
    LectureTranscriptionResult,
    SemanticFact,
    SemanticLedger,
    TranscriptionSegment,
    ValidatedLearningUnit,
)


def _make_frame(timestamp_sec: float = 5.0) -> CanonicalFrame:
    return CanonicalFrame(
        timestamp_sec=timestamp_sec,
        ocr_text="Slide text",
        image_path=Path("workspace/data/04_canonical/frame-001.png"),
    )


def _make_segment(
    start_time: float = 0.0, end_time: float = 5.0
) -> TranscriptionSegment:
    return TranscriptionSegment(
        start_time=start_time, end_time=end_time, text="Fala transcrita."
    )


def _make_evidence_pack(**overrides: object) -> EvidencePack:
    defaults: dict[object, object] = dict(
        lecture_id="aula-001",
        pack_sequence=1,
        start_time=0.0,
        end_time=10.0,
        anchor_frame=_make_frame(5.0),
        segments=[_make_segment()],
    )
    defaults.update(overrides)
    return EvidencePack(**defaults)


def test_evidence_pack_without_data_raises_validation_error():
    with pytest.raises(ValidationError):
        EvidencePack()


def test_lecture_metadata_rejects_string_for_float_field():
    with pytest.raises(ValidationError):
        LectureMetadata(
            id="aula-001",
            title="Cardiologia Veterinaria I",
            subject="Cardiologia",
            url="https://example.com/aulas/aula-001",
            estimated_duration_sec="3600.0",
        )


def test_lecture_metadata_rejects_empty_id():
    with pytest.raises(ValidationError):
        LectureMetadata(
            id="",
            title="Cardiologia Veterinaria I",
            subject="Cardiologia",
            url="https://example.com/aulas/aula-001",
            estimated_duration_sec=3600.0,
        )


def test_lecture_metadata_rejects_path_traversal_id():
    with pytest.raises(ValidationError):
        LectureMetadata(
            id="../../etc/passwd",
            title="Cardiologia Veterinaria I",
            subject="Cardiologia",
            url="https://example.com/aulas/aula-001",
            estimated_duration_sec=3600.0,
        )


def test_lecture_metadata_rejects_path_separator_in_id():
    with pytest.raises(ValidationError):
        LectureMetadata(
            id="aula/001",
            title="Cardiologia Veterinaria I",
            subject="Cardiologia",
            url="https://example.com/aulas/aula-001",
            estimated_duration_sec=3600.0,
        )


def test_canonical_frame_rejects_int_for_string_field():
    with pytest.raises(ValidationError):
        CanonicalFrame(
            timestamp_sec=5.0,
            ocr_text=123,
            image_path=Path("workspace/data/04_canonical/frame-001.png"),
        )


def test_canonical_frame_rejects_string_for_float_field():
    with pytest.raises(ValidationError):
        CanonicalFrame(
            timestamp_sec="5.0",
            ocr_text="Slide text",
            image_path=Path("workspace/data/04_canonical/frame-001.png"),
        )


def test_canonical_frame_accepts_valid_data():
    frame = _make_frame(5.0)
    assert frame.timestamp_sec == 5.0
    assert frame.image_path == Path("workspace/data/04_canonical/frame-001.png")


def test_canonical_frame_path_round_trips_through_model_validate_and_json():
    frame = _make_frame(5.0)

    from_json = CanonicalFrame.model_validate_json(frame.model_dump_json())
    from_dict = CanonicalFrame.model_validate(json.loads(frame.model_dump_json()))

    assert from_json.image_path == frame.image_path
    assert from_dict.image_path == frame.image_path


def test_canonical_frame_rejects_int_for_path_field():
    with pytest.raises(ValidationError):
        CanonicalFrame(timestamp_sec=5.0, ocr_text="Slide text", image_path=123)


def test_semantic_fact_accepts_valid_data():
    fact = SemanticFact(
        entity_name="Bradicardia",
        category="Achado clinico",
        clinical_context="Frequencia cardiaca reduzida observada no exame.",
        timestamp_ref=42.5,
    )
    assert fact.entity_name == "Bradicardia"


def test_semantic_fact_rejects_string_for_float_field():
    with pytest.raises(ValidationError):
        SemanticFact(
            entity_name="Bradicardia",
            category="Achado clinico",
            clinical_context="Frequencia cardiaca reduzida observada no exame.",
            timestamp_ref="42.5",
        )


def test_semantic_ledger_accepts_valid_data_with_facts():
    ledger = SemanticLedger(
        lecture_id="aula-001",
        pack_sequence=1,
        facts=[
            SemanticFact(
                entity_name="Bradicardia",
                category="Achado clinico",
                clinical_context="Frequencia cardiaca reduzida observada no exame.",
                timestamp_ref=42.5,
            )
        ],
    )
    assert ledger.lecture_id == "aula-001"
    assert ledger.pack_sequence == 1
    assert len(ledger.facts) == 1


def test_semantic_ledger_accepts_empty_facts_list():
    """A pack with no clinical content (introduction, joke, transition
    slide) is a valid, not an error, outcome."""
    ledger = SemanticLedger(lecture_id="aula-001", pack_sequence=1, facts=[])
    assert ledger.facts == []


def test_semantic_ledger_defaults_facts_to_empty_list():
    ledger = SemanticLedger(lecture_id="aula-001", pack_sequence=1)
    assert ledger.facts == []


def test_semantic_ledger_rejects_path_traversal_lecture_id():
    with pytest.raises(ValidationError):
        SemanticLedger(lecture_id="../../etc/passwd", pack_sequence=1, facts=[])


def test_semantic_ledger_rejects_zero_pack_sequence():
    with pytest.raises(ValidationError):
        SemanticLedger(lecture_id="aula-001", pack_sequence=0, facts=[])


def test_evidence_pack_accepts_valid_data():
    pack = _make_evidence_pack()
    assert pack.lecture_id == "aula-001"
    assert pack.pack_sequence == 1
    assert pack.anchor_frame is not None
    assert pack.anchor_frame.timestamp_sec == 5.0
    assert pack.segments[0].text == "Fala transcrita."


def test_evidence_pack_rejects_end_time_not_greater_than_start_time():
    with pytest.raises(ValidationError):
        _make_evidence_pack(start_time=10.0, end_time=10.0)


def test_evidence_pack_accepts_none_anchor_frame():
    pack = _make_evidence_pack(anchor_frame=None)
    assert pack.anchor_frame is None


def test_evidence_pack_accepts_empty_segments():
    pack = _make_evidence_pack(segments=[])
    assert pack.segments == []


def test_evidence_pack_rejects_anchor_frame_timestamp_outside_window():
    with pytest.raises(ValidationError):
        _make_evidence_pack(
            start_time=0.0, end_time=10.0, anchor_frame=_make_frame(15.0)
        )


def test_evidence_pack_rejects_anchor_frame_timestamp_at_window_boundary():
    with pytest.raises(ValidationError):
        _make_evidence_pack(
            start_time=0.0, end_time=10.0, anchor_frame=_make_frame(10.0)
        )


def test_evidence_pack_accepts_anchor_frame_at_start_time_boundary():
    pack = _make_evidence_pack(
        start_time=0.0, end_time=10.0, anchor_frame=_make_frame(0.0)
    )
    assert pack.anchor_frame is not None
    assert pack.anchor_frame.timestamp_sec == 0.0


def test_evidence_pack_rejects_empty_lecture_id():
    with pytest.raises(ValidationError):
        _make_evidence_pack(lecture_id="")


def test_evidence_pack_rejects_path_traversal_lecture_id():
    with pytest.raises(ValidationError):
        _make_evidence_pack(lecture_id="../../etc/passwd")


def test_evidence_pack_rejects_pack_sequence_below_one():
    with pytest.raises(ValidationError):
        _make_evidence_pack(pack_sequence=0)


def _make_extraction_result(**overrides: object) -> LectureExtractionResult:
    defaults: dict[object, object] = dict(
        lecture_id="aula-001",
        audio_path=Path("workspace/data/02_extracted_audio/aula-001.wav"),
        frames=[_make_frame(5.0)],
    )
    defaults.update(overrides)
    return LectureExtractionResult(**defaults)


def test_lecture_extraction_result_accepts_valid_data():
    result = _make_extraction_result()
    assert result.lecture_id == "aula-001"
    assert result.audio_path == Path("workspace/data/02_extracted_audio/aula-001.wav")
    assert result.frames[0].timestamp_sec == 5.0


def test_lecture_extraction_result_accepts_empty_frames():
    result = _make_extraction_result(frames=[])
    assert result.frames == []


def test_lecture_extraction_result_rejects_empty_lecture_id():
    with pytest.raises(ValidationError):
        _make_extraction_result(lecture_id="")


def test_lecture_extraction_result_rejects_path_traversal_lecture_id():
    with pytest.raises(ValidationError):
        _make_extraction_result(lecture_id="../../etc/passwd")


def test_lecture_extraction_result_rejects_int_for_lecture_id():
    with pytest.raises(ValidationError):
        _make_extraction_result(lecture_id=123)


def test_lecture_extraction_result_rejects_int_for_audio_path():
    with pytest.raises(ValidationError):
        _make_extraction_result(audio_path=123)


def test_lecture_extraction_result_rejects_int_for_frames():
    with pytest.raises(ValidationError):
        _make_extraction_result(frames=123)


def test_lecture_extraction_result_path_round_trips_through_model_validate_and_json():
    result = _make_extraction_result()

    from_json = LectureExtractionResult.model_validate_json(result.model_dump_json())
    from_dict = LectureExtractionResult.model_validate(
        json.loads(result.model_dump_json())
    )

    assert from_json.audio_path == result.audio_path
    assert from_dict.audio_path == result.audio_path


def _make_transcription_result(**overrides: object) -> LectureTranscriptionResult:
    defaults: dict[object, object] = dict(
        lecture_id="aula-001",
        frames=[_make_frame(5.0)],
        segments=[_make_segment()],
    )
    defaults.update(overrides)
    return LectureTranscriptionResult(**defaults)


def test_transcription_segment_accepts_valid_data():
    segment = _make_segment(1.5, 9.0)
    assert segment.start_time == 1.5
    assert segment.end_time == 9.0
    assert segment.text == "Fala transcrita."


def test_transcription_segment_rejects_string_for_float_field():
    with pytest.raises(ValidationError):
        TranscriptionSegment(start_time="0.0", end_time=5.0, text="Fala transcrita.")


def test_transcription_segment_rejects_int_for_text_field():
    with pytest.raises(ValidationError):
        TranscriptionSegment(start_time=0.0, end_time=5.0, text=123)


def test_transcription_segment_rejects_end_time_before_start_time():
    with pytest.raises(ValidationError):
        TranscriptionSegment(start_time=5.0, end_time=1.0, text="Fala transcrita.")


def test_transcription_segment_rejects_negative_start_time():
    with pytest.raises(ValidationError):
        TranscriptionSegment(start_time=-1.0, end_time=1.0, text="Fala transcrita.")


def test_transcription_segment_accepts_zero_duration():
    segment = TranscriptionSegment(
        start_time=5.0, end_time=5.0, text="Fala transcrita."
    )
    assert segment.start_time == segment.end_time == 5.0


def test_lecture_transcription_result_accepts_valid_data():
    result = _make_transcription_result()
    assert result.lecture_id == "aula-001"
    assert result.frames[0].timestamp_sec == 5.0
    assert result.segments[0].text == "Fala transcrita."


def test_lecture_transcription_result_accepts_empty_frames_and_segments():
    result = _make_transcription_result(frames=[], segments=[])
    assert result.frames == []
    assert result.segments == []


def test_lecture_transcription_result_rejects_empty_lecture_id():
    with pytest.raises(ValidationError):
        _make_transcription_result(lecture_id="")


def test_lecture_transcription_result_rejects_path_traversal_lecture_id():
    with pytest.raises(ValidationError):
        _make_transcription_result(lecture_id="../../etc/passwd")


def test_lecture_transcription_result_rejects_int_for_lecture_id():
    with pytest.raises(ValidationError):
        _make_transcription_result(lecture_id=123)


def test_lecture_transcription_result_rejects_int_for_frames():
    with pytest.raises(ValidationError):
        _make_transcription_result(frames=123)


def test_lecture_transcription_result_rejects_int_for_segments():
    with pytest.raises(ValidationError):
        _make_transcription_result(segments=123)


def test_lecture_transcription_result_path_round_trips_through_model_validate_and_json():
    result = _make_transcription_result()

    from_json = LectureTranscriptionResult.model_validate_json(result.model_dump_json())
    from_dict = LectureTranscriptionResult.model_validate(
        json.loads(result.model_dump_json())
    )

    assert from_json.segments == result.segments
    assert from_dict.segments == result.segments


def _make_learning_unit_draft(**overrides: object) -> LearningUnitDraft:
    defaults: dict[str, object] = dict(
        lecture_id="aula-001",
        pack_sequence=1,
        title="Bradicardia sinusal",
        body_markdown="## Bradicardia sinusal\n\nAchado clinico relevante.",
        clinical_warnings=["FC 40bpm: risco de instabilidade hemodinamica."],
    )
    defaults.update(overrides)
    return LearningUnitDraft(**defaults)


def test_learning_unit_draft_accepts_valid_data():
    draft = _make_learning_unit_draft()
    assert draft.lecture_id == "aula-001"
    assert draft.pack_sequence == 1
    assert draft.title == "Bradicardia sinusal"
    assert "Bradicardia sinusal" in draft.body_markdown
    assert draft.clinical_warnings == ["FC 40bpm: risco de instabilidade hemodinamica."]


def test_learning_unit_draft_defaults_clinical_warnings_to_empty_list():
    draft = _make_learning_unit_draft(clinical_warnings=[])
    assert draft.clinical_warnings == []


def test_learning_unit_draft_rejects_path_traversal_lecture_id():
    with pytest.raises(ValidationError):
        _make_learning_unit_draft(lecture_id="../../etc/passwd")


def test_learning_unit_draft_rejects_pack_sequence_below_one():
    with pytest.raises(ValidationError):
        _make_learning_unit_draft(pack_sequence=0)


def test_learning_unit_draft_rejects_int_for_title():
    with pytest.raises(ValidationError):
        _make_learning_unit_draft(title=123)


def test_learning_unit_draft_rejects_int_for_clinical_warnings_items():
    with pytest.raises(ValidationError):
        _make_learning_unit_draft(clinical_warnings=[1, 2])


def _make_critic_review(**overrides: object) -> CriticReview:
    defaults: dict[str, object] = dict(
        passed=True,
        hallucination_score=0.0,
        feedback="Todas as afirmacoes estao lastreadas nas evidencias.",
    )
    defaults.update(overrides)
    return CriticReview(**defaults)


def test_critic_review_accepts_valid_passing_data():
    review = _make_critic_review()
    assert review.passed is True
    assert review.hallucination_score == 0.0
    assert "lastreadas" in review.feedback


def test_critic_review_accepts_valid_failing_data():
    review = _make_critic_review(
        passed=False,
        hallucination_score=0.8,
        feedback="FC de 60bpm nao consta em nenhuma evidencia ou ledger.",
    )
    assert review.passed is False
    assert review.hallucination_score == 0.8


def test_critic_review_rejects_hallucination_score_above_one():
    with pytest.raises(ValidationError):
        _make_critic_review(hallucination_score=1.1)


def test_critic_review_rejects_hallucination_score_below_zero():
    with pytest.raises(ValidationError):
        _make_critic_review(hallucination_score=-0.1)


def test_critic_review_accepts_hallucination_score_boundaries():
    assert _make_critic_review(hallucination_score=0.0).hallucination_score == 0.0
    assert _make_critic_review(hallucination_score=1.0).hallucination_score == 1.0


def test_critic_review_rejects_string_for_passed_field():
    with pytest.raises(ValidationError):
        _make_critic_review(passed="true")


def test_critic_review_rejects_int_for_feedback_field():
    with pytest.raises(ValidationError):
        _make_critic_review(feedback=123)


def _make_validated_learning_unit(**overrides: object) -> ValidatedLearningUnit:
    defaults: dict[str, object] = dict(
        lecture_id="aula-001",
        pack_sequence=1,
        content=_make_learning_unit_draft(),
        review=_make_critic_review(),
    )
    defaults.update(overrides)
    return ValidatedLearningUnit(**defaults)


def test_validated_learning_unit_accepts_valid_data():
    unit = _make_validated_learning_unit()
    assert unit.lecture_id == "aula-001"
    assert unit.pack_sequence == 1
    assert unit.content.title == "Bradicardia sinusal"
    assert unit.review.passed is True


def test_validated_learning_unit_preserves_failed_review():
    unit = _make_validated_learning_unit(
        review=_make_critic_review(passed=False, feedback="Retries exhausted.")
    )
    assert unit.review.passed is False
    assert unit.review.feedback == "Retries exhausted."


def test_validated_learning_unit_rejects_path_traversal_lecture_id():
    with pytest.raises(ValidationError):
        _make_validated_learning_unit(lecture_id="../../etc/passwd")


def test_validated_learning_unit_rejects_pack_sequence_below_one():
    with pytest.raises(ValidationError):
        _make_validated_learning_unit(pack_sequence=0)


def test_validated_learning_unit_rejects_dict_for_content_type_mismatch():
    with pytest.raises(ValidationError):
        _make_validated_learning_unit(content={"not": "a draft"})


def test_validated_learning_unit_rejects_dict_for_review_type_mismatch():
    with pytest.raises(ValidationError):
        _make_validated_learning_unit(review={"not": "a review"})


def test_validated_learning_unit_path_round_trips_through_model_validate_and_json():
    unit = _make_validated_learning_unit()

    from_json = ValidatedLearningUnit.model_validate_json(unit.model_dump_json())
    from_dict = ValidatedLearningUnit.model_validate(json.loads(unit.model_dump_json()))

    assert from_json == unit
    assert from_dict == unit


def _make_canonical_lecture_document(**overrides: object) -> CanonicalLectureDocument:
    units = [_make_validated_learning_unit()]
    defaults: dict[str, object] = dict(
        lecture_id="aula-001",
        total_units=len(units),
        units=units,
        assembled_at=1700000000.0,
    )
    defaults.update(overrides)
    return CanonicalLectureDocument(**defaults)


def test_canonical_lecture_document_accepts_valid_data():
    document = _make_canonical_lecture_document()

    assert document.total_units == 1
    assert document.units[0].pack_sequence == 1


def test_canonical_lecture_document_accepts_empty_units():
    document = _make_canonical_lecture_document(total_units=0, units=[])

    assert document.units == []


def test_canonical_lecture_document_rejects_total_units_mismatch():
    with pytest.raises(ValidationError, match="total_units"):
        _make_canonical_lecture_document(total_units=2)


def test_canonical_lecture_document_rejects_path_traversal_lecture_id():
    with pytest.raises(ValidationError):
        _make_canonical_lecture_document(lecture_id="../../etc")


def test_canonical_lecture_document_path_round_trips_through_model_validate_and_json():
    document = _make_canonical_lecture_document()

    from_json = CanonicalLectureDocument.model_validate_json(document.model_dump_json())
    from_dict = CanonicalLectureDocument.model_validate(
        json.loads(document.model_dump_json())
    )

    assert from_json == document
    assert from_dict == document


def _make_semantic_fact(**overrides: object) -> SemanticFact:
    defaults: dict[str, object] = dict(
        entity_name="Aspirina",
        category="dosagem",
        clinical_context="100mg via oral a cada 8 horas.",
        timestamp_ref=12.5,
    )
    defaults.update(overrides)
    return SemanticFact(**defaults)


def _make_lecture_global_index(**overrides: object) -> LectureGlobalIndex:
    defaults: dict[str, object] = dict(
        lecture_id="aula-001",
        glossary_terms=[_make_semantic_fact()],
        dosages=[_make_semantic_fact()],
        red_flags=[],
    )
    defaults.update(overrides)
    return LectureGlobalIndex(**defaults)


def test_lecture_global_index_accepts_valid_data():
    index = _make_lecture_global_index()

    assert index.lecture_id == "aula-001"
    assert index.glossary_terms[0].entity_name == "Aspirina"
    assert index.dosages[0].entity_name == "Aspirina"
    assert index.red_flags == []


def test_lecture_global_index_accepts_empty_lists():
    index = _make_lecture_global_index(glossary_terms=[], dosages=[], red_flags=[])

    assert index.glossary_terms == []
    assert index.dosages == []
    assert index.red_flags == []


def test_lecture_global_index_rejects_path_traversal_lecture_id():
    with pytest.raises(ValidationError):
        _make_lecture_global_index(lecture_id="../../etc")


def test_lecture_global_index_rejects_dict_for_dosages_entry():
    with pytest.raises(ValidationError):
        _make_lecture_global_index(dosages=[{"entity_name": "Aspirina"}])


def test_lecture_global_index_rejects_dict_for_glossary_terms_entry():
    with pytest.raises(ValidationError):
        _make_lecture_global_index(glossary_terms=[{"entity_name": "Aspirina"}])


def test_lecture_global_index_path_round_trips_through_model_validate_and_json():
    index = _make_lecture_global_index()

    from_json = LectureGlobalIndex.model_validate_json(index.model_dump_json())
    from_dict = LectureGlobalIndex.model_validate(json.loads(index.model_dump_json()))

    assert from_json == index
    assert from_dict == index
