"""Extraction worker tests: FFmpeg wrappers, Apple Vision OCR, and the
producer-consumer orchestrator. All subprocess and macOS API calls are
mocked; no real ffmpeg or Vision invocation happens in this suite."""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from lecture_study.domain.models import LectureMetadata
from lecture_study.extraction import pipeline as pipeline_module
from lecture_study.extraction.media_workers import (
    _MAXIMUM_VISUAL_FRAME_GAP_SECONDS,
    MediaExtractionError,
    extract_audio,
    extract_frames,
)
from lecture_study.extraction.ocr_worker import OcrError, extract_text_from_image
from lecture_study.extraction.pipeline import (
    ExtractionOrchestrator,
    RawVideoNotFoundError,
)


class FakeProcess:
    """Stand-in for asyncio.subprocess.Process."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode: int | None = None
        self._expected_returncode = returncode
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self._expected_returncode
        return self.returncode


def _patch_subprocess_exec(monkeypatch: pytest.MonkeyPatch, process: FakeProcess):
    captured_args: list[str] = []
    monkeypatch.setattr(
        "lecture_study.extraction.media_workers.resolve_trusted_executable",
        lambda executable, **kwargs: f"/trusted/{executable}",
    )

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        captured_args.extend(args)
        return process

    monkeypatch.setattr(
        "lecture_study.extraction.media_workers.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    return captured_args


def _patch_subprocess_exec_raising(monkeypatch: pytest.MonkeyPatch, error: Exception):
    monkeypatch.setattr(
        "lecture_study.extraction.media_workers.resolve_trusted_executable",
        lambda executable, **kwargs: f"/trusted/{executable}",
    )

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        raise error

    monkeypatch.setattr(
        "lecture_study.extraction.media_workers.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )


@pytest.mark.asyncio
async def test_extract_audio_builds_strict_ffmpeg_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    captured_args = _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))
    video_path = tmp_path / "lecture.mp4"
    output_dir = tmp_path / "audio_out"

    result = await extract_audio(video_path, output_dir)

    assert result == output_dir / "lecture.wav"
    assert "-vn" in captured_args
    assert "-acodec" in captured_args
    assert captured_args[captured_args.index("-acodec") + 1] == "pcm_s16le"
    assert "-ar" in captured_args
    assert captured_args[captured_args.index("-ar") + 1] == "16000"
    assert "-ac" in captured_args
    assert captured_args[captured_args.index("-ac") + 1] == "1"
    assert str(video_path) in captured_args


@pytest.mark.asyncio
async def test_extract_audio_creates_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))
    output_dir = tmp_path / "nested" / "audio_out"
    assert not output_dir.exists()

    await extract_audio(tmp_path / "lecture.mp4", output_dir)

    assert output_dir.exists()


@pytest.mark.asyncio
async def test_extract_audio_raises_when_ffmpeg_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_subprocess_exec_raising(monkeypatch, FileNotFoundError("no ffmpeg"))

    with pytest.raises(MediaExtractionError):
        await extract_audio(tmp_path / "lecture.mp4", tmp_path / "out")


@pytest.mark.asyncio
async def test_extract_audio_raises_on_nonzero_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_subprocess_exec(
        monkeypatch,
        FakeProcess(returncode=1, stderr=b"Invalid data found when processing input"),
    )

    with pytest.raises(MediaExtractionError, match="Invalid data found"):
        await extract_audio(tmp_path / "corrupted.mp4", tmp_path / "out")


@pytest.mark.asyncio
async def test_extract_frames_builds_scene_and_temporal_safety_ffmpeg_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    captured_args = _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))
    output_dir = tmp_path / "slides_out"

    await extract_frames(tmp_path / "lecture.mp4", output_dir)

    assert "-vf" in captured_args
    scene_filter = captured_args[captured_args.index("-vf") + 1]
    assert "select=" in scene_filter
    assert "gt(scene,0.3)" in scene_filter
    assert "isnan(prev_selected_t)" in scene_filter
    assert "gte(t-prev_selected_t,37.5)" in scene_filter
    assert "-fps_mode" in captured_args
    assert captured_args[captured_args.index("-fps_mode") + 1] == "vfr"


def test_temporal_safety_interval_matches_decision_record():
    """ADR 0025 amends ADR 0021's 150-second interval to 37.5 seconds. This
    test binds the module constant to the value stated in that decision
    record, so a silent edit of either side fails the suite."""
    decision_record_path = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "decisions"
        / "0025-denser-slide-frame-extraction.md"
    )
    decision_text = decision_record_path.read_text()

    assert f"{_MAXIMUM_VISUAL_FRAME_GAP_SECONDS} seconds" in decision_text
    assert _MAXIMUM_VISUAL_FRAME_GAP_SECONDS == 37.5


@pytest.mark.asyncio
async def test_extract_frames_renames_sequential_output_to_timestamps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    output_dir = tmp_path / "slides_out"
    output_dir.mkdir()
    (output_dir / "frame_0001.png").write_bytes(b"fake-png-1")
    (output_dir / "frame_0002.png").write_bytes(b"fake-png-2")
    showinfo_log = (
        b"[Parsed_showinfo_1 @ 0x1] n:0 pts:1024 pts_time:5.5 \n"
        b"[Parsed_showinfo_1 @ 0x1] n:1 pts:2048 pts_time:45.5 \n"
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0, stderr=showinfo_log))

    frames = await extract_frames(tmp_path / "lecture.mp4", output_dir)

    assert frames == [
        (5.5, output_dir / "frame_0005.500.png"),
        (45.5, output_dir / "frame_0045.500.png"),
    ]
    assert (output_dir / "frame_0005.500.png").exists()
    assert (output_dir / "frame_0045.500.png").exists()
    assert not (output_dir / "frame_0001.png").exists()
    assert not (output_dir / "frame_0002.png").exists()


@pytest.mark.asyncio
async def test_extract_frames_raises_when_scene_log_frame_count_mismatches_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    output_dir = tmp_path / "slides_out"
    output_dir.mkdir()
    (output_dir / "frame_0001.png").write_bytes(b"fake-png-1")
    showinfo_log = (
        b"[Parsed_showinfo_1 @ 0x1] n:0 pts:1024 pts_time:5.5 \n"
        b"[Parsed_showinfo_1 @ 0x1] n:1 pts:2048 pts_time:45.5 \n"
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0, stderr=showinfo_log))

    with pytest.raises(MediaExtractionError):
        await extract_frames(tmp_path / "lecture.mp4", output_dir)


@pytest.mark.asyncio
async def test_extract_frames_raises_when_log_indices_mismatch_disk_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    output_dir = tmp_path / "slides_out"
    output_dir.mkdir()
    # Disk has frame_0001.png and frame_0002.png (2 files), but the scene
    # log claims indices 0 and 2 (i.e. frame_0001.png and frame_0003.png):
    # same count, different identities.
    (output_dir / "frame_0001.png").write_bytes(b"fake-png-1")
    (output_dir / "frame_0002.png").write_bytes(b"fake-png-2")
    showinfo_log = (
        b"[Parsed_showinfo_1 @ 0x1] n:0 pts:1024 pts_time:5.5 \n"
        b"[Parsed_showinfo_1 @ 0x1] n:2 pts:4096 pts_time:90.0 \n"
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0, stderr=showinfo_log))

    with pytest.raises(MediaExtractionError, match="do not match"):
        await extract_frames(tmp_path / "lecture.mp4", output_dir)


@pytest.mark.asyncio
async def test_extract_frames_raises_when_timestamps_collide_after_formatting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    output_dir = tmp_path / "slides_out"
    output_dir.mkdir()
    (output_dir / "frame_0001.png").write_bytes(b"fake-png-1")
    (output_dir / "frame_0002.png").write_bytes(b"fake-png-2")
    # Both timestamps format to the same "frame_0012.500.png" filename.
    showinfo_log = (
        b"[Parsed_showinfo_1 @ 0x1] n:0 pts:1024 pts_time:12.5001 \n"
        b"[Parsed_showinfo_1 @ 0x1] n:1 pts:2048 pts_time:12.4996 \n"
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0, stderr=showinfo_log))

    with pytest.raises(MediaExtractionError, match="collide"):
        await extract_frames(tmp_path / "lecture.mp4", output_dir)


@pytest.mark.asyncio
async def test_extract_frames_raises_when_ffmpeg_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_subprocess_exec_raising(monkeypatch, FileNotFoundError("no ffmpeg"))

    with pytest.raises(MediaExtractionError):
        await extract_frames(tmp_path / "lecture.mp4", tmp_path / "out")


@pytest.mark.asyncio
async def test_extract_frames_returns_empty_list_when_ffmpeg_reports_no_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    output_dir = tmp_path / "slides_out"
    output_dir.mkdir()
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0, stderr=b""))

    frames = await extract_frames(tmp_path / "lecture.mp4", output_dir)

    assert frames == []


class FakeVisionCandidate:
    def __init__(self, text: str):
        self._text = text

    def string(self) -> str:
        return self._text


class FakeVisionObservation:
    def __init__(self, text: str):
        self._candidate = FakeVisionCandidate(text)

    def topCandidates_(self, max_candidates: int) -> list[FakeVisionCandidate]:
        return [self._candidate]


class FakeTextRequest:
    """Stand-in for Vision.VNRecognizeTextRequest."""

    last_instance: "FakeTextRequest | None" = None

    def __init__(self) -> None:
        self.recognition_level: object = None
        self.recognition_languages: list[str] | None = None
        self.uses_language_correction: bool | None = None
        self._results: list[FakeVisionObservation] = []
        FakeTextRequest.last_instance = self

    @classmethod
    def alloc(cls) -> "FakeTextRequest":
        return cls()

    def init(self) -> "FakeTextRequest":
        return self

    def setRecognitionLevel_(self, level: object) -> None:
        self.recognition_level = level

    def setRecognitionLanguages_(self, languages: list[str]) -> None:
        self.recognition_languages = languages

    def setUsesLanguageCorrection_(self, uses_correction: bool) -> None:
        self.uses_language_correction = uses_correction

    def results(self) -> list[FakeVisionObservation]:
        return self._results

    def set_results(self, observations: list[FakeVisionObservation]) -> None:
        self._results = observations


class FakeImageRequestHandler:
    """Stand-in for Vision.VNImageRequestHandler."""

    def __init__(self, success: bool, error: object, texts: list[str]):
        self._success = success
        self._error = error
        self._texts = texts
        self.cg_image: object = None

    def initWithCGImage_options_(
        self, cg_image: object, options: object
    ) -> "FakeImageRequestHandler":
        self.cg_image = cg_image
        return self

    def performRequests_error_(
        self, requests: list[FakeTextRequest], error: object
    ) -> tuple[bool, object]:
        if self._success:
            requests[0].set_results([FakeVisionObservation(t) for t in self._texts])
        return self._success, self._error


def _patch_vision(
    monkeypatch: pytest.MonkeyPatch,
    success: bool = True,
    error: object = None,
    texts: list[str] | None = None,
    cg_image: object = "fake-cg-image",
    image_source: object = "fake-image-source",
) -> FakeImageRequestHandler:
    handler = FakeImageRequestHandler(success=success, error=error, texts=texts or [])

    class _HandlerAlloc:
        @staticmethod
        def alloc() -> FakeImageRequestHandler:
            return handler

    monkeypatch.setattr(
        "lecture_study.extraction.ocr_worker.VNImageRequestHandler", _HandlerAlloc
    )
    monkeypatch.setattr(
        "lecture_study.extraction.ocr_worker.VNRecognizeTextRequest", FakeTextRequest
    )
    monkeypatch.setattr(
        "lecture_study.extraction.ocr_worker.CGImageSourceCreateWithURL",
        lambda url, options: image_source,
    )
    monkeypatch.setattr(
        "lecture_study.extraction.ocr_worker.CGImageSourceCreateImageAtIndex",
        lambda source, index, options: cg_image,
    )
    monkeypatch.setattr(
        "lecture_study.extraction.ocr_worker.NSURL",
        type("_FakeNSURL", (), {"fileURLWithPath_": staticmethod(lambda path: path)}),
    )
    return handler


def test_extract_text_from_image_returns_joined_recognized_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_vision(monkeypatch, success=True, texts=["Bradicardia", "FC reduzida"])

    text = extract_text_from_image(tmp_path / "frame_0005.500.png")

    assert text == "Bradicardia\nFC reduzida"


def test_extract_text_from_image_returns_empty_string_when_no_text_detected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_vision(monkeypatch, success=True, texts=[])

    text = extract_text_from_image(tmp_path / "frame_blank.png")

    assert text == ""


def test_extract_text_from_image_raises_when_image_source_cannot_be_decoded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_vision(monkeypatch, image_source=None)

    with pytest.raises(OcrError):
        extract_text_from_image(tmp_path / "corrupted.png")


def test_extract_text_from_image_raises_when_cg_image_cannot_be_decoded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_vision(monkeypatch, cg_image=None)

    with pytest.raises(OcrError):
        extract_text_from_image(tmp_path / "corrupted.png")


def test_extract_text_from_image_raises_when_vision_request_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_vision(monkeypatch, success=False, error="VNError: could not process")

    with pytest.raises(OcrError, match="could not process"):
        extract_text_from_image(tmp_path / "frame_0005.500.png")


def test_extract_text_from_image_configures_accurate_pt_br_recognition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from Vision import VNRequestTextRecognitionLevelAccurate

    _patch_vision(monkeypatch, success=True, texts=["Slide"])

    extract_text_from_image(tmp_path / "frame_0005.500.png")

    request = FakeTextRequest.last_instance
    assert request is not None
    assert request.recognition_level == VNRequestTextRecognitionLevelAccurate
    assert request.recognition_languages == ["pt-BR"]
    assert request.uses_language_correction is True


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


def _setup_orchestrator_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pipeline_module, "RAW_VIDEO_DIR", tmp_path / "01_raw")
    monkeypatch.setattr(pipeline_module, "AUDIO_OUTPUT_DIR", tmp_path / "02_audio")
    monkeypatch.setattr(pipeline_module, "SLIDES_OUTPUT_DIR", tmp_path / "03_slides")
    monkeypatch.setattr(
        pipeline_module, "CANONICAL_OUTPUT_DIR", tmp_path / "04_canonical"
    )
    (tmp_path / "01_raw").mkdir()


async def _fake_extract_audio(video_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{video_path.stem}.wav"
    audio_path.write_bytes(b"fake-wav")
    return audio_path


def _fake_extract_frames_for(
    frames_by_video: dict[str, list[tuple[float, Path]]],
):
    async def _fake(video_path: Path, output_dir: Path) -> list[tuple[float, Path]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        return frames_by_video.get(video_path.stem, [])

    return _fake


@pytest.mark.asyncio
async def test_orchestrator_processes_queued_lecture_successfully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    (tmp_path / "01_raw" / "aula-001.mp4").write_bytes(b"fake-video")
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    frame_path = tmp_path / "03_slides" / "aula-001" / "frame_0001.000.png"

    monkeypatch.setattr(pipeline_module, "extract_audio", _fake_extract_audio)
    monkeypatch.setattr(
        pipeline_module,
        "extract_frames",
        _fake_extract_frames_for({"aula-001": [(1.0, frame_path)]}),
    )
    monkeypatch.setattr(
        pipeline_module, "extract_text_supervised", lambda path: "Texto do slide"
    )

    orchestrator = ExtractionOrchestrator(queue_path=queue_path, concurrency_limit=2)
    outcomes = await orchestrator.run()

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.succeeded
    assert outcome.lecture_id == "aula-001"
    assert outcome.result is not None
    assert outcome.result.audio_path == (
        tmp_path / "02_audio" / "aula-001" / "aula-001.wav"
    )
    assert outcome.result.frames[0].ocr_text == "Texto do slide"
    assert outcome.result.frames[0].image_path == frame_path

    persisted = json.loads((tmp_path / "04_canonical" / "aula-001.json").read_text())
    assert persisted == outcome.result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_orchestrator_isolates_failure_and_continues_other_lectures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    (tmp_path / "01_raw" / "aula-001.mp4").write_bytes(b"fake-video")
    # aula-002 has no raw video file on disk: must fail without aborting aula-001.
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001"), _make_lecture("aula-002")])

    monkeypatch.setattr(pipeline_module, "extract_audio", _fake_extract_audio)
    monkeypatch.setattr(pipeline_module, "extract_frames", _fake_extract_frames_for({}))
    monkeypatch.setattr(
        pipeline_module, "extract_text_supervised", lambda path: "Texto do slide"
    )

    orchestrator = ExtractionOrchestrator(queue_path=queue_path, concurrency_limit=2)
    outcomes = await orchestrator.run()

    outcomes_by_id = {outcome.lecture_id: outcome for outcome in outcomes}
    assert outcomes_by_id["aula-001"].succeeded
    assert not outcomes_by_id["aula-002"].succeeded
    assert "aula-002" in (outcomes_by_id["aula-002"].error or "")


def test_orchestrator_raises_when_no_raw_video_matches_lecture_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(pipeline_module, "RAW_VIDEO_DIR", tmp_path)
    orchestrator = ExtractionOrchestrator(queue_path=tmp_path / "queue.json")

    with pytest.raises(RawVideoNotFoundError):
        orchestrator._resolve_raw_video_path("aula-999")


def test_orchestrator_raises_when_multiple_raw_videos_match_lecture_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(pipeline_module, "RAW_VIDEO_DIR", tmp_path)
    (tmp_path / "aula-001.mp4").write_bytes(b"a")
    (tmp_path / "aula-001.mkv").write_bytes(b"b")
    orchestrator = ExtractionOrchestrator(queue_path=tmp_path / "queue.json")

    with pytest.raises(RawVideoNotFoundError):
        orchestrator._resolve_raw_video_path("aula-001")


@pytest.mark.asyncio
async def test_orchestrator_respects_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    lecture_ids = [f"aula-{i:03d}" for i in range(4)]
    for lecture_id in lecture_ids:
        (tmp_path / "01_raw" / f"{lecture_id}.mp4").write_bytes(b"fake-video")
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture(lid) for lid in lecture_ids])

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def _tracking_extract_audio(video_path: Path, output_dir: Path) -> Path:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{video_path.stem}.wav"

    monkeypatch.setattr(pipeline_module, "extract_audio", _tracking_extract_audio)
    monkeypatch.setattr(pipeline_module, "extract_frames", _fake_extract_frames_for({}))
    monkeypatch.setattr(pipeline_module, "extract_text_supervised", lambda path: "")

    orchestrator = ExtractionOrchestrator(queue_path=queue_path, concurrency_limit=2)
    outcomes = await orchestrator.run()

    assert all(outcome.succeeded for outcome in outcomes)
    # Both bounds matter: <=2 proves the semaphore caps concurrency, >=2
    # proves it is not accidentally over-serializing (e.g. limit=1 would
    # also pass a <=2-only assertion).
    assert max_in_flight <= 2
    assert max_in_flight >= 2


@pytest.mark.asyncio
async def test_orchestrator_respects_ocr_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    (tmp_path / "01_raw" / "aula-001.mp4").write_bytes(b"fake-video")
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    frame_paths = [
        tmp_path / "03_slides" / "aula-001" / f"frame_000{i}.000.png" for i in range(8)
    ]

    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()

    def _tracking_extract_text_from_image(frame_path: Path) -> str:
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.01)
        with lock:
            in_flight -= 1
        return "Texto"

    monkeypatch.setattr(pipeline_module, "extract_audio", _fake_extract_audio)
    monkeypatch.setattr(
        pipeline_module,
        "extract_frames",
        _fake_extract_frames_for(
            {"aula-001": [(float(i), path) for i, path in enumerate(frame_paths)]}
        ),
    )
    monkeypatch.setattr(
        pipeline_module, "extract_text_supervised", _tracking_extract_text_from_image
    )

    orchestrator = ExtractionOrchestrator(
        queue_path=queue_path, concurrency_limit=2, ocr_concurrency_limit=3
    )
    outcomes = await orchestrator.run()

    assert outcomes[0].succeeded
    assert outcomes[0].result is not None
    assert len(outcomes[0].result.frames) == 8
    # Both bounds matter: <=3 proves the OCR semaphore caps fan-out, >=3
    # proves it is not accidentally over-serializing.
    assert max_in_flight <= 3
    assert max_in_flight >= 3


@pytest.mark.asyncio
async def test_orchestrator_writes_no_overlapping_frame_paths_across_concurrent_lectures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Regression test for the frame-collision bug fixed by scoping output
    under an AUDIO_OUTPUT_DIR/SLIDES_OUTPUT_DIR/{lecture.id} subdirectory.

    The fake below derives every frame path from the `output_dir` argument
    the orchestrator actually passes in, using the same filenames
    (frame_0000.png, frame_0001.png, ...) for every lecture — exactly what
    real FFmpeg does (it names slide frames sequentially regardless of
    source video). If `_extract_lecture` ever regresses to sharing one
    output_dir across lectures (dropping the `/ lecture.id` join), this
    fake reproduces the real collision and the set()-based assertion below
    catches it; a hand-built per-lecture-id path set would not."""
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    lecture_ids = [f"aula-{i:03d}" for i in range(4)]
    for lecture_id in lecture_ids:
        (tmp_path / "01_raw" / f"{lecture_id}.mp4").write_bytes(b"fake-video")
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture(lid) for lid in lecture_ids])

    async def _fake_extract_frames(
        video_path: Path, output_dir: Path
    ) -> list[tuple[float, Path]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        return [(float(i), output_dir / f"frame_{i:04d}.png") for i in range(3)]

    async def _fake_extract_audio_shared_name(
        video_path: Path, output_dir: Path
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / "audio.wav"
        audio_path.write_bytes(b"fake-wav")
        return audio_path

    monkeypatch.setattr(
        pipeline_module, "extract_audio", _fake_extract_audio_shared_name
    )
    monkeypatch.setattr(pipeline_module, "extract_frames", _fake_extract_frames)
    monkeypatch.setattr(pipeline_module, "extract_text_supervised", lambda path: "")

    orchestrator = ExtractionOrchestrator(queue_path=queue_path, concurrency_limit=4)
    outcomes = await orchestrator.run()

    assert all(outcome.succeeded for outcome in outcomes)
    all_image_paths = [
        frame.image_path
        for outcome in outcomes
        for frame in outcome.result.frames  # type: ignore[union-attr]
    ]
    assert len(all_image_paths) == len(set(all_image_paths))
    all_audio_paths = [outcome.result.audio_path for outcome in outcomes]  # type: ignore[union-attr]
    assert len(all_audio_paths) == len(set(all_audio_paths))


@pytest.mark.asyncio
async def test_orchestrator_rejects_queue_with_duplicate_lecture_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Two queue entries sharing one lecture.id would race on the same
    AUDIO_OUTPUT_DIR/{id} and SLIDES_OUTPUT_DIR/{id} subdirectory under
    concurrency_limit > 1; _read_queue must fail fast instead."""
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001"), _make_lecture("aula-001")])

    orchestrator = ExtractionOrchestrator(queue_path=queue_path)

    with pytest.raises(ValueError, match="duplicate lecture ids"):
        await orchestrator.run()
