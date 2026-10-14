"""OCR supervisor tests: a real child process is spawned with a fake worker
module so hang, timeout, retry and normal paths are exercised end to end."""

import subprocess
from pathlib import Path

import pytest

from lecture_study.extraction import ocr_supervisor
from lecture_study.extraction.ocr_worker import OcrError


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _script_runner(monkeypatch, outcomes):
    """Replace run_bounded_process with a scripted sequence of outcomes."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs))
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(ocr_supervisor, "run_bounded_process", fake_run)
    return calls


def _timeout():
    return subprocess.TimeoutExpired(["child"], 120)


def test_returns_text_from_child_json(monkeypatch) -> None:
    calls = _script_runner(monkeypatch, [_completed(0, '{"text": "Olá\\nmundo"}\n')])

    text = ocr_supervisor.extract_text_supervised(Path("frame.png"))

    assert text == "Olá\nmundo"
    assert len(calls) == 1
    assert calls[0][0][-2:] == ["lecture_study.extraction.ocr_worker", "frame.png"]
    assert calls[0][1]["timeout_seconds"] == ocr_supervisor.OCR_FRAME_TIMEOUT_SECONDS


def test_retries_once_with_fresh_child_after_timeout(monkeypatch) -> None:
    calls = _script_runner(monkeypatch, [_timeout(), _completed(0, '{"text": "ok"}')])

    assert ocr_supervisor.extract_text_supervised(Path("frame.png")) == "ok"
    assert len(calls) == 2


def test_raises_clear_error_when_both_attempts_hang(monkeypatch) -> None:
    calls = _script_runner(monkeypatch, [_timeout(), _timeout()])

    with pytest.raises(OcrError, match="Vision OCR hung on frame.png"):
        ocr_supervisor.extract_text_supervised(Path("frame.png"))
    assert len(calls) == 2


def test_ocr_failure_is_not_retried(monkeypatch) -> None:
    calls = _script_runner(monkeypatch, [_completed(2, stderr="cannot decode\n")])

    with pytest.raises(OcrError, match="cannot decode"):
        ocr_supervisor.extract_text_supervised(Path("frame.png"))
    assert len(calls) == 1


def test_child_crash_raises_ocr_error(monkeypatch) -> None:
    _script_runner(monkeypatch, [_completed(-11, stderr="segfault")])

    with pytest.raises(OcrError, match="exited -11"):
        ocr_supervisor.extract_text_supervised(Path("frame.png"))


def test_real_hung_child_is_killed_and_reported(monkeypatch, tmp_path) -> None:
    hang_module = tmp_path / "hang_worker.py"
    hang_module.write_text("import time\ntime.sleep(60)\n")
    real_run = ocr_supervisor.run_bounded_process
    spawned = []

    def run_hanging(args, **kwargs):
        spawned.append(1)
        return real_run(["/usr/bin/env", "python3", str(hang_module)], **kwargs)

    monkeypatch.setattr(ocr_supervisor, "run_bounded_process", run_hanging)

    with pytest.raises(OcrError, match="hung"):
        ocr_supervisor.extract_text_supervised(Path("frame.png"), timeout_seconds=0.5)
    assert len(spawned) == 2
