"""Conceptual Evidence Pack segmentation and Phase 4 orchestration tests."""

import json
from pathlib import Path

import pytest

from lecture_study.domain.models import (
    CanonicalFrame,
    DurationException,
    DurationExceptionReason,
    EvidencePack,
    LectureMetadata,
    LectureTranscriptionResult,
    TranscriptionSegment,
)
from lecture_study.evidence import pipeline as pipeline_module
from lecture_study.evidence.chunker import (
    ConceptualSegmentationError,
    UnboundedLectureEndTimeError,
    build_evidence_packs,
)
from lecture_study.evidence.pipeline import (
    EvidencePackOrchestrator,
    TranscriptionHandoffNotFoundError,
)


def _make_frame(timestamp_sec: float) -> CanonicalFrame:
    return CanonicalFrame(
        timestamp_sec=timestamp_sec,
        ocr_text=f"Slide at {timestamp_sec}",
        image_path=Path(f"workspace/data/04_canonical/frame-{timestamp_sec}.png"),
    )


def _make_segment(start_time: float, end_time: float) -> TranscriptionSegment:
    return TranscriptionSegment(
        start_time=start_time, end_time=end_time, text=f"Fala {start_time}-{end_time}."
    )


def _make_transcription_result(
    frames: list[CanonicalFrame], segments: list[TranscriptionSegment]
) -> LectureTranscriptionResult:
    return LectureTranscriptionResult(
        lecture_id="aula-001", frames=frames, segments=segments
    )


def test_build_evidence_packs_returns_empty_list_for_frameless_segmentless_lecture():
    result = _make_transcription_result(frames=[], segments=[])

    assert build_evidence_packs(result) == []


def test_build_evidence_packs_requires_transcript_when_frames_exist():
    result = _make_transcription_result(frames=[_make_frame(5.0)], segments=[])

    with pytest.raises(UnboundedLectureEndTimeError):
        build_evidence_packs(result)


def test_build_evidence_packs_marks_single_short_lecture_pack_as_duration_exception():
    result = _make_transcription_result(
        frames=[],
        segments=[_make_segment(0.0, 120.0), _make_segment(120.0, 299.0)],
    )

    packs = build_evidence_packs(result)

    assert len(packs) == 1
    assert packs[0].duration_exception == DurationException(
        reason=DurationExceptionReason.LECTURE_SHORTER_THAN_MINIMUM_UNIT
    )


def test_build_evidence_packs_leaves_normal_lecture_unmarked():
    result = _make_transcription_result(frames=[], segments=[_make_segment(0.0, 300.0)])

    assert build_evidence_packs(result)[0].duration_exception is None


def test_build_evidence_packs_still_rejects_unmergeable_short_remainder():
    result = _make_transcription_result(
        frames=[],
        segments=[_make_segment(0.0, 590.0), _make_segment(590.0, 600.0 + 50.0)],
    )

    with pytest.raises(ConceptualSegmentationError, match="shorter than 300"):
        build_evidence_packs(result)


def test_evidence_pack_without_duration_exception_field_still_loads():
    payload = {
        "lecture_id": "aula-001",
        "pack_sequence": 1,
        "start_time": 0.0,
        "end_time": 10.0,
        "anchor_frame": None,
        "segments": [],
    }

    pack = EvidencePack.model_validate_json(json.dumps(payload))

    assert pack.duration_exception is None


def test_build_evidence_packs_selects_representative_midpoint_frame():
    earlier_frame = _make_frame(140.0)
    later_frame = _make_frame(160.0)
    boundary_frame = _make_frame(300.0)
    segment = _make_segment(0.0, 300.0)
    result = _make_transcription_result(
        frames=[later_frame, boundary_frame, earlier_frame],
        segments=[segment],
    )

    packs = build_evidence_packs(result)

    assert len(packs) == 1
    assert packs[0].pack_sequence == 1
    assert (packs[0].start_time, packs[0].end_time) == (0.0, 300.0)
    assert packs[0].anchor_frame == earlier_frame
    assert packs[0].segments == [segment]


def test_build_evidence_packs_frameless_unit_has_no_anchor():
    result = _make_transcription_result(
        frames=[],
        segments=[_make_segment(0.0, 300.0)],
    )

    packs = build_evidence_packs(result)

    assert len(packs) == 1
    assert packs[0].anchor_frame is None


def test_build_evidence_packs_frame_clusters_do_not_create_empty_units():
    segment = _make_segment(0.0, 300.0)
    frames = [_make_frame(timestamp) for timestamp in (10.0, 10.1, 10.2, 250.0)]
    result = _make_transcription_result(frames=frames, segments=[segment])

    packs = build_evidence_packs(result)

    assert len(packs) == 1
    assert packs[0].segments == [segment]


def test_build_evidence_packs_uses_slide_proximity_to_break_target_tie():
    segments = [
        _make_segment(float(start), float(start + 100)) for start in range(0, 800, 100)
    ]
    result = _make_transcription_result(
        frames=[_make_frame(498.0)],
        segments=segments,
    )

    packs = build_evidence_packs(result)

    assert [(pack.start_time, pack.end_time) for pack in packs] == [
        (0.0, 500.0),
        (500.0, 800.0),
    ]


def test_build_evidence_packs_prefers_full_final_unit_over_short_remainder():
    segments = [
        _make_segment(float(start), float(start + 100)) for start in range(0, 700, 100)
    ]
    result = _make_transcription_result(frames=[], segments=segments)

    packs = build_evidence_packs(result)

    assert [(pack.start_time, pack.end_time) for pack in packs] == [
        (0.0, 400.0),
        (400.0, 700.0),
    ]


def test_build_evidence_packs_rejects_zero_duration_transcript_segment():
    segment_a = _make_segment(0.0, 300.0)
    point_segment = _make_segment(300.0, 300.0)
    segment_b = _make_segment(300.0, 650.0)
    segments = [segment_a, point_segment, segment_b]
    result = _make_transcription_result(frames=[], segments=segments)

    with pytest.raises(
        ConceptualSegmentationError,
        match="positive temporal extent",
    ):
        build_evidence_packs(result)


def test_build_evidence_packs_rejects_segment_longer_than_maximum_unit():
    result = _make_transcription_result(
        frames=[],
        segments=[_make_segment(0.0, 601.0)],
    )

    with pytest.raises(ConceptualSegmentationError, match="exact transcript"):
        build_evidence_packs(result)


def test_build_evidence_packs_real_shape_uses_complete_conceptual_units():
    lecture_duration = 7160.78
    segment_count = 1952
    segment_duration = lecture_duration / segment_count
    segments = [
        _make_segment(
            index * segment_duration,
            lecture_duration
            if index == segment_count - 1
            else (index + 1) * segment_duration,
        )
        for index in range(segment_count)
    ]
    clustered_frames = [28.36, 29.48, 29.8]
    early_frames = [120.0 + index * 50.0 for index in range(43)]
    late_frames = [5204.44 + index * 42.0 for index in range(46)]
    frames = [
        _make_frame(timestamp)
        for timestamp in clustered_frames + early_frames + late_frames
    ]
    result = _make_transcription_result(frames=frames, segments=segments)

    packs = build_evidence_packs(result)

    allocated_segments = [segment for pack in packs for segment in pack.segments]
    assert allocated_segments == segments
    assert len(allocated_segments) == len(segments)
    assert len(packs) == 16
    assert all(pack.segments for pack in packs)
    assert all(
        pack.start_time <= segment.start_time and segment.end_time <= pack.end_time
        for pack in packs
        for segment in pack.segments
    )
    assert all(300.0 <= pack.end_time - pack.start_time <= 600.0 for pack in packs)
    assert any(pack.anchor_frame is None for pack in packs)
    assert all(
        pack.anchor_frame is None
        or pack.start_time <= pack.anchor_frame.timestamp_sec < pack.end_time
        for pack in packs
    )
    assert result.frames == frames
    assert build_evidence_packs(result) == packs


def test_build_evidence_packs_rejects_unmergeable_short_final_remainder():
    segments = [
        _make_segment(0.0, 400.0),
        _make_segment(400.0, 650.0),
    ]
    result = _make_transcription_result(
        frames=[_make_frame(200.0)],
        segments=segments,
    )

    with pytest.raises(ConceptualSegmentationError, match="final transcript remainder"):
        build_evidence_packs(result)


def _make_lecture(lecture_id: str) -> LectureMetadata:
    return LectureMetadata(
        id=lecture_id,
        title=f"Aula {lecture_id}",
        subject="Cardiologia",
        url=f"https://example.com/{lecture_id}",
        estimated_duration_sec=60.0,
    )


def _write_queue(queue_path: Path, lectures: list[LectureMetadata]) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        json.dumps([lecture.model_dump(mode="json") for lecture in lectures])
    )


def _write_transcription_handoff(
    transcribed_dir: Path, result: LectureTranscriptionResult
) -> None:
    transcribed_dir.mkdir(parents=True, exist_ok=True)
    (transcribed_dir / f"{result.lecture_id}_transcription.json").write_text(
        json.dumps(result.model_dump(mode="json"))
    )


def _setup_orchestrator_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        pipeline_module, "TRANSCRIBED_INPUT_DIR", tmp_path / "05_transcribed"
    )
    monkeypatch.setattr(
        pipeline_module, "EVIDENCE_PACKS_OUTPUT_DIR", tmp_path / "06_evidence_packs"
    )


@pytest.mark.asyncio
async def test_orchestrator_processes_queued_lecture_and_saves_one_file_per_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    handoff = _make_transcription_result(
        frames=[_make_frame(150.0)],
        segments=[_make_segment(0.0, 300.0)],
    )
    _write_transcription_handoff(tmp_path / "05_transcribed", handoff)

    orchestrator = EvidencePackOrchestrator(queue_path=queue_path)
    outcomes = await orchestrator.run()

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.succeeded
    assert outcome.lecture_id == "aula-001"
    assert outcome.packs is not None
    assert len(outcome.packs) == 1

    lecture_dir = tmp_path / "06_evidence_packs" / "aula-001"
    assert (lecture_dir / "pack_001.json").exists()
    persisted = json.loads((lecture_dir / "pack_001.json").read_text())
    assert persisted == outcome.packs[0].model_dump(mode="json")


@pytest.mark.asyncio
async def test_orchestrator_prunes_stale_packs_from_a_previous_larger_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    larger_handoff = _make_transcription_result(
        frames=[_make_frame(225.0), _make_frame(675.0)],
        segments=[
            _make_segment(0.0, 450.0),
            _make_segment(450.0, 900.0),
        ],
    )
    _write_transcription_handoff(tmp_path / "05_transcribed", larger_handoff)
    orchestrator = EvidencePackOrchestrator(queue_path=queue_path)
    first_outcomes = await orchestrator.run()
    assert first_outcomes[0].packs is not None
    assert len(first_outcomes[0].packs) == 2

    smaller_handoff = _make_transcription_result(
        frames=[_make_frame(150.0)], segments=[_make_segment(0.0, 300.0)]
    )
    _write_transcription_handoff(tmp_path / "05_transcribed", smaller_handoff)
    second_outcomes = await orchestrator.run()
    assert second_outcomes[0].packs is not None
    assert len(second_outcomes[0].packs) == 1

    lecture_dir = tmp_path / "06_evidence_packs" / "aula-001"
    assert sorted(path.name for path in lecture_dir.glob("pack_*.json")) == [
        "pack_001.json"
    ]


@pytest.mark.asyncio
async def test_orchestrator_clears_leftover_staging_dir_from_a_crashed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    handoff = _make_transcription_result(
        frames=[_make_frame(150.0)], segments=[_make_segment(0.0, 300.0)]
    )
    _write_transcription_handoff(tmp_path / "05_transcribed", handoff)
    leftover_staging_dir = tmp_path / "06_evidence_packs" / "aula-001.staging"
    leftover_staging_dir.mkdir(parents=True)
    (leftover_staging_dir / "pack_099.json").write_text("{}")

    orchestrator = EvidencePackOrchestrator(queue_path=queue_path)
    outcomes = await orchestrator.run()

    assert outcomes[0].succeeded
    lecture_dir = tmp_path / "06_evidence_packs" / "aula-001"
    assert sorted(path.name for path in lecture_dir.glob("pack_*.json")) == [
        "pack_001.json"
    ]
    assert not leftover_staging_dir.exists()


@pytest.mark.asyncio
async def test_orchestrator_clears_leftover_backup_dir_from_a_crashed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    handoff = _make_transcription_result(
        frames=[_make_frame(150.0)], segments=[_make_segment(0.0, 300.0)]
    )
    _write_transcription_handoff(tmp_path / "05_transcribed", handoff)
    leftover_backup_dir = tmp_path / "06_evidence_packs" / "aula-001.previous"
    leftover_backup_dir.mkdir(parents=True)
    (leftover_backup_dir / "pack_099.json").write_text("{}")

    orchestrator = EvidencePackOrchestrator(queue_path=queue_path)
    outcomes = await orchestrator.run()

    assert outcomes[0].succeeded
    lecture_dir = tmp_path / "06_evidence_packs" / "aula-001"
    assert sorted(path.name for path in lecture_dir.glob("pack_*.json")) == [
        "pack_001.json"
    ]
    assert not leftover_backup_dir.exists()


@pytest.mark.asyncio
async def test_orchestrator_preserves_previous_packs_when_a_rerun_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    handoff = _make_transcription_result(
        frames=[_make_frame(150.0)], segments=[_make_segment(0.0, 300.0)]
    )
    _write_transcription_handoff(tmp_path / "05_transcribed", handoff)
    orchestrator = EvidencePackOrchestrator(queue_path=queue_path)
    first_outcomes = await orchestrator.run()
    assert first_outcomes[0].succeeded

    (tmp_path / "05_transcribed" / "aula-001_transcription.json").unlink()
    second_outcomes = await orchestrator.run()
    assert not second_outcomes[0].succeeded

    lecture_dir = tmp_path / "06_evidence_packs" / "aula-001"
    assert (lecture_dir / "pack_001.json").exists()


@pytest.mark.asyncio
async def test_orchestrator_processes_lectures_concurrently_up_to_the_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import threading
    import time

    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    lecture_ids = [f"aula-{i:03d}" for i in range(6)]
    for lecture_id in lecture_ids:
        handoff = LectureTranscriptionResult(
            lecture_id=lecture_id,
            frames=[_make_frame(150.0)],
            segments=[_make_segment(0.0, 300.0)],
        )
        _write_transcription_handoff(tmp_path / "05_transcribed", handoff)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture(lid) for lid in lecture_ids])

    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()
    original_build = pipeline_module.build_evidence_packs

    def _tracking_build(handoff: LectureTranscriptionResult) -> object:
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.05)
        result = original_build(handoff)
        with lock:
            in_flight -= 1
        return result

    monkeypatch.setattr(pipeline_module, "build_evidence_packs", _tracking_build)

    orchestrator = EvidencePackOrchestrator(queue_path=queue_path)
    outcomes = await orchestrator.run()

    assert all(outcome.succeeded for outcome in outcomes)
    assert max_in_flight > 1


@pytest.mark.asyncio
async def test_orchestrator_isolates_failure_and_continues_other_lectures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001"), _make_lecture("aula-002")])
    handoff = _make_transcription_result(
        frames=[_make_frame(150.0)], segments=[_make_segment(0.0, 300.0)]
    )
    _write_transcription_handoff(tmp_path / "05_transcribed", handoff)
    # Only aula-001 has a transcription handoff; aula-002 must fail in isolation.

    orchestrator = EvidencePackOrchestrator(queue_path=queue_path)
    outcomes = await orchestrator.run()

    outcomes_by_id = {outcome.lecture_id: outcome for outcome in outcomes}
    assert outcomes_by_id["aula-001"].succeeded
    assert not outcomes_by_id["aula-002"].succeeded
    assert "aula-002" in (outcomes_by_id["aula-002"].error or "")


def test_orchestrator_raises_when_no_transcription_handoff_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    orchestrator = EvidencePackOrchestrator(queue_path=tmp_path / "queue.json")

    with pytest.raises(TranscriptionHandoffNotFoundError):
        orchestrator._read_transcription_handoff("aula-999")


@pytest.mark.asyncio
async def test_orchestrator_rejects_queue_with_duplicate_lecture_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001"), _make_lecture("aula-001")])

    orchestrator = EvidencePackOrchestrator(queue_path=queue_path)

    with pytest.raises(ValueError, match="duplicate lecture ids"):
        await orchestrator.run()


def test_orchestrator_default_concurrency_limit_is_four(tmp_path: Path):
    orchestrator = EvidencePackOrchestrator(queue_path=tmp_path / "queue.json")

    assert orchestrator._semaphore._value == 4


def test_orchestrator_honors_explicit_concurrency_limit(tmp_path: Path):
    orchestrator = EvidencePackOrchestrator(
        queue_path=tmp_path / "queue.json", concurrency_limit=2
    )

    assert orchestrator._semaphore._value == 2
