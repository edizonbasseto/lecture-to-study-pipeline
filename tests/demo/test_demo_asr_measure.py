"""ASR before/after the dictionary: reconstruction, both conditions and the
first report slice."""

import json
from datetime import date

import pytest

from lecture_study.demo.asr_measure import (
    AsrMeasurementError,
    AsrReference,
    measure_pipeline_transcript,
    measure_unprompted_transcript,
    parse_terminology_corrections,
    uncorrected_segments,
)
from lecture_study.demo.report import (
    AdapterResults,
    DemoResults,
    render_results_json,
    render_results_markdown,
)
from lecture_study.domain.models import TranscriptionSegment
from lecture_study.llm.gateway import AdapterName

PIPELINE_REFERENCE = AsrReference(
    text="A margem fascial profunda mostra o exame histopatológico completo.",
    planted_terms=("histopatológico", "margem fascial"),
)
FASCIA_CORRECTION = {
    "start_time": 0.0,
    "original_text": "A margem facial profunda mostra",
    "corrected_text": "A margem fascial profunda mostra",
    "term": "fascial",
}


def segment(start_time: float, text: str) -> TranscriptionSegment:
    return TranscriptionSegment(
        start_time=start_time, end_time=start_time + 1.0, text=text
    )


def stored_segments() -> list[TranscriptionSegment]:
    return [
        segment(0.0, "A margem fascial profunda mostra"),
        segment(2.0, "o exame histopatológico"),
        segment(4.0, "completo."),
    ]


def corrections_json(*entries: dict[str, object]) -> str:
    return json.dumps(list(entries))


def test_uncorrected_segments_restore_the_original_text() -> None:
    corrections = parse_terminology_corrections(corrections_json(FASCIA_CORRECTION))

    restored = uncorrected_segments(stored_segments(), corrections)

    assert [item.text for item in restored] == [
        "A margem facial profunda mostra",
        "o exame histopatológico",
        "completo.",
    ]


def test_pipeline_measurement_lowers_the_rate_with_the_dictionary() -> None:
    corrections = parse_terminology_corrections(corrections_json(FASCIA_CORRECTION))

    measurement = measure_pipeline_transcript(
        PIPELINE_REFERENCE, stored_segments(), corrections
    )

    assert measurement.condition == "pipeline_prompt_on"
    assert measurement.before.reference_words == 9
    assert measurement.before.substitutions == 1
    assert measurement.after.errors() == 0
    assert measurement.before.rate() > measurement.after.rate()
    assert measurement.corrections_applied == 1


def test_planted_term_counts_move_from_before_to_after() -> None:
    corrections = parse_terminology_corrections(corrections_json(FASCIA_CORRECTION))

    measurement = measure_pipeline_transcript(
        PIPELINE_REFERENCE, stored_segments(), corrections
    )

    counts = {item.term: item for item in measurement.planted_terms}
    assert counts["margem fascial"].reference_occurrences == 1
    assert counts["margem fascial"].before_occurrences == 0
    assert counts["margem fascial"].after_occurrences == 1
    assert counts["histopatológico"].before_occurrences == 1
    assert counts["histopatológico"].after_occurrences == 1


def test_correction_matching_no_segment_fails_the_measurement() -> None:
    unmatched = {**FASCIA_CORRECTION, "start_time": 99.0}
    corrections = parse_terminology_corrections(corrections_json(unmatched))

    with pytest.raises(AsrMeasurementError, match="99.0"):
        uncorrected_segments(stored_segments(), corrections)


def test_correction_with_a_different_stored_text_fails_the_measurement() -> None:
    drifted = {**FASCIA_CORRECTION, "corrected_text": "texto que nao existe"}
    corrections = parse_terminology_corrections(corrections_json(drifted))

    with pytest.raises(AsrMeasurementError):
        measure_pipeline_transcript(PIPELINE_REFERENCE, stored_segments(), corrections)


def test_malformed_corrections_name_the_field_only() -> None:
    incomplete = {
        key: value for key, value in FASCIA_CORRECTION.items() if key != "term"
    }

    with pytest.raises(AsrMeasurementError, match="term") as raised:
        parse_terminology_corrections(corrections_json(incomplete))

    assert "mostra" not in str(raised.value)


def test_corrections_with_an_unknown_field_are_refused() -> None:
    with pytest.raises(AsrMeasurementError):
        parse_terminology_corrections(
            corrections_json({**FASCIA_CORRECTION, "extra": "x"})
        )


def test_unprompted_transcript_is_corrected_by_the_dictionary() -> None:
    reference = AsrReference(
        text="A camada fascial na profundidade mostra o exame histopatológico completo.",
        planted_terms=("histopatológico", "camada fascial"),
    )
    raw = [
        segment(
            0.0,
            "A camada facial na profundidade mostra o exame estopatológico completo.",
        )
    ]

    measurement = measure_unprompted_transcript(reference, raw)

    assert measurement.condition == "ablation_prompt_off"
    assert measurement.before.substitutions == 2
    assert measurement.after.errors() == 0
    assert measurement.corrections_applied == 2
    assert measurement.before.rate() > measurement.after.rate()
    counts = {item.term: item for item in measurement.planted_terms}
    assert counts["histopatológico"].before_occurrences == 0
    assert counts["histopatológico"].after_occurrences == 1


def test_the_tracer_renders_the_asr_section_and_round_trips_through_json() -> None:
    corrections = parse_terminology_corrections(corrections_json(FASCIA_CORRECTION))
    measurement = measure_pipeline_transcript(
        PIPELINE_REFERENCE, stored_segments(), corrections
    )
    results = DemoResults(
        measured_on=date(2026, 10, 15),
        video_seconds=240.0,
        reference_words=9,
        adapters=(
            AdapterResults(
                adapter=AdapterName.LLAMA_CPP,
                lecture_id="demo-local",
                asr=(measurement,),
                phases=(),
                llm_stages=(),
                critic=None,
                evidence_packs=1,
                notes=(),
            ),
        ),
        adapters_not_run=(),
    )

    markdown = render_results_markdown(results)

    assert markdown.startswith("# Demo lecture results")
    assert "0047-demo-lecture-measurement-method.md" in markdown
    assert "## ASR word error rate" in markdown
    assert "11.1% (1 of 9 words)" in markdown
    assert "0.0% (0 of 9 words)" in markdown
    assert DemoResults.model_validate_json(render_results_json(results)) == results
