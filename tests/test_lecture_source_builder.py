"""Tests for exact diagnostic source construction from canonical lecture evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lecture_study.domain.models import (
    CanonicalFrame,
    DurationException,
    DurationExceptionReason,
    EvidencePack,
    LectureTranscriptionResult,
    TranscriptionSegment,
)
from lecture_study.domain.source_universe import (
    EvidenceModality,
    SourceSpanClassification,
    TimelineClassification,
)
from lecture_study.evidence.chunker import build_evidence_packs
from lecture_study.quality.content_ledger import canonical_source_universe_sha256
from lecture_study.quality.source_universe import SourceUniverseAuditor
from lecture_study.source_content.lecture_builder import (
    LectureSourceBuildError,
    build_diagnostic_lecture_source,
)


def _segment(start_time: float, end_time: float, text: str) -> TranscriptionSegment:
    return TranscriptionSegment(
        start_time=start_time,
        end_time=end_time,
        text=text,
    )


def _frame(
    root: Path,
    *,
    timestamp_seconds: float,
    sequence: int,
) -> CanonicalFrame:
    relative_path = Path("workspace") / "frames" / f"frame-{sequence:03d}.png"
    absolute_path = root / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(f"frame-bytes-{sequence}".encode())
    return CanonicalFrame(
        timestamp_sec=timestamp_seconds,
        ocr_text=f"OCR frame {sequence}",
        image_path=relative_path,
    )


def _fixture(
    root: Path,
) -> tuple[LectureTranscriptionResult, tuple[EvidencePack, ...]]:
    first_segment = _segment(0.0, 300.0, "First exact transcript segment.")
    second_segment = _segment(300.0, 600.0, "Second exact transcript segment.")
    first_frame = _frame(root, timestamp_seconds=100.0, sequence=1)
    second_frame = _frame(root, timestamp_seconds=400.0, sequence=2)
    transcription = LectureTranscriptionResult(
        lecture_id="lecture-001",
        frames=[first_frame, second_frame],
        segments=[first_segment, second_segment],
    )
    packs = (
        EvidencePack(
            lecture_id="lecture-001",
            pack_sequence=1,
            start_time=0.0,
            end_time=300.0,
            anchor_frame=first_frame,
            segments=[first_segment],
        ),
        EvidencePack(
            lecture_id="lecture-001",
            pack_sequence=2,
            start_time=300.0,
            end_time=600.0,
            anchor_frame=second_frame,
            segments=[second_segment],
        ),
    )
    return transcription, packs


def test_builder_preserves_every_segment_ocr_frame_and_visual_frame(
    tmp_path: Path,
) -> None:
    transcription, packs = _fixture(tmp_path)

    result = build_diagnostic_lecture_source(
        transcription=transcription,
        evidence_packs=packs,
        repository_root=tmp_path,
    )

    universe = result.source_universe
    assert len(universe.timeline_intervals) == 2
    assert len(universe.learning_units) == 2
    assert len(universe.evidence_spans) == 6
    assert len(result.clause_inventories) == 2
    assert len(result.text_artifacts) == 4
    assert len(result.visual_artifacts) == 2
    assert len(result.unresolved_visual_span_ids) == 2

    transcript_spans = tuple(
        span
        for span in universe.evidence_spans
        if span.modality is EvidenceModality.TRANSCRIPT
    )
    unresolved_spans = tuple(
        span
        for span in universe.evidence_spans
        if span.classification is SourceSpanClassification.AMBIGUOUS
    )
    assert len(transcript_spans) == 2
    assert len(unresolved_spans) == 4
    assert all(
        inventory.source_universe_sha256 == canonical_source_universe_sha256(universe)
        for inventory in result.clause_inventories
    )
    assert [
        clause.verbatim_text
        for inventory in result.clause_inventories
        for clause in inventory.clauses
    ] == [
        "First exact transcript segment.",
        "Second exact transcript segment.",
    ]

    report = SourceUniverseAuditor().audit(universe)
    assert report.timeline_gap_seconds == 0.0
    assert report.timeline_overlap_seconds == 0.0
    assert report.learning_unit_gap_seconds == 0.0
    assert report.learning_unit_overlap_seconds == 0.0
    assert report.missing_primary_ownership_count == 0
    assert report.unresolved_source_span_count == 4
    assert not report.passed


def test_builder_hashes_original_visual_bytes_without_copying_them(
    tmp_path: Path,
) -> None:
    transcription, packs = _fixture(tmp_path)

    result = build_diagnostic_lecture_source(
        transcription=transcription,
        evidence_packs=packs,
        repository_root=tmp_path,
    )

    first_visual = result.visual_artifacts[0]
    original_path = tmp_path / first_visual.repository_relative_path
    assert (
        first_visual.content_sha256
        == hashlib.sha256(original_path.read_bytes()).hexdigest()
    )
    assert first_visual.byte_size == original_path.stat().st_size


def test_builder_represents_natural_transcription_pause_explicitly(
    tmp_path: Path,
) -> None:
    transcription, packs = _fixture(tmp_path)
    first_segment = _segment(0.0, 299.0, "First exact transcript segment.")
    second_segment = _segment(301.0, 600.0, "Second exact transcript segment.")
    transcription = transcription.model_copy(
        update={"segments": [first_segment, second_segment]}
    )
    packs = (
        packs[0].model_copy(update={"end_time": 299.0, "segments": [first_segment]}),
        packs[1].model_copy(update={"start_time": 301.0, "segments": [second_segment]}),
    )

    result = build_diagnostic_lecture_source(
        transcription=transcription,
        evidence_packs=packs,
        repository_root=tmp_path,
    )

    report = SourceUniverseAuditor().audit(result.source_universe)
    assert report.timeline_gap_seconds == 0.0
    assert report.learning_unit_gap_seconds == 0.0
    assert [
        (span.start_time, span.end_time)
        for span in result.source_universe.evidence_spans
        if span.modality is EvidenceModality.TRANSCRIPT
    ] == [(0.0, 299.0), (301.0, 600.0)]
    assert [
        (
            interval.start_time,
            interval.end_time,
            interval.classification,
            tuple(
                span_id
                for span_id in interval.evidence_span_ids
                if str(span_id).startswith("span-transcript-")
            ),
        )
        for interval in result.source_universe.timeline_intervals
    ] == [
        (
            0.0,
            299.0,
            TimelineClassification.INSTRUCTIONAL,
            ("span-transcript-unit-001-segment-0001",),
        ),
        (299.0, 301.0, TimelineClassification.AMBIGUOUS, ()),
        (
            301.0,
            600.0,
            TimelineClassification.INSTRUCTIONAL,
            ("span-transcript-unit-002-segment-0001",),
        ),
    ]
    assert report.unresolved_timeline_interval_count == 1


def test_builder_does_not_hide_pause_between_segments_in_one_unit(
    tmp_path: Path,
) -> None:
    first_segment = _segment(0.0, 299.0, "First exact transcript segment.")
    second_segment = _segment(301.0, 600.0, "Second exact transcript segment.")
    frame = _frame(tmp_path, timestamp_seconds=400.0, sequence=1)
    transcription = LectureTranscriptionResult(
        lecture_id="lecture-001",
        frames=[frame],
        segments=[first_segment, second_segment],
    )
    pack = EvidencePack(
        lecture_id="lecture-001",
        pack_sequence=1,
        start_time=0.0,
        end_time=600.0,
        anchor_frame=frame,
        segments=[first_segment, second_segment],
    )

    result = build_diagnostic_lecture_source(
        transcription=transcription,
        evidence_packs=(pack,),
        repository_root=tmp_path,
    )

    transcript_spans = tuple(
        span
        for span in result.source_universe.evidence_spans
        if span.modality is EvidenceModality.TRANSCRIPT
    )
    assert [(span.start_time, span.end_time) for span in transcript_spans] == [
        (0.0, 299.0),
        (301.0, 600.0),
    ]
    assert [
        interval.classification
        for interval in result.source_universe.timeline_intervals
    ] == [
        TimelineClassification.INSTRUCTIONAL,
        TimelineClassification.AMBIGUOUS,
        TimelineClassification.INSTRUCTIONAL,
    ]
    assert result.source_universe.learning_units[0].start_time == 0.0
    assert result.source_universe.learning_units[0].end_time == 600.0


def test_real_shape_sixteen_pack_source_universe_remains_exact(
    tmp_path: Path,
) -> None:
    lecture_duration = 7160.78
    segment_count = 1952
    segment_duration = lecture_duration / segment_count
    segments = [
        _segment(
            index * segment_duration,
            lecture_duration
            if index == segment_count - 1
            else (index + 1) * segment_duration,
            f"Exact transcript segment {index + 1}.",
        )
        for index in range(segment_count)
    ]
    frame_timestamps = (
        [28.36, 29.48, 29.8]
        + [120.0 + index * 50.0 for index in range(43)]
        + [5204.44 + index * 42.0 for index in range(46)]
    )
    frames = [
        _frame(
            tmp_path,
            timestamp_seconds=timestamp,
            sequence=sequence,
        )
        for sequence, timestamp in enumerate(frame_timestamps, start=1)
    ]
    transcription = LectureTranscriptionResult(
        lecture_id="lecture-001",
        frames=frames,
        segments=segments,
    )
    packs = tuple(build_evidence_packs(transcription))

    result = build_diagnostic_lecture_source(
        transcription=transcription,
        evidence_packs=packs,
        repository_root=tmp_path,
    )

    report = SourceUniverseAuditor().audit(result.source_universe)
    transcript_spans = tuple(
        span
        for span in result.source_universe.evidence_spans
        if span.modality is EvidenceModality.TRANSCRIPT
    )
    assert len(packs) == 16
    assert len(transcript_spans) == segment_count
    assert all(
        (span.start_time, span.end_time) == (segment.start_time, segment.end_time)
        for span, segment in zip(transcript_spans, segments, strict=True)
    )
    assert report.timeline_gap_seconds == 0.0
    assert report.timeline_overlap_seconds == 0.0
    assert report.learning_unit_gap_seconds == 0.0
    assert report.learning_unit_overlap_seconds == 0.0
    assert report.unjustified_duration_unit_count == 0
    assert report.duration_exception_unit_count == 0


def test_builder_rejects_pack_timeline_overlap(tmp_path: Path) -> None:
    transcription, packs = _fixture(tmp_path)
    second_pack = packs[1].model_copy(update={"start_time": 299.0})

    with pytest.raises(LectureSourceBuildError, match="overlaps"):
        build_diagnostic_lecture_source(
            transcription=transcription,
            evidence_packs=(packs[0], second_pack),
            repository_root=tmp_path,
        )


def test_builder_rejects_non_positive_transcript_segment(tmp_path: Path) -> None:
    transcription, packs = _fixture(tmp_path)
    point_segment = _segment(300.0, 300.0, "Point transcript segment.")
    transcription = transcription.model_copy(
        update={
            "segments": [
                transcription.segments[0],
                point_segment,
                transcription.segments[1],
            ]
        }
    )
    first_pack = packs[0].model_copy(
        update={"segments": [packs[0].segments[0], point_segment]}
    )

    with pytest.raises(LectureSourceBuildError, match="positive duration"):
        build_diagnostic_lecture_source(
            transcription=transcription,
            evidence_packs=(first_pack, packs[1]),
            repository_root=tmp_path,
        )


def test_builder_rejects_overlapping_transcript_segments(tmp_path: Path) -> None:
    first_segment = _segment(0.0, 350.0, "First overlapping segment.")
    second_segment = _segment(300.0, 600.0, "Second overlapping segment.")
    frame = _frame(tmp_path, timestamp_seconds=400.0, sequence=1)
    transcription = LectureTranscriptionResult(
        lecture_id="lecture-001",
        frames=[frame],
        segments=[first_segment, second_segment],
    )
    pack = EvidencePack(
        lecture_id="lecture-001",
        pack_sequence=1,
        start_time=0.0,
        end_time=600.0,
        anchor_frame=frame,
        segments=[first_segment, second_segment],
    )

    with pytest.raises(LectureSourceBuildError, match="must not overlap"):
        build_diagnostic_lecture_source(
            transcription=transcription,
            evidence_packs=(pack,),
            repository_root=tmp_path,
        )


def test_builder_rejects_duplicate_or_missing_segment_allocation(
    tmp_path: Path,
) -> None:
    transcription, packs = _fixture(tmp_path)
    duplicated_segment_pack = packs[1].model_copy(
        update={"segments": [packs[0].segments[0]]}
    )

    with pytest.raises(LectureSourceBuildError, match="exactly once"):
        build_diagnostic_lecture_source(
            transcription=transcription,
            evidence_packs=(packs[0], duplicated_segment_pack),
            repository_root=tmp_path,
        )


def test_builder_carries_pack_duration_exception_into_learning_unit(
    tmp_path: Path,
) -> None:
    segment = _segment(0.0, 120.0, "Short surgical clip narration.")
    transcription = LectureTranscriptionResult(
        lecture_id="lecture-001", frames=[], segments=[segment]
    )
    packs = tuple(build_evidence_packs(transcription))

    result = build_diagnostic_lecture_source(
        transcription=transcription,
        evidence_packs=packs,
        repository_root=tmp_path,
    )

    unit = result.source_universe.learning_units[0]
    assert unit.duration_exception_reason == "lecture_shorter_than_minimum_unit"
    assert (
        SourceUniverseAuditor()
        .audit(result.source_universe)
        .unjustified_duration_unit_count
        == 0
    )


def test_builder_rejects_duration_exception_on_pack_of_multi_pack_lecture(
    tmp_path: Path,
) -> None:
    segments = [
        _segment(0.0, 400.0, "First part."),
        _segment(400.0, 800.0, "Second part."),
    ]
    transcription = LectureTranscriptionResult(
        lecture_id="lecture-001", frames=[], segments=segments
    )
    packs = tuple(build_evidence_packs(transcription))
    forged = packs[0].model_copy(
        update={
            "duration_exception": DurationException(
                reason=DurationExceptionReason.LECTURE_SHORTER_THAN_MINIMUM_UNIT
            )
        }
    )

    with pytest.raises(LectureSourceBuildError, match="duration exception"):
        build_diagnostic_lecture_source(
            transcription=transcription,
            evidence_packs=(forged, *packs[1:]),
            repository_root=tmp_path,
        )
