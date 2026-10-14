"""Deterministic conceptual segmentation for the legacy EvidencePack boundary.

Transcript segments are allocated whole and exactly once to five-to-ten-minute
Learning Units. Slide timestamps influence deterministic boundary selection and
provide one representative frame per unit; they no longer create windows.

The legacy EvidencePack schema can retain only one ``anchor_frame``. The full
frame/OCR collection remains unchanged on ``LectureTranscriptionResult`` and in
the canonical source artifacts for later SourceUniverse processing. No topic
label or other semantic classification is inferred at this compatibility layer.
"""

from dataclasses import dataclass

from lecture_study.domain.models import (
    CanonicalFrame,
    DurationException,
    DurationExceptionReason,
    EvidencePack,
    LectureTranscriptionResult,
    TranscriptionSegment,
)

MINIMUM_LEARNING_UNIT_DURATION_SECONDS = 300.0
TARGET_LEARNING_UNIT_DURATION_SECONDS = 450.0
MAXIMUM_LEARNING_UNIT_DURATION_SECONDS = 600.0


class ConceptualSegmentationError(ValueError):
    """Raised when exact segment boundaries cannot satisfy the duration policy."""


class UnboundedLectureEndTimeError(ConceptualSegmentationError):
    """Raised when frames exist but no transcript can bound a Learning Unit."""


class DegenerateWindowError(ConceptualSegmentationError):
    """Raised when transcript evidence has no positive temporal extent."""


@dataclass(frozen=True)
class _ConceptualUnit:
    segments: tuple[TranscriptionSegment, ...]
    start_time: float
    end_time: float

    @classmethod
    def from_segments(
        cls,
        segments: tuple[TranscriptionSegment, ...],
    ) -> "_ConceptualUnit":
        return cls(
            segments=segments,
            start_time=segments[0].start_time,
            end_time=max(segment.end_time for segment in segments),
        )

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time


@dataclass(frozen=True)
class _CutCandidate:
    next_segment_index: int
    unit_end_time: float
    unit_duration_seconds: float


def build_evidence_packs(
    transcription_result: LectureTranscriptionResult,
) -> list[EvidencePack]:
    """Build deterministic conceptual units through the legacy pack schema."""

    frames = _ordered_frames(transcription_result.frames)
    segments = _ordered_segments(transcription_result.segments)
    if not segments:
        if frames:
            raise UnboundedLectureEndTimeError(
                "cannot build a conceptual Learning Unit without transcript segments"
            )
        return []

    units = _partition_segments(segments, frames)
    return [
        EvidencePack(
            lecture_id=transcription_result.lecture_id,
            pack_sequence=sequence,
            start_time=unit.start_time,
            end_time=unit.end_time,
            anchor_frame=_representative_frame(unit, frames),
            segments=list(unit.segments),
            duration_exception=_duration_exception_for(unit, units),
        )
        for sequence, unit in enumerate(units, start=1)
    ]


def _duration_exception_for(
    unit: _ConceptualUnit,
    units: list[_ConceptualUnit],
) -> DurationException | None:
    if (
        len(units) == 1
        and unit.duration_seconds < MINIMUM_LEARNING_UNIT_DURATION_SECONDS
    ):
        return DurationException(
            reason=DurationExceptionReason.LECTURE_SHORTER_THAN_MINIMUM_UNIT
        )
    return None


def _ordered_frames(frames: list[CanonicalFrame]) -> tuple[CanonicalFrame, ...]:
    return tuple(
        sorted(
            frames,
            key=lambda frame: (
                frame.timestamp_sec,
                str(frame.image_path),
                frame.ocr_text,
            ),
        )
    )


def _ordered_segments(
    segments: list[TranscriptionSegment],
) -> tuple[TranscriptionSegment, ...]:
    if any(segment.end_time <= segment.start_time for segment in segments):
        raise DegenerateWindowError(
            "transcript segments must have positive temporal extent"
        )
    return tuple(
        sorted(
            segments,
            key=lambda segment: (
                segment.start_time,
                segment.end_time,
                segment.text,
            ),
        )
    )


def _partition_segments(
    segments: tuple[TranscriptionSegment, ...],
    frames: tuple[CanonicalFrame, ...],
) -> list[_ConceptualUnit]:
    units: list[_ConceptualUnit] = []
    next_index = 0

    while next_index < len(segments):
        remainder = _ConceptualUnit.from_segments(segments[next_index:])
        if remainder.duration_seconds <= MAXIMUM_LEARNING_UNIT_DURATION_SECONDS:
            _append_final_remainder(units, remainder)
            break

        candidate = _select_cut_candidate(segments, next_index, frames)
        units.append(
            _ConceptualUnit.from_segments(
                segments[next_index : candidate.next_segment_index]
            )
        )
        next_index = candidate.next_segment_index

    return units


def _append_final_remainder(
    units: list[_ConceptualUnit],
    remainder: _ConceptualUnit,
) -> None:
    if (
        remainder.duration_seconds >= MINIMUM_LEARNING_UNIT_DURATION_SECONDS
        or not units
    ):
        units.append(remainder)
        return

    merged = _ConceptualUnit.from_segments(units[-1].segments + remainder.segments)
    if merged.duration_seconds <= MAXIMUM_LEARNING_UNIT_DURATION_SECONDS:
        units[-1] = merged
        return

    raise ConceptualSegmentationError(
        "the final transcript remainder is shorter than 300 seconds and cannot "
        "be merged without exceeding 600 seconds"
    )


def _select_cut_candidate(
    segments: tuple[TranscriptionSegment, ...],
    start_index: int,
    frames: tuple[CanonicalFrame, ...],
) -> _CutCandidate:
    candidates = _valid_cut_candidates(segments, start_index)
    if not candidates:
        raise ConceptualSegmentationError(
            "no exact transcript-segment boundary can keep the Learning Unit "
            "between 300 and 600 seconds"
        )

    candidates_without_short_final = [
        candidate
        for candidate in candidates
        if _remainder_duration(segments, candidate.next_segment_index)
        >= MINIMUM_LEARNING_UNIT_DURATION_SECONDS
    ]
    eligible = candidates_without_short_final or candidates
    return min(eligible, key=lambda candidate: _candidate_score(candidate, frames))


def _valid_cut_candidates(
    segments: tuple[TranscriptionSegment, ...],
    start_index: int,
) -> list[_CutCandidate]:
    start_time = segments[start_index].start_time
    maximum_end_time = start_time
    candidates: list[_CutCandidate] = []

    for next_index in range(start_index + 1, len(segments)):
        maximum_end_time = max(
            maximum_end_time,
            segments[next_index - 1].end_time,
        )
        duration = maximum_end_time - start_time
        if duration > MAXIMUM_LEARNING_UNIT_DURATION_SECONDS:
            break
        if duration >= MINIMUM_LEARNING_UNIT_DURATION_SECONDS:
            candidates.append(
                _CutCandidate(
                    next_segment_index=next_index,
                    unit_end_time=maximum_end_time,
                    unit_duration_seconds=duration,
                )
            )

    return candidates


def _remainder_duration(
    segments: tuple[TranscriptionSegment, ...],
    start_index: int,
) -> float:
    return (
        max(segment.end_time for segment in segments[start_index:])
        - segments[start_index].start_time
    )


def _candidate_score(
    candidate: _CutCandidate,
    frames: tuple[CanonicalFrame, ...],
) -> tuple[float, float, float, int]:
    target_distance = abs(
        candidate.unit_duration_seconds - TARGET_LEARNING_UNIT_DURATION_SECONDS
    )
    slide_distance = min(
        (abs(frame.timestamp_sec - candidate.unit_end_time) for frame in frames),
        default=float("inf"),
    )
    return (
        target_distance,
        slide_distance,
        candidate.unit_end_time,
        candidate.next_segment_index,
    )


def _representative_frame(
    unit: _ConceptualUnit,
    frames: tuple[CanonicalFrame, ...],
) -> CanonicalFrame | None:
    candidates = [
        frame
        for frame in frames
        if unit.start_time <= frame.timestamp_sec < unit.end_time
    ]
    if not candidates:
        return None

    midpoint = unit.start_time + unit.duration_seconds / 2
    return min(
        candidates,
        key=lambda frame: (
            abs(frame.timestamp_sec - midpoint),
            frame.timestamp_sec,
            str(frame.image_path),
            frame.ocr_text,
        ),
    )
