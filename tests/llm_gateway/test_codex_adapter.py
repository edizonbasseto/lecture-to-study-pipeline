"""Codex adapter behaviour against a real stub child process."""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture_study.llm.codex_adapter import CodexCliAdapter
from lecture_study.llm.gateway import LlmCallError, LlmRequest, Stage

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
