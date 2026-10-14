"""Codex adapter behaviour against a real stub child process."""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture_study.llm import codex_adapter
from lecture_study.llm.codex_adapter import CodexCliAdapter
from lecture_study.llm.gateway import (
    LlmCallError,
    LlmRequest,
    PromptTooLargeError,
    Stage,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROMPT_MARKER = "PROMPT-MARKER-1"
RESPONSE_TEXT = '{"answer": "stub response"}'


def _only_call(codex_stub):
    calls = codex_stub.control.calls("codex")
    assert len(calls) == 1
    return calls[0]


def test_complete_returns_response_bytes_with_the_codex_argv(codex_stub) -> None:
    codex_stub.control.script("codex", [{"response_file_text": RESPONSE_TEXT}])
    adapter = CodexCliAdapter(str(codex_stub.executable))

    response = adapter.complete(LlmRequest(Stage.SEMANTIC, PROMPT_MARKER))

    assert response.content == RESPONSE_TEXT.encode()
    argv = _only_call(codex_stub)["argv"]
    scratch_dir = argv[argv.index("-C") + 1]
    assert argv == [
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-C",
        scratch_dir,
        "-o",
        str(Path(scratch_dir) / "response.txt"),
        PROMPT_MARKER,
    ]


def test_child_runs_in_a_scratch_directory_outside_the_repository(
    codex_stub,
) -> None:
    codex_stub.control.script("codex", [{"response_file_text": RESPONSE_TEXT}])

    CodexCliAdapter(str(codex_stub.executable)).complete(
        LlmRequest(Stage.SEMANTIC, PROMPT_MARKER)
    )

    call = _only_call(codex_stub)
    scratch_dir = Path(call["argv"][call["argv"].index("-C") + 1])
    logged_cwd = Path(call["cwd"]).resolve()
    assert logged_cwd == scratch_dir.resolve()
    assert logged_cwd != REPOSITORY_ROOT
    assert logged_cwd != Path.cwd().resolve()
    assert not scratch_dir.exists()


def test_non_zero_exit_raises_a_call_error_without_the_prompt(codex_stub) -> None:
    codex_stub.control.script("codex", [{"exit_code": 3, "stderr": PROMPT_MARKER}])
    adapter = CodexCliAdapter(str(codex_stub.executable))

    with pytest.raises(LlmCallError) as raised:
        adapter.complete(LlmRequest(Stage.SEMANTIC, PROMPT_MARKER))

    assert PROMPT_MARKER not in str(raised.value)
    assert "non-zero exit code 3" in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_timeout_raises_a_call_error_without_the_prompt(codex_stub) -> None:
    codex_stub.control.script("codex", [{"sleep_seconds": 5}])
    adapter = CodexCliAdapter(str(codex_stub.executable), timeout_seconds=0.5)

    with pytest.raises(LlmCallError) as raised:
        adapter.complete(LlmRequest(Stage.DRAFTING, PROMPT_MARKER))

    assert "timed out" in str(raised.value)
    assert PROMPT_MARKER not in str(raised.value)


def test_per_request_timeout_overrides_the_adapter_default(codex_stub) -> None:
    codex_stub.control.script("codex", [{"sleep_seconds": 5}])
    adapter = CodexCliAdapter(str(codex_stub.executable), timeout_seconds=60.0)

    with pytest.raises(LlmCallError) as raised:
        adapter.complete(LlmRequest(Stage.PODCAST, PROMPT_MARKER, timeout_seconds=0.5))

    assert "timed out" in str(raised.value)
    assert PROMPT_MARKER not in str(raised.value)


def test_output_cap_overflow_raises_a_call_error(codex_stub) -> None:
    codex_stub.control.script("codex", [{"stdout": "x" * 4096}])
    adapter = CodexCliAdapter(str(codex_stub.executable), maximum_output_bytes=1024)

    with pytest.raises(LlmCallError) as raised:
        adapter.complete(LlmRequest(Stage.CRITIC, PROMPT_MARKER))

    assert "output exceeded 1024 bytes" in str(raised.value)
    assert PROMPT_MARKER not in str(raised.value)


def test_zero_exit_without_a_response_file_raises_a_call_error(codex_stub) -> None:
    codex_stub.control.script("codex", [{}])
    adapter = CodexCliAdapter(str(codex_stub.executable))

    with pytest.raises(LlmCallError) as raised:
        adapter.complete(LlmRequest(Stage.EXAMS, PROMPT_MARKER))

    assert "wrote no response file" in str(raised.value)
    assert PROMPT_MARKER not in str(raised.value)


def test_missing_executable_raises_a_call_error(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(str(tmp_path / "absent-codex"))

    with pytest.raises(LlmCallError) as raised:
        adapter.complete(LlmRequest(Stage.FLASHCARDS, PROMPT_MARKER))

    assert "could not start the process" in str(raised.value)
    assert PROMPT_MARKER not in str(raised.value)
    assert raised.value.__cause__ is None


def test_oversized_prompt_is_refused_before_spawning(codex_stub) -> None:
    adapter = CodexCliAdapter(str(codex_stub.executable), argv_budget_bytes=4096)
    oversized_prompt = PROMPT_MARKER + "a" * 8192

    with pytest.raises(PromptTooLargeError) as raised:
        adapter.complete(LlmRequest(Stage.PODCAST, oversized_prompt))

    assert raised.value.unit == "bytes"
    assert raised.value.limit == 4096
    assert raised.value.required > 8192
    assert PROMPT_MARKER not in str(raised.value)
    assert codex_stub.control.calls("codex") == []


def test_child_environment_keeps_home_and_drops_credentials(
    codex_stub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home_directory = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_directory))
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-value")
    monkeypatch.setenv("PORTAL_PASS", "placeholder-value")
    codex_stub.control.script("codex", [{"response_file_text": RESPONSE_TEXT}])

    CodexCliAdapter(str(codex_stub.executable)).complete(
        LlmRequest(Stage.SEMANTIC, PROMPT_MARKER)
    )

    child_environment = _only_call(codex_stub)["env"]
    assert child_environment["HOME"] == str(home_directory)
    assert "OPENAI_API_KEY" not in child_environment
    assert "PORTAL_PASS" not in child_environment


def test_prompt_with_a_lone_surrogate_raises_a_call_error_before_spawning(
    codex_stub,
) -> None:
    adapter = CodexCliAdapter(str(codex_stub.executable))
    unencodable_prompt = PROMPT_MARKER + " \ud800 tail"

    with pytest.raises(LlmCallError) as raised:
        adapter.complete(LlmRequest(Stage.SEMANTIC, unencodable_prompt))

    assert "not encodable" in str(raised.value)
    assert PROMPT_MARKER not in str(raised.value)
    assert raised.value.__cause__ is None
    assert codex_stub.control.calls("codex") == []


def test_scratch_directory_failure_raises_a_call_error_without_the_prompt(
    codex_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_to_create(*_arguments: str, **_keywords: str) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(codex_adapter.tempfile, "TemporaryDirectory", refuse_to_create)
    adapter = CodexCliAdapter(str(codex_stub.executable))

    with pytest.raises(LlmCallError) as raised:
        adapter.complete(LlmRequest(Stage.SEMANTIC, PROMPT_MARKER))

    assert "could not create the scratch directory: OSError" in str(raised.value)
    assert PROMPT_MARKER not in str(raised.value)
    assert raised.value.__cause__ is None
    assert codex_stub.control.calls("codex") == []


def test_unreadable_response_file_raises_a_call_error_without_the_prompt(
    tmp_path: Path,
) -> None:
    request = LlmRequest(Stage.SEMANTIC, PROMPT_MARKER)
    directory_in_place_of_file = tmp_path / "response.txt"
    directory_in_place_of_file.mkdir()

    with pytest.raises(LlmCallError) as raised:
        codex_adapter._read_response(request, directory_in_place_of_file)

    assert "could not read the response file: IsADirectoryError" in str(raised.value)
    assert PROMPT_MARKER not in str(raised.value)
    assert raised.value.__cause__ is None
