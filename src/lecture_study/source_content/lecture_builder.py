"""Build non-authorizing lecture source contracts from canonical local evidence."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from lecture_study.domain.models import (
    CanonicalFrame,
    EvidencePack,
    LectureTranscriptionResult,
    TranscriptionSegment,
)
from lecture_study.domain.semantic_observation import (
    SourceClause,
    SourceClauseInventory,
    SourcePartitionKind,
    SourceTextExtent,
    TextPartitionSelector,
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
from lecture_study.evidence.chunker import MINIMUM_LEARNING_UNIT_DURATION_SECONDS
from lecture_study.quality.content_ledger import canonical_source_universe_sha256

_TIMELINE_TOLERANCE_SECONDS = 0.001


class LectureSourceBuildError(RuntimeError):
    """Raised when canonical lecture evidence cannot form an exact source graph."""


@dataclass(frozen=True, slots=True)
class DiagnosticTextArtifact:
    """Canonical text bytes materialized by the diagnostic package writer."""

    artifact_id: str
    record_id: str
    span_id: str
    relative_path: str
    modality: EvidenceModality
    content: bytes


@dataclass(frozen=True, slots=True)
class DiagnosticVisualArtifact:
    """Identity of one original encoded frame retained in the repository."""

    artifact_id: str
    record_id: str
    span_id: str
    repository_relative_path: str
    content_sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class DiagnosticLectureSource:
    """Complete structural inputs that deliberately carry no production authority."""

    source_universe: SourceUniverse
    clause_inventories: tuple[SourceClauseInventory, ...]
    text_artifacts: tuple[DiagnosticTextArtifact, ...]
    visual_artifacts: tuple[DiagnosticVisualArtifact, ...]
    unresolved_visual_span_ids: tuple[str, ...]


def build_diagnostic_lecture_source(
    *,
    transcription: LectureTranscriptionResult,
    evidence_packs: tuple[EvidencePack, ...],
    repository_root: Path,
) -> DiagnosticLectureSource:
    """Build exact textual partitions while preserving unresolved visual evidence."""

    packs = _validated_packs(transcription, evidence_packs)
    root = repository_root.resolve(strict=True)
    evidence_spans: list[SourceEvidenceSpan] = []
    timeline_intervals: list[TimelineInterval] = []
    learning_units: list[ConceptualLearningUnit] = []
    inventories: list[SourceClauseInventory] = []
    text_artifacts: list[DiagnosticTextArtifact] = []
    visual_artifacts: list[DiagnosticVisualArtifact] = []
    unresolved_visual_span_ids: list[str] = []
    unit_time_ranges = _unit_time_ranges(transcription, packs)
    unit_frame_groups = _assign_frames_to_units(transcription, unit_time_ranges)

    for unit_sequence, (pack, time_range, frames) in enumerate(
        zip(packs, unit_time_ranges, unit_frame_groups, strict=True),
        start=1,
    ):
        unit_id = f"unit-{unit_sequence:03d}"
        unit_start_time, unit_end_time = time_range
        transcript_artifacts, transcript_spans, inventory = _build_transcript_unit(
            lecture_id=str(transcription.lecture_id),
            unit_id=unit_id,
            unit_sequence=unit_sequence,
            pack=pack,
        )
        evidence_spans.extend(transcript_spans)
        inventories.append(inventory)
        text_artifacts.extend(transcript_artifacts)

        contextual_span_ids: list[str] = []
        for frame_sequence, frame in frames:
            ocr_artifact, ocr_span = _build_ocr_frame(
                lecture_id=str(transcription.lecture_id),
                unit_id=unit_id,
                frame_sequence=frame_sequence,
                timestamp_seconds=frame.timestamp_sec,
                ocr_text=frame.ocr_text,
            )
            visual_artifact, visual_span = _build_visual_frame(
                lecture_id=str(transcription.lecture_id),
                frame_sequence=frame_sequence,
                timestamp_seconds=frame.timestamp_sec,
                image_path=frame.image_path,
                repository_root=root,
            )
            text_artifacts.append(ocr_artifact)
            visual_artifacts.append(visual_artifact)
            evidence_spans.extend((ocr_span, visual_span))
            contextual_span_ids.extend(
                (str(ocr_span.span_id), str(visual_span.span_id))
            )
            unresolved_visual_span_ids.append(str(visual_span.span_id))

        learning_units.append(
            ConceptualLearningUnit(
                schema_version=1,
                unit_id=unit_id,
                lecture_id=transcription.lecture_id,
                sequence=unit_sequence,
                start_time=unit_start_time,
                end_time=unit_end_time,
                primary_source_span_ids=tuple(
                    str(transcript_span.span_id) for transcript_span in transcript_spans
                ),
                context_source_span_ids=tuple(contextual_span_ids),
                duration_exception_reason=_duration_exception_reason(pack),
            )
        )

    media_duration_seconds = max(segment.end_time for segment in transcription.segments)
    timeline_intervals.extend(
        _build_timeline_intervals(
            lecture_id=str(transcription.lecture_id),
            media_duration_seconds=media_duration_seconds,
            evidence_spans=tuple(evidence_spans),
        )
    )
    source_universe = SourceUniverse(
        schema_version=1,
        lecture_id=transcription.lecture_id,
        media_duration_seconds=media_duration_seconds,
        timeline_intervals=tuple(timeline_intervals),
        evidence_spans=tuple(evidence_spans),
        learning_units=tuple(learning_units),
    )
    source_universe_sha256 = canonical_source_universe_sha256(source_universe)
    bound_inventories = tuple(
        SourceClauseInventory.model_validate(
            inventory.model_copy(
                update={"source_universe_sha256": source_universe_sha256}
            ).model_dump(mode="python", warnings=False)
        )
        for inventory in inventories
    )
    return DiagnosticLectureSource(
        source_universe=source_universe,
        clause_inventories=bound_inventories,
        text_artifacts=tuple(text_artifacts),
        visual_artifacts=tuple(visual_artifacts),
        unresolved_visual_span_ids=tuple(unresolved_visual_span_ids),
    )


def _validated_packs(
    transcription: LectureTranscriptionResult,
    evidence_packs: tuple[EvidencePack, ...],
) -> tuple[EvidencePack, ...]:
    if not evidence_packs:
        raise LectureSourceBuildError("at least one evidence pack is required")
    packs = tuple(sorted(evidence_packs, key=lambda pack: pack.pack_sequence))
    expected_sequences = tuple(range(1, len(packs) + 1))
    if tuple(pack.pack_sequence for pack in packs) != expected_sequences:
        raise LectureSourceBuildError("evidence pack sequences must be contiguous")
    if any(pack.lecture_id != transcription.lecture_id for pack in packs):
        raise LectureSourceBuildError(
            "every evidence pack must match the transcription lecture"
        )
    if any(not pack.segments for pack in packs):
        raise LectureSourceBuildError(
            "every diagnostic learning unit requires transcript evidence"
        )
    if any(
        segment.end_time <= segment.start_time for segment in transcription.segments
    ):
        raise LectureSourceBuildError(
            "every transcript evidence segment must have positive duration"
        )
    _require_ordered_timeline(packs)
    _require_exact_segment_allocation(transcription, packs)
    _require_pack_bounds(packs)
    _require_duration_exception_scope(packs)
    return packs


def _require_ordered_timeline(packs: tuple[EvidencePack, ...]) -> None:
    for previous, current in zip(packs, packs[1:]):
        if previous.end_time - current.start_time > _TIMELINE_TOLERANCE_SECONDS:
            raise LectureSourceBuildError(
                "evidence pack timeline must not contain overlaps"
            )


def _require_pack_bounds(packs: tuple[EvidencePack, ...]) -> None:
    for pack in packs:
        expected_start = min(segment.start_time for segment in pack.segments)
        expected_end = max(segment.end_time for segment in pack.segments)
        if (
            abs(pack.start_time - expected_start) > _TIMELINE_TOLERANCE_SECONDS
            or abs(pack.end_time - expected_end) > _TIMELINE_TOLERANCE_SECONDS
        ):
            raise LectureSourceBuildError(
                f"evidence pack {pack.pack_sequence} bounds must match its segments"
            )


def _unit_time_ranges(
    transcription: LectureTranscriptionResult,
    packs: tuple[EvidencePack, ...],
) -> tuple[tuple[float, float], ...]:
    boundaries = [0.0]
    boundaries.extend(
        (previous.end_time + current.start_time) / 2
        for previous, current in zip(packs, packs[1:])
    )
    boundaries.append(max(segment.end_time for segment in transcription.segments))
    return tuple(zip(boundaries, boundaries[1:]))


def _require_exact_segment_allocation(
    transcription: LectureTranscriptionResult,
    packs: tuple[EvidencePack, ...],
) -> None:
    expected = Counter(_segment_identity(segment) for segment in transcription.segments)
    observed = Counter(
        _segment_identity(segment) for pack in packs for segment in pack.segments
    )
    if observed != expected:
        raise LectureSourceBuildError(
            "evidence packs must allocate every transcription segment exactly once"
        )
    expected_end = max(segment.end_time for segment in transcription.segments)
    if abs(packs[-1].end_time - expected_end) > _TIMELINE_TOLERANCE_SECONDS:
        raise LectureSourceBuildError(
            "evidence pack timeline must end at the transcription duration"
        )


def _require_duration_exception_scope(packs: tuple[EvidencePack, ...]) -> None:
    marked = [pack for pack in packs if pack.duration_exception is not None]
    if not marked:
        return
    is_sole_short_pack = (
        len(packs) == 1
        and packs[0].end_time - packs[0].start_time
        < MINIMUM_LEARNING_UNIT_DURATION_SECONDS
    )
    if not is_sole_short_pack:
        raise LectureSourceBuildError(
            "a duration exception is only valid on the sole pack of a lecture "
            "shorter than the minimum learning-unit duration"
        )


def _duration_exception_reason(pack: EvidencePack) -> str | None:
    if pack.duration_exception is None:
        return None
    return pack.duration_exception.reason.value


def _segment_identity(segment: TranscriptionSegment) -> tuple[float, float, str]:
    return segment.start_time, segment.end_time, segment.text


def _assign_frames_to_units(
    transcription: LectureTranscriptionResult,
    unit_time_ranges: tuple[tuple[float, float], ...],
) -> tuple[tuple[tuple[int, CanonicalFrame], ...], ...]:
    grouped: list[list[tuple[int, CanonicalFrame]]] = [[] for _ in unit_time_ranges]
    for frame_sequence, frame in enumerate(transcription.frames, start=1):
        matching_indexes = [
            index
            for index, (start_time, end_time) in enumerate(unit_time_ranges)
            if start_time <= frame.timestamp_sec < end_time
        ]
        if len(matching_indexes) != 1:
            raise LectureSourceBuildError(
                f"frame {frame_sequence} does not belong to exactly one learning unit"
            )
        grouped[matching_indexes[0]].append((frame_sequence, frame))
    return tuple(tuple(group) for group in grouped)


def _build_transcript_unit(
    *,
    lecture_id: str,
    unit_id: str,
    unit_sequence: int,
    pack: EvidencePack,
) -> tuple[
    tuple[DiagnosticTextArtifact, ...],
    tuple[SourceEvidenceSpan, ...],
    SourceClauseInventory,
]:
    artifacts: list[DiagnosticTextArtifact] = []
    spans: list[SourceEvidenceSpan] = []
    text_extents: list[SourceTextExtent] = []
    clauses: list[SourceClause] = []
    for segment_sequence, segment in enumerate(pack.segments, start=1):
        canonical_text = segment.text
        canonical_bytes = canonical_text.encode("utf-8")
        content_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
        artifact_id = f"transcript-{unit_id}-segment-{segment_sequence:04d}"
        record_id = f"record-{artifact_id}"
        span_id = f"span-{artifact_id}"
        artifacts.append(
            DiagnosticTextArtifact(
                artifact_id=artifact_id,
                record_id=record_id,
                span_id=span_id,
                relative_path=(
                    f"source/transcript/{unit_id}/segment-{segment_sequence:04d}.txt"
                ),
                modality=EvidenceModality.TRANSCRIPT,
                content=canonical_bytes,
            )
        )
        spans.append(
            SourceEvidenceSpan(
                schema_version=1,
                span_id=span_id,
                lecture_id=lecture_id,
                source_artifact_id=artifact_id,
                source_record_id=record_id,
                modality=EvidenceModality.TRANSCRIPT,
                start_time=segment.start_time,
                end_time=segment.end_time,
                classification=SourceSpanClassification.INSTRUCTIONAL,
                content_sha256=content_sha256,
            )
        )
        text_extents.append(
            SourceTextExtent(
                source_span_id=span_id,
                source_content_sha256=content_sha256,
                modality=EvidenceModality.TRANSCRIPT,
                canonical_text=canonical_text,
                byte_length=len(canonical_bytes),
            )
        )
        clauses.append(
            SourceClause(
                schema_version=1,
                clause_id=(
                    f"clause-transcript-{unit_sequence:03d}-{segment_sequence:04d}"
                ),
                lecture_id=lecture_id,
                learning_unit_id=unit_id,
                sequence=segment_sequence,
                source_span_id=span_id,
                source_content_sha256=content_sha256,
                modality=EvidenceModality.TRANSCRIPT,
                selector=TextPartitionSelector(
                    kind=SourcePartitionKind.TEXT_RANGE,
                    start_byte=0,
                    end_byte=len(canonical_bytes),
                    selected_sha256=content_sha256,
                ),
                verbatim_text=canonical_text,
                text_sha256=content_sha256,
            )
        )
    inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id=lecture_id,
        learning_unit_id=unit_id,
        source_universe_sha256="0" * 64,
        text_extents=tuple(text_extents),
        clauses=tuple(clauses),
    )
    return tuple(artifacts), tuple(spans), inventory


def _build_timeline_intervals(
    *,
    lecture_id: str,
    media_duration_seconds: float,
    evidence_spans: tuple[SourceEvidenceSpan, ...],
) -> tuple[TimelineInterval, ...]:
    transcript_spans = sorted(
        (
            span
            for span in evidence_spans
            if span.modality is EvidenceModality.TRANSCRIPT
        ),
        key=lambda span: (span.start_time, span.end_time, str(span.span_id)),
    )
    interval_specs: list[tuple[float, float, TimelineClassification, list[str]]] = []
    cursor = 0.0
    for span in transcript_spans:
        if span.start_time < cursor:
            raise LectureSourceBuildError(
                "transcript segments must not overlap on the source timeline"
            )
        if span.start_time > cursor:
            interval_specs.append(
                (
                    cursor,
                    span.start_time,
                    TimelineClassification.AMBIGUOUS,
                    [],
                )
            )
        interval_specs.append(
            (
                span.start_time,
                span.end_time,
                TimelineClassification.INSTRUCTIONAL,
                [str(span.span_id)],
            )
        )
        cursor = span.end_time
    if cursor < media_duration_seconds:
        interval_specs.append(
            (
                cursor,
                media_duration_seconds,
                TimelineClassification.AMBIGUOUS,
                [],
            )
        )

    for span in evidence_spans:
        if span.modality is EvidenceModality.TRANSCRIPT:
            continue
        matching_indexes = _matching_timeline_interval_indexes(span, interval_specs)
        if not matching_indexes:
            raise LectureSourceBuildError(
                f"source span {span.span_id} does not fit the media timeline"
            )
        interval_specs[matching_indexes[0]][3].append(str(span.span_id))

    return tuple(
        TimelineInterval(
            schema_version=1,
            interval_id=f"interval-{sequence:04d}",
            lecture_id=lecture_id,
            sequence=sequence,
            start_time=start_time,
            end_time=end_time,
            classification=classification,
            evidence_span_ids=tuple(span_ids),
        )
        for sequence, (start_time, end_time, classification, span_ids) in enumerate(
            interval_specs,
            start=1,
        )
    )


def _matching_timeline_interval_indexes(
    span: SourceEvidenceSpan,
    interval_specs: list[tuple[float, float, TimelineClassification, list[str]]],
) -> list[int]:
    starts_at_span = [
        index
        for index, (start_time, end_time, _, _) in enumerate(interval_specs)
        if start_time == span.start_time and span.end_time <= end_time
    ]
    if starts_at_span:
        return starts_at_span
    return [
        index
        for index, (start_time, end_time, _, _) in enumerate(interval_specs)
        if start_time <= span.start_time and span.end_time <= end_time
    ]


def _build_ocr_frame(
    *,
    lecture_id: str,
    unit_id: str,
    frame_sequence: int,
    timestamp_seconds: float,
    ocr_text: str,
) -> tuple[DiagnosticTextArtifact, SourceEvidenceSpan]:
    content = ocr_text.encode("utf-8")
    content_sha256 = hashlib.sha256(content).hexdigest()
    artifact_id = f"ocr-frame-{frame_sequence:04d}"
    record_id = f"record-{artifact_id}"
    span_id = f"span-{artifact_id}"
    return (
        DiagnosticTextArtifact(
            artifact_id=artifact_id,
            record_id=record_id,
            span_id=span_id,
            relative_path=f"source/ocr/{unit_id}/frame-{frame_sequence:04d}.txt",
            modality=EvidenceModality.OCR,
            content=content,
        ),
        SourceEvidenceSpan(
            schema_version=1,
            span_id=span_id,
            lecture_id=lecture_id,
            source_artifact_id=artifact_id,
            source_record_id=record_id,
            modality=EvidenceModality.OCR,
            start_time=timestamp_seconds,
            end_time=timestamp_seconds,
            classification=SourceSpanClassification.AMBIGUOUS,
            content_sha256=content_sha256,
        ),
    )


def _build_visual_frame(
    *,
    lecture_id: str,
    frame_sequence: int,
    timestamp_seconds: float,
    image_path: Path,
    repository_root: Path,
) -> tuple[DiagnosticVisualArtifact, SourceEvidenceSpan]:
    absolute_path = (repository_root / image_path).resolve(strict=True)
    try:
        relative_path = absolute_path.relative_to(repository_root).as_posix()
    except ValueError as error:
        raise LectureSourceBuildError(
            f"frame {frame_sequence} escapes the repository root"
        ) from error
    content = absolute_path.read_bytes()
    content_sha256 = hashlib.sha256(content).hexdigest()
    artifact_id = f"visual-frame-{frame_sequence:04d}"
    record_id = f"record-{artifact_id}"
    span_id = f"span-{artifact_id}"
    return (
        DiagnosticVisualArtifact(
            artifact_id=artifact_id,
            record_id=record_id,
            span_id=span_id,
            repository_relative_path=relative_path,
            content_sha256=content_sha256,
            byte_size=len(content),
        ),
        SourceEvidenceSpan(
            schema_version=1,
            span_id=span_id,
            lecture_id=lecture_id,
            source_artifact_id=artifact_id,
            source_record_id=record_id,
            modality=EvidenceModality.VISUAL,
            start_time=timestamp_seconds,
            end_time=timestamp_seconds,
            classification=SourceSpanClassification.AMBIGUOUS,
            content_sha256=content_sha256,
        ),
    )
