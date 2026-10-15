"""ASR word error rate before and after the terminology dictionary (ADR 0047).

Two conditions are measured apart. The pipeline transcript was decoded with
the domain vocabulary prompt and then rewritten by the dictionary; its raw
text is not persisted, so the uncorrected text is rebuilt from the recorded
corrections. The ablation transcript was decoded without the prompt, so the
dictionary can be applied to it directly. Reporting both keeps the effect of
the prompt apart from the effect of the histopathology rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from lecture_study.demo.wer import (
    WordErrorCounts,
    normalized_words,
    word_error_counts,
)
from lecture_study.domain.models import TranscriptionSegment
from lecture_study.transcription.terminology import (
    TerminologyCorrection,
    correct_specialist_terminology,
)

__all__ = [
    "AsrMeasurement",
    "AsrMeasurementError",
    "AsrReference",
    "PlantedTermCount",
    "measure_pipeline_transcript",
    "measure_unprompted_transcript",
    "parse_terminology_corrections",
    "uncorrected_segments",
]

AsrCondition = Literal["pipeline_prompt_on", "ablation_prompt_off"]

_SEGMENT_TEXT_SEPARATOR = " "
_CORRECTIONS_ADAPTER = TypeAdapter(
    tuple[TerminologyCorrection, ...],
    config=ConfigDict(extra="forbid"),
)


class AsrMeasurementError(RuntimeError):
    """The transcript artifacts cannot be measured as given."""


@dataclass(frozen=True, slots=True)
class AsrReference:
    """The authored text the narration was synthesized from."""

    text: str
    planted_terms: tuple[str, ...]


class PlantedTermCount(BaseModel):
    """Occurrences of one planted term in the reference and in both transcripts."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    term: str
    reference_occurrences: int = Field(ge=0)
    before_occurrences: int = Field(ge=0)
    after_occurrences: int = Field(ge=0)


class AsrMeasurement(BaseModel):
    """Word error counts of one transcript before and after the dictionary."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    condition: AsrCondition
    before: WordErrorCounts
    after: WordErrorCounts
    corrections_applied: int = Field(ge=0)
    planted_terms: tuple[PlantedTermCount, ...]


def parse_terminology_corrections(text: str) -> tuple[TerminologyCorrection, ...]:
    """Validate the corrections sidecar; failures name field locations only."""
    try:
        return _CORRECTIONS_ADAPTER.validate_json(text)
    except ValidationError as error:
        locations = ", ".join(
            ".".join(str(part) for part in problem["loc"]) for problem in error.errors()
        )
        raise AsrMeasurementError(
            f"terminology corrections are malformed at: {locations}"
        ) from None


def uncorrected_segments(
    segments: Sequence[TranscriptionSegment],
    corrections: Sequence[TerminologyCorrection],
) -> list[TranscriptionSegment]:
    """Rebuild the text the ASR produced before the dictionary rewrote it."""
    originals_by_start = _originals_by_corrected_segment(segments, corrections)
    return [
        segment.model_copy(
            update={"text": originals_by_start.get(segment.start_time, segment.text)}
        )
        for segment in segments
    ]


def measure_pipeline_transcript(
    reference: AsrReference,
    segments: Sequence[TranscriptionSegment],
    corrections: Sequence[TerminologyCorrection],
) -> AsrMeasurement:
    """Measure the stored transcript against its reconstructed uncorrected form."""
    return _measure(
        "pipeline_prompt_on",
        reference,
        before_segments=uncorrected_segments(segments, corrections),
        after_segments=segments,
        corrections_applied=len(corrections),
    )


def measure_unprompted_transcript(
    reference: AsrReference, raw_segments: Sequence[TranscriptionSegment]
) -> AsrMeasurement:
    """Measure an ASR transcript decoded without the prompt, then corrected."""
    corrected_segments, corrections = correct_specialist_terminology(raw_segments)
    return _measure(
        "ablation_prompt_off",
        reference,
        before_segments=raw_segments,
        after_segments=corrected_segments,
        corrections_applied=len(corrections),
    )


def _originals_by_corrected_segment(
    segments: Sequence[TranscriptionSegment],
    corrections: Sequence[TerminologyCorrection],
) -> dict[float, str]:
    corrected_texts = {segment.start_time: segment.text for segment in segments}
    originals: dict[float, str] = {}
    for correction in corrections:
        if corrected_texts.get(correction.start_time) != correction.corrected_text:
            raise AsrMeasurementError(
                "a terminology correction matches no segment at start time "
                f"{correction.start_time}"
            )
        originals[correction.start_time] = correction.original_text
    return originals


def _measure(
    condition: AsrCondition,
    reference: AsrReference,
    *,
    before_segments: Sequence[TranscriptionSegment],
    after_segments: Sequence[TranscriptionSegment],
    corrections_applied: int,
) -> AsrMeasurement:
    reference_words = normalized_words(reference.text)
    before_words = _hypothesis_words(before_segments)
    after_words = _hypothesis_words(after_segments)
    return AsrMeasurement(
        condition=condition,
        before=word_error_counts(reference_words, before_words),
        after=word_error_counts(reference_words, after_words),
        corrections_applied=corrections_applied,
        planted_terms=tuple(
            PlantedTermCount(
                term=term,
                reference_occurrences=_occurrences(term, reference_words),
                before_occurrences=_occurrences(term, before_words),
                after_occurrences=_occurrences(term, after_words),
            )
            for term in reference.planted_terms
        ),
    )


def _hypothesis_words(segments: Sequence[TranscriptionSegment]) -> tuple[str, ...]:
    return normalized_words(
        _SEGMENT_TEXT_SEPARATOR.join(segment.text for segment in segments)
    )


def _occurrences(term: str, words: Sequence[str]) -> int:
    term_words = normalized_words(term)
    if not term_words:
        return 0
    width = len(term_words)
    return sum(
        tuple(words[start : start + width]) == term_words
        for start in range(len(words) - width + 1)
    )
