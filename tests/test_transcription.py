"""Transcription worker tests: the whisper.cpp (Metal) async wrapper and the
producer-consumer orchestrator. All subprocess calls are mocked; no real
whisper.cpp invocation happens in this suite."""

import asyncio
import json
from pathlib import Path

import pytest

from lecture_study.domain.models import (
    LectureExtractionResult,
    LectureMetadata,
    TranscriptionSegment,
)
from lecture_study.transcription import pipeline as pipeline_module
from lecture_study.transcription.pipeline import (
    CanonicalHandoffNotFoundError,
    TranscriptionOrchestrator,
)
from lecture_study.transcription.terminology import DOMAIN_VOCABULARY_PROMPT
from lecture_study.transcription.whisper_worker import (
    WhisperTranscriptionError,
    run_transcription,
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
        "lecture_study.transcription.whisper_worker.resolve_trusted_executable",
        lambda executable, **kwargs: "/trusted/whisper-cli",
    )

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        captured_args.extend(args)
        return process

    monkeypatch.setattr(
        "lecture_study.transcription.whisper_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    return captured_args


def _patch_subprocess_exec_raising(monkeypatch: pytest.MonkeyPatch, error: Exception):
    monkeypatch.setattr(
        "lecture_study.transcription.whisper_worker.resolve_trusted_executable",
        lambda executable, **kwargs: "/trusted/whisper-cli",
    )

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        raise error

    monkeypatch.setattr(
        "lecture_study.transcription.whisper_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )


def _write_whisper_json(json_path: Path, entries: list[tuple[int, int, str]]) -> None:
    payload = {
        "transcription": [
            {"offsets": {"from": start_ms, "to": end_ms}, "text": text}
            for start_ms, end_ms, text in entries
        ]
    }
    json_path.write_text(json.dumps(payload))


@pytest.mark.asyncio
async def test_run_transcription_builds_strict_whisper_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    model_path = tmp_path / "ggml-model.bin"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(json_path, [(0, 5000, "Ola mundo")])
    captured_args = _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    await run_transcription(audio_path, model_path)

    assert captured_args == [
        "/trusted/whisper-cli",
        "-m",
        str(model_path),
        "-f",
        str(audio_path),
        "-oj",
        "-l",
        "pt",
        "--prompt",
        DOMAIN_VOCABULARY_PROMPT,
        "--carry-initial-prompt",
    ]


@pytest.mark.asyncio
async def test_run_transcription_carries_the_domain_vocabulary_into_every_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The truncated-syllable defect ("cisional" for excisional) recurs in
    any window the prompt does not reach, so the prompt must be carried,
    not applied to the first window alone."""
    audio_path = tmp_path / "aula-001.wav"
    model_path = tmp_path / "ggml-model.bin"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(json_path, [(0, 5000, "Ola mundo")])
    captured_args = _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    await run_transcription(audio_path, model_path)

    assert "--carry-initial-prompt" in captured_args
    assert "biópsia excisional" in DOMAIN_VOCABULARY_PROMPT
    assert "histopatológico" in DOMAIN_VOCABULARY_PROMPT


@pytest.mark.asyncio
async def test_run_transcription_parses_segments_from_json_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    model_path = tmp_path / "ggml-model.bin"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path,
        [(0, 5000, " Ola mundo "), (5000, 12500, "Segunda fala")],
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    segments = await run_transcription(audio_path, model_path)

    assert len(segments) == 2
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 5.0
    assert segments[0].text == "Ola mundo"
    assert segments[1].start_time == 5.0
    assert segments[1].end_time == 12.5
    assert segments[1].text == "Segunda fala"


@pytest.mark.asyncio
async def test_run_transcription_removes_adjacent_duplicate_point_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path,
        [
            (0, 1000, "Primeira fala"),
            (1000, 1000, "Primeira fala"),
            (1000, 2000, "Segunda fala"),
        ],
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    segments = await run_transcription(audio_path, tmp_path / "model.bin")

    assert [
        (segment.start_time, segment.end_time, segment.text) for segment in segments
    ] == [
        (0.0, 1.0, "Primeira fala"),
        (1.0, 2.0, "Segunda fala"),
    ]


@pytest.mark.asyncio
async def test_run_transcription_removes_adjacent_suffix_duplicate_point_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path,
        [
            (0, 1000, "Trecho em que o cirurgião posiciona o dispositivo"),
            (1000, 1000, "o cirurgião posiciona o dispositivo"),
            (1000, 2000, "Segunda fala"),
        ],
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    segments = await run_transcription(audio_path, tmp_path / "model.bin")

    assert [
        (segment.start_time, segment.end_time, segment.text) for segment in segments
    ] == [
        (0.0, 1.0, "Trecho em que o cirurgião posiciona o dispositivo"),
        (1.0, 2.0, "Segunda fala"),
    ]


@pytest.mark.asyncio
async def test_run_transcription_merges_unique_boundary_point_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path,
        [
            (0, 1000, "Primeira fala"),
            (1000, 1000, "Conteúdo sem duração"),
            (1000, 2000, "Segunda fala"),
        ],
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    segments = await run_transcription(audio_path, tmp_path / "model.bin")

    assert [
        (segment.start_time, segment.end_time, segment.text) for segment in segments
    ] == [
        (0.0, 1.0, "Primeira fala Conteúdo sem duração"),
        (1.0, 2.0, "Segunda fala"),
    ]


@pytest.mark.asyncio
async def test_run_transcription_rejects_non_boundary_point_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path,
        [
            (0, 1000, "Primeira fala"),
            (500, 500, "Conteúdo sem duração"),
        ],
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    with pytest.raises(WhisperTranscriptionError, match="zero-duration"):
        await run_transcription(audio_path, tmp_path / "model.bin")


@pytest.mark.asyncio
async def test_run_transcription_merges_terminal_point_after_silent_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path,
        [
            (0, 1000, "Primeira fala"),
            (4000, 4000, "Opa."),
        ],
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    segments = await run_transcription(audio_path, tmp_path / "model.bin")

    assert [
        (segment.start_time, segment.end_time, segment.text) for segment in segments
    ] == [(0.0, 1.0, "Primeira fala Opa.")]


@pytest.mark.asyncio
async def test_run_transcription_assigns_point_inside_gap_to_available_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path,
        [
            (0, 1000, "Primeira fala"),
            (1200, 1200, "Transição"),
            (1500, 2000, "Segunda fala"),
        ],
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    segments = await run_transcription(audio_path, tmp_path / "model.bin")

    assert [
        (segment.start_time, segment.end_time, segment.text) for segment in segments
    ] == [
        (0.0, 1.0, "Primeira fala"),
        (1.2, 1.5, "Transição"),
        (1.5, 2.0, "Segunda fala"),
    ]


@pytest.mark.asyncio
async def test_run_transcription_prepends_point_at_next_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path,
        [
            (0, 1000, "Primeira fala"),
            (1500, 1500, "Transição"),
            (1500, 2000, "Segunda fala"),
        ],
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    segments = await run_transcription(audio_path, tmp_path / "model.bin")

    assert segments[-1].text == "Transição Segunda fala"


@pytest.mark.asyncio
async def test_run_transcription_deletes_ephemeral_json_after_parse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    model_path = tmp_path / "ggml-model.bin"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(json_path, [(0, 1000, "Fala")])
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    await run_transcription(audio_path, model_path)

    assert not json_path.exists()


@pytest.mark.asyncio
async def test_run_transcription_raises_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_subprocess_exec_raising(monkeypatch, FileNotFoundError("no whisper.cpp"))

    with pytest.raises(WhisperTranscriptionError):
        await run_transcription(tmp_path / "aula-001.wav", tmp_path / "model.bin")


@pytest.mark.asyncio
async def test_run_transcription_raises_on_nonzero_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_subprocess_exec(
        monkeypatch,
        FakeProcess(returncode=1, stderr=b"error: failed to load model"),
    )

    with pytest.raises(WhisperTranscriptionError, match="failed to load model"):
        await run_transcription(tmp_path / "aula-001.wav", tmp_path / "missing.bin")


@pytest.mark.asyncio
async def test_run_transcription_deletes_partial_json_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Regression test: whisper.cpp can write a partial -oj file before
    crashing (e.g. Metal contention). The ephemeral file must not survive
    a failed run, matching run_transcription's own documented contract."""
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(json_path, [(0, 1000, "Fala parcial")])
    _patch_subprocess_exec(
        monkeypatch, FakeProcess(returncode=1, stderr=b"metal contention crash")
    )

    with pytest.raises(WhisperTranscriptionError):
        await run_transcription(audio_path, tmp_path / "model.bin")

    assert not json_path.exists()


@pytest.mark.asyncio
async def test_run_transcription_raises_when_json_output_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    with pytest.raises(WhisperTranscriptionError, match="json"):
        await run_transcription(tmp_path / "aula-001.wav", tmp_path / "model.bin")


@pytest.mark.asyncio
async def test_run_transcription_raises_whisper_error_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Regression test: a whisper.cpp -oj payload missing an expected key
    (offsets/text) must surface as WhisperTranscriptionError, not a raw
    KeyError, matching run_transcription's single documented failure type."""
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    json_path.write_text(json.dumps({"transcription": [{"text": "sem offsets"}]}))
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    with pytest.raises(WhisperTranscriptionError, match="malformed"):
        await run_transcription(audio_path, tmp_path / "model.bin")


@pytest.mark.asyncio
async def test_run_transcription_raises_whisper_error_on_invalid_json_syntax(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    json_path.write_text("{not valid json")
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    with pytest.raises(WhisperTranscriptionError, match="malformed"):
        await run_transcription(audio_path, tmp_path / "model.bin")


@pytest.mark.asyncio
async def test_run_transcription_raises_whisper_error_when_text_is_wrong_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Regression test: a present-but-wrong-typed "text" (null, or a
    non-string) raises AttributeError from .strip() unless wrapped."""
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    json_path.write_text(
        json.dumps(
            {"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": None}]}
        )
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    with pytest.raises(WhisperTranscriptionError, match="malformed"):
        await run_transcription(audio_path, tmp_path / "model.bin")


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


def _write_canonical_handoff(
    canonical_dir: Path, lecture_id: str, audio_path: Path
) -> None:
    canonical_dir.mkdir(parents=True, exist_ok=True)
    result = LectureExtractionResult(
        lecture_id=lecture_id, audio_path=audio_path, frames=[]
    )
    (canonical_dir / f"{lecture_id}.json").write_text(
        json.dumps(result.model_dump(mode="json"))
    )


def _setup_orchestrator_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        pipeline_module, "CANONICAL_INPUT_DIR", tmp_path / "04_canonical"
    )
    monkeypatch.setattr(
        pipeline_module, "TRANSCRIBED_OUTPUT_DIR", tmp_path / "05_transcribed"
    )


@pytest.mark.asyncio
async def test_orchestrator_processes_queued_lecture_successfully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    audio_path = tmp_path / "02_audio" / "aula-001.wav"
    _write_canonical_handoff(tmp_path / "04_canonical", "aula-001", audio_path)

    fake_segments = [TranscriptionSegment(start_time=0.0, end_time=1.0, text="Ola")]

    async def _fake_run_transcription(audio: Path, model: Path) -> list[object]:
        assert audio == audio_path
        return fake_segments

    monkeypatch.setattr(pipeline_module, "run_transcription", _fake_run_transcription)

    orchestrator = TranscriptionOrchestrator(
        queue_path=queue_path, model_path=tmp_path / "model.bin"
    )
    outcomes = await orchestrator.run()

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.succeeded
    assert outcome.lecture_id == "aula-001"
    assert outcome.result is not None
    assert outcome.result.segments == fake_segments

    persisted = json.loads(
        (tmp_path / "05_transcribed" / "aula-001_transcription.json").read_text()
    )
    assert persisted == outcome.result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_orchestrator_skips_lecture_with_existing_transcription_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Filesystem-driven idempotency: an existing output artifact means the
    lecture is already done. queue.json is never consulted for status, and
    whisper.cpp must not be invoked again for it."""
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001")])
    transcribed_dir = tmp_path / "05_transcribed"
    transcribed_dir.mkdir(parents=True)
    (transcribed_dir / "aula-001_transcription.json").write_text("{}")
    # No canonical handoff written: if the orchestrator tried to reprocess
    # this lecture it would raise CanonicalHandoffNotFoundError, failing
    # this test.

    async def _fail_if_called(audio: Path, model: Path) -> list[object]:
        raise AssertionError(
            "run_transcription must not be called for a skipped lecture"
        )

    monkeypatch.setattr(pipeline_module, "run_transcription", _fail_if_called)

    orchestrator = TranscriptionOrchestrator(
        queue_path=queue_path, model_path=tmp_path / "model.bin"
    )
    outcomes = await orchestrator.run()

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.skipped
    assert outcome.succeeded
    assert outcome.result is None
    assert outcome.error is None
    assert json.loads(queue_path.read_text()) == [
        _make_lecture("aula-001").model_dump(mode="json")
    ]


@pytest.mark.asyncio
async def test_orchestrator_isolates_failure_and_continues_other_lectures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001"), _make_lecture("aula-002")])
    # Only aula-001 has a canonical handoff; aula-002 must fail in isolation.
    _write_canonical_handoff(
        tmp_path / "04_canonical", "aula-001", tmp_path / "aula-001.wav"
    )

    async def _fake_run_transcription(audio: Path, model: Path) -> list[object]:
        return []

    monkeypatch.setattr(pipeline_module, "run_transcription", _fake_run_transcription)

    orchestrator = TranscriptionOrchestrator(
        queue_path=queue_path, model_path=tmp_path / "model.bin"
    )
    outcomes = await orchestrator.run()

    outcomes_by_id = {outcome.lecture_id: outcome for outcome in outcomes}
    assert outcomes_by_id["aula-001"].succeeded
    assert not outcomes_by_id["aula-002"].succeeded
    assert "aula-002" in (outcomes_by_id["aula-002"].error or "")


def test_orchestrator_raises_when_no_canonical_handoff_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    orchestrator = TranscriptionOrchestrator(
        queue_path=tmp_path / "queue.json", model_path=tmp_path / "model.bin"
    )

    with pytest.raises(CanonicalHandoffNotFoundError):
        orchestrator._read_canonical_handoff("aula-999")


@pytest.mark.asyncio
async def test_orchestrator_respects_default_concurrency_limit_of_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    lecture_ids = [f"aula-{i:03d}" for i in range(3)]
    for lecture_id in lecture_ids:
        _write_canonical_handoff(
            tmp_path / "04_canonical", lecture_id, tmp_path / f"{lecture_id}.wav"
        )
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture(lid) for lid in lecture_ids])

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def _tracking_run_transcription(audio: Path, model: Path) -> list[object]:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return []

    monkeypatch.setattr(
        pipeline_module, "run_transcription", _tracking_run_transcription
    )

    orchestrator = TranscriptionOrchestrator(
        queue_path=queue_path, model_path=tmp_path / "model.bin"
    )
    outcomes = await orchestrator.run()

    assert all(outcome.succeeded for outcome in outcomes)
    assert max_in_flight == 1


@pytest.mark.asyncio
async def test_orchestrator_rejects_queue_with_duplicate_lecture_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_orchestrator_dirs(monkeypatch, tmp_path)
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_make_lecture("aula-001"), _make_lecture("aula-001")])

    orchestrator = TranscriptionOrchestrator(
        queue_path=queue_path, model_path=tmp_path / "model.bin"
    )

    with pytest.raises(ValueError, match="duplicate lecture ids"):
        await orchestrator.run()


@pytest.mark.asyncio
async def test_run_transcription_rejects_terminal_point_far_from_previous_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path, [(0, 1000, "Primeira fala"), (3600000, 3600000, "Obrigado.")]
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    with pytest.raises(WhisperTranscriptionError):
        await run_transcription(audio_path, tmp_path / "model.bin")


@pytest.mark.asyncio
async def test_run_transcription_does_not_duplicate_repeated_terminal_remark(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio_path = tmp_path / "aula-001.wav"
    json_path = tmp_path / "aula-001.wav.json"
    _write_whisper_json(
        json_path, [(0, 1000, "Muito obrigada. Opa."), (4000, 4000, "Opa.")]
    )
    _patch_subprocess_exec(monkeypatch, FakeProcess(returncode=0))

    segments = await run_transcription(audio_path, tmp_path / "model.bin")

    assert [segment.text for segment in segments] == ["Muito obrigada. Opa."]
