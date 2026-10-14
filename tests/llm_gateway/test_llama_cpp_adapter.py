"""Llama.cpp adapter behaviour against real stub child processes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lecture_study.llm.gateway import (
    AdapterName,
    GatewayConfigurationError,
    LlmCallError,
    LlmRequest,
    PromptTooLargeError,
    Stage,
)
from lecture_study.llm.llama_cpp_adapter import (
    DEFAULT_STAGE_BUDGETS,
    RETRY_REPEAT_LAST_N,
    RETRY_REPEAT_PENALTY,
    LlamaCppAdapter,
    StageBudget,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_ARGUMENTS = frozenset(
    {
        "-hf",
        "-hfr",
        "--hf-repo",
        "-hff",
        "--hf-file",
        "-hft",
        "--hf-token",
        "-mu",
        "--model-url",
        "-dr",
        "--docker-repo",
        "--port",
        "--host",
        "-p",
        "--prompt",
    }
)

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


@pytest.mark.parametrize("stage", list(Stage))
def test_each_stage_uses_its_context_cap_and_temperature(
    llama_stubs, stage: Stage
) -> None:
    budget = DEFAULT_STAGE_BUDGETS[stage]
    _script_success(llama_stubs)

    _adapter(llama_stubs).complete(LlmRequest(stage, PROMPT_TEXT))

    argv = _only_call(llama_stubs, "llama-completion")["argv"]
    assert argv[argv.index("-c") + 1] == str(budget.context_tokens)
    assert argv[argv.index("-n") + 1] == str(budget.completion_tokens)
    assert argv[argv.index("--temp") + 1] == str(budget.temperature)


def test_repeat_penalty_flags_appear_only_on_the_retry_attempt(llama_stubs) -> None:
    llama_stubs.control.script("llama-tokenize", [{"token_count": 10}] * 2)
    llama_stubs.control.script("llama-completion", [{"stdout": "{}"}] * 2)
    adapter = _adapter(llama_stubs)

    adapter.complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))
    adapter.complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT, is_retry=True))

    first_argv, retry_argv = (
        call["argv"] for call in llama_stubs.control.calls("llama-completion")
    )
    assert "--repeat-penalty" not in first_argv
    assert "--repeat-last-n" not in first_argv
    assert retry_argv[-4:] == [
        "--repeat-penalty",
        RETRY_REPEAT_PENALTY,
        "--repeat-last-n",
        RETRY_REPEAT_LAST_N,
    ]
    assert (RETRY_REPEAT_PENALTY, RETRY_REPEAT_LAST_N) == ("1.3", "256")


def test_argv_never_carries_a_download_listener_or_url_argument(llama_stubs) -> None:
    llama_stubs.control.script("llama-tokenize", [{"token_count": 10}] * 2)
    llama_stubs.control.script("llama-completion", [{"stdout": "{}"}] * 2)
    adapter = _adapter(llama_stubs)

    adapter.complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))
    adapter.complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT, is_retry=True))

    for name in ("llama-tokenize", "llama-completion"):
        for call in llama_stubs.control.calls(name):
            argv = call["argv"]
            assert not FORBIDDEN_ARGUMENTS.intersection(argv)
            assert not any(argument.startswith("--hf-") for argument in argv)
            assert not any("://" in argument for argument in argv)


@pytest.mark.parametrize("forbidden_argument", sorted(FORBIDDEN_ARGUMENTS))
def test_the_stub_tripwire_rejects_every_forbidden_argument(
    llama_stubs, forbidden_argument: str
) -> None:
    completed = subprocess.run(
        [str(llama_stubs.completion_executable), forbidden_argument],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 3
    assert "stub tripwire" in completed.stderr


def test_the_stub_tripwire_rejects_urls_and_download_environment(
    llama_stubs,
) -> None:
    executable = str(llama_stubs.completion_executable)

    url_run = subprocess.run(
        [executable, "-m", "https://example.invalid/model.gguf"],
        capture_output=True,
        check=False,
    )
    environment_run = subprocess.run(
        [executable, "-m", "model.gguf"],
        env={"LLAMA_ARG_MODEL_URL": "synthetic-value"},
        capture_output=True,
        check=False,
    )

    assert url_run.returncode == 3
    assert environment_run.returncode == 3


def test_child_environment_is_offline_and_free_of_download_variables(
    llama_stubs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HF_TOKEN", "synthetic-value")
    monkeypatch.setenv("HF_HOME", "synthetic-value")
    monkeypatch.setenv("LLAMA_ARG_MODEL_URL", "synthetic-value")
    monkeypatch.setenv("LLAMA_ARG_HF_REPO", "synthetic-value")
    _script_success(llama_stubs)

    _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    for name in ("llama-tokenize", "llama-completion"):
        environment = _only_call(llama_stubs, name)["env"]
        assert environment["LLAMA_ARG_OFFLINE"] == "1"
        assert "HOME" not in environment
        assert not [key for key in environment if key.startswith("HF_")]
        assert [key for key in environment if key.startswith("LLAMA_ARG_")] == [
            "LLAMA_ARG_OFFLINE"
        ]


def test_child_starts_in_a_fresh_empty_directory_outside_the_repository(
    llama_stubs,
) -> None:
    _script_success(llama_stubs)

    _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    for name in ("llama-tokenize", "llama-completion"):
        call = _only_call(llama_stubs, name)
        child_cwd = Path(str(call["cwd"])).resolve()
        assert call["cwd_entries"] == []
        assert child_cwd != REPOSITORY_ROOT
        assert not child_cwd.exists()


@pytest.mark.parametrize(
    "model_path",
    [
        "https://example.invalid/model.gguf",
        "org/model",
        "-hf",
    ],
)
def test_model_path_that_is_not_an_existing_file_is_rejected(
    llama_stubs, tmp_path: Path, model_path: str
) -> None:
    with pytest.raises(GatewayConfigurationError):
        LlamaCppAdapter(
            str(llama_stubs.completion_executable),
            str(llama_stubs.tokenize_executable),
            model_path,
            MODEL_FAMILY,
        )


def test_model_path_that_is_a_directory_is_rejected(llama_stubs) -> None:
    with pytest.raises(GatewayConfigurationError):
        LlamaCppAdapter(
            str(llama_stubs.completion_executable),
            str(llama_stubs.tokenize_executable),
            llama_stubs.model_path.parent,
            MODEL_FAMILY,
        )


def test_model_path_that_does_not_exist_is_rejected(
    llama_stubs, tmp_path: Path
) -> None:
    with pytest.raises(GatewayConfigurationError):
        LlamaCppAdapter(
            str(llama_stubs.completion_executable),
            str(llama_stubs.tokenize_executable),
            tmp_path / "absent.gguf",
            MODEL_FAMILY,
        )


def test_relative_model_path_is_passed_to_the_child_as_an_absolute_path(
    llama_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(llama_stubs.model_path.parent)
    _script_success(llama_stubs)
    adapter = LlamaCppAdapter(
        str(llama_stubs.completion_executable),
        str(llama_stubs.tokenize_executable),
        "model.gguf",
        MODEL_FAMILY,
    )

    adapter.complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    argv = _only_call(llama_stubs, "llama-completion")["argv"]
    model_argument = argv[argv.index("-m") + 1]
    assert Path(model_argument).is_absolute()
    assert model_argument == str(llama_stubs.model_path.resolve())


@pytest.mark.parametrize("model_family", ["", "Qwen3", "-qwen", "qwen 3", "../qwen"])
def test_invalid_model_family_is_rejected(llama_stubs, model_family: str) -> None:
    with pytest.raises(GatewayConfigurationError):
        LlamaCppAdapter(
            str(llama_stubs.completion_executable),
            str(llama_stubs.tokenize_executable),
            llama_stubs.model_path,
            model_family,
        )


def test_stage_budgets_must_cover_every_stage(llama_stubs) -> None:
    incomplete_budgets = {Stage.SEMANTIC: StageBudget(4096, 512, 0.0)}

    with pytest.raises(GatewayConfigurationError):
        LlamaCppAdapter(
            str(llama_stubs.completion_executable),
            str(llama_stubs.tokenize_executable),
            llama_stubs.model_path,
            MODEL_FAMILY,
            stage_budgets=incomplete_budgets,
        )


def test_oversized_prompt_is_refused_before_completion_is_spawned(
    llama_stubs,
) -> None:
    llama_stubs.control.script("llama-tokenize", [{"token_count": 20000}])
    budget = DEFAULT_STAGE_BUDGETS[Stage.SEMANTIC]

    with pytest.raises(PromptTooLargeError) as raised:
        _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    assert raised.value.unit == "tokens"
    assert raised.value.stage is Stage.SEMANTIC
    assert raised.value.limit == budget.context_tokens
    assert raised.value.required > 20000 + budget.completion_tokens
    assert PROMPT_TEXT not in str(raised.value)
    assert len(llama_stubs.control.calls("llama-tokenize")) == 1
    assert llama_stubs.control.calls("llama-completion") == []


def test_prompt_that_exactly_fits_the_budget_is_completed(llama_stubs) -> None:
    budget = DEFAULT_STAGE_BUDGETS[Stage.SEMANTIC]
    fitting_tokens = budget.context_tokens - budget.completion_tokens - 128
    _script_success(llama_stubs, token_count=fitting_tokens)

    response = _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    assert response.content == RESPONSE_OBJECT.encode()


def test_missing_token_count_raises_a_call_error_before_completion(
    llama_stubs,
) -> None:
    llama_stubs.control.script("llama-tokenize", [{"stdout": "no count here"}])

    with pytest.raises(LlmCallError) as raised:
        _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    assert "token count not reported" in str(raised.value)
    assert llama_stubs.control.calls("llama-completion") == []


def test_non_zero_completion_exit_raises_a_call_error_without_the_prompt(
    llama_stubs,
) -> None:
    llama_stubs.control.script("llama-tokenize", [{"token_count": 10}])
    llama_stubs.control.script(
        "llama-completion", [{"exit_code": 1, "stderr": "prompt is too long"}]
    )

    with pytest.raises(LlmCallError) as raised:
        _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    assert "non-zero exit code 1" in str(raised.value)
    assert PROMPT_TEXT not in str(raised.value)
    assert "prompt is too long" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_non_zero_tokenize_exit_raises_a_call_error_before_completion(
    llama_stubs,
) -> None:
    llama_stubs.control.script("llama-tokenize", [{"exit_code": 2}])

    with pytest.raises(LlmCallError):
        _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    assert llama_stubs.control.calls("llama-completion") == []


def test_output_without_the_end_marker_is_returned_unchanged(llama_stubs) -> None:
    llama_stubs.control.script("llama-tokenize", [{"token_count": 10}])
    llama_stubs.control.script(
        "llama-completion", [{"stdout": '{"cut": ', "end_marker": False}]
    )

    response = _adapter(llama_stubs).complete(LlmRequest(Stage.SEMANTIC, PROMPT_TEXT))

    assert response.content == b'{"cut": '


def test_describe_reports_the_model_and_the_built_engine_commit(
    llama_stubs, tmp_path: Path
) -> None:
    (tmp_path / ".built-commit").write_text("0123abcd\n", encoding="utf-8")

    identity = _adapter(llama_stubs).describe()

    assert identity.adapter_name is AdapterName.LLAMA_CPP
    assert identity.model_identifier == f"{MODEL_FAMILY}:model.gguf"
    assert identity.model_path == llama_stubs.model_path.resolve()
    assert identity.engine_version == "0123abcd"


def test_describe_reports_no_engine_version_without_a_build_marker(
    llama_stubs,
) -> None:
    assert _adapter(llama_stubs).describe().engine_version is None


def test_stage_budget_rejects_non_positive_sizes_and_invalid_temperature() -> None:
    with pytest.raises(ValueError):
        StageBudget(0, 512, 0.0)
    with pytest.raises(ValueError):
        StageBudget(4096, 0, 0.0)
    with pytest.raises(ValueError):
        StageBudget(4096, 512, -0.1)
    with pytest.raises(ValueError):
        StageBudget(4096, 512, float("nan"))
