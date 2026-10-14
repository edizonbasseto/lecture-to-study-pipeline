"""Llama.cpp adapter behaviour against real stub child processes."""

from __future__ import annotations

from lecture_study.llm.gateway import LlmRequest, Stage
from lecture_study.llm.llama_cpp_adapter import LlamaCppAdapter

PROMPT_TEXT = 'LLAMA-PROMPT-MARKER-1 {"schema": "a\\nb"}'
RESPONSE_OBJECT = '{"name": "alpha", "count": 1}'
MODEL_FAMILY = "stub-family"


def _adapter(llama_stubs) -> LlamaCppAdapter:
    return LlamaCppAdapter(
        str(llama_stubs.completion_executable),
        str(llama_stubs.tokenize_executable),
        llama_stubs.model_path,
        MODEL_FAMILY,
    )


def _script_success(
    llama_stubs, *, token_count: int = 100, stdout: str = RESPONSE_OBJECT
) -> None:
    llama_stubs.control.script("llama-tokenize", [{"token_count": token_count}])
    llama_stubs.control.script("llama-completion", [{"stdout": stdout}])


def _only_call(llama_stubs, name: str) -> dict[str, object]:
    (call,) = llama_stubs.control.calls(name)
    return call


def test_complete_returns_the_generated_bytes_without_the_end_marker(
    llama_stubs,
) -> None:
    _script_success(llama_stubs)

    response = _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    assert response.content == RESPONSE_OBJECT.encode()


def test_completion_argv_carries_the_semantic_stage_flags(
    llama_stubs,
) -> None:
    _script_success(llama_stubs)

    _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    argv = _only_call(llama_stubs, "llama-completion")["argv"]
    prompt_file = argv[argv.index("-f") + 1]
    assert argv == [
        "-m",
        str(llama_stubs.model_path.resolve()),
        "-f",
        prompt_file,
        "-c",
        "12288",
        "-n",
        "2048",
        "--temp",
        "0.0",
        "--single-turn",
        "--no-display-prompt",
        "--no-escape",
        "--simple-io",
        "--offline",
        "--reasoning",
        "off",
    ]


def test_tokenize_argv_counts_the_prompt_file_without_escape_processing(
    llama_stubs,
) -> None:
    _script_success(llama_stubs)

    _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    argv = _only_call(llama_stubs, "llama-tokenize")["argv"]
    assert argv == [
        "-m",
        str(llama_stubs.model_path.resolve()),
        "-f",
        argv[argv.index("-f") + 1],
        "--no-escape",
        "--ids",
        "--show-count",
    ]


def test_prompt_travels_in_a_file_and_never_in_argv(llama_stubs) -> None:
    _script_success(llama_stubs)

    _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    for name in ("llama-tokenize", "llama-completion"):
        call = _only_call(llama_stubs, name)
        assert call["prompt_file_text"] == PROMPT_TEXT
        assert all(PROMPT_TEXT not in argument for argument in call["argv"])


def test_tokenize_runs_before_completion(llama_stubs) -> None:
    _script_success(llama_stubs)

    _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    assert llama_stubs.control.call_names() == ["llama-tokenize", "llama-completion"]
