"""Local llama.cpp adapter: one bounded ``llama-completion`` process per call.

The adapter has no network egress surface (D-09). It never starts a server,
never passes a download, listener or inline-prompt flag, and builds the child
environment from the sanitized allowlist plus ``LLAMA_ARG_OFFLINE=1``. The
prompt travels through a file, never through argv, and its exact token count
is checked with ``llama-tokenize`` before generation, so an oversized prompt
fails loudly instead of being truncated by the engine (D-08, D-23).

Failures surface as typed gateway errors raised ``from None`` with reasons
that never contain the argv or any prompt text. The adapter never parses the
response (D-01); it only removes the end-of-generation marker llama.cpp prints.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from lecture_study.llm.bounded_call import run_adapter_process
from lecture_study.llm.gateway import (
    AdapterName,
    GatewayConfigurationError,
    GatewayIdentity,
    LlmCallError,
    LlmRequest,
    LlmResponse,
    PromptTooLargeError,
    Stage,
)
from lecture_study.ops.process_environment import sanitized_child_process_environment
from lecture_study.quality.prompt_budget import PromptBudget

DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_MAXIMUM_OUTPUT_BYTES = 16 * 1024 * 1024
RETRY_REPEAT_PENALTY = "1.3"
RETRY_REPEAT_LAST_N = "256"

_PROMPT_FILE_NAME = "prompt.txt"
_WORK_DIRECTORY_NAME = "work"
_BUILT_COMMIT_MARKER_NAME = ".built-commit"
_OFFLINE_VARIABLE = "LLAMA_ARG_OFFLINE"
_STRIPPED_VARIABLE_PREFIXES = ("LLAMA_ARG_", "HF_")
_END_OF_TEXT_MARKER = " [end of text]"
_TOKEN_COUNT_PATTERN = re.compile(r"Total number of tokens:\s*(\d+)")
_MODEL_FAMILY_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")
_URL_SEPARATOR = "://"


@dataclass(frozen=True, slots=True)
class StageBudget:
    """Context window, generation cap and sampling temperature of one stage."""

    context_tokens: int
    completion_tokens: int
    temperature: float

    def __post_init__(self) -> None:
        if self.context_tokens <= 0 or self.completion_tokens <= 0:
            raise ValueError("context_tokens and completion_tokens must be positive")
        if not math.isfinite(self.temperature) or self.temperature < 0:
            raise ValueError("temperature must be a non-negative finite number")


DEFAULT_STAGE_BUDGETS: Mapping[Stage, StageBudget] = MappingProxyType(
    {
        Stage.SEMANTIC: StageBudget(12288, 2048, 0.0),
        Stage.DRAFTING: StageBudget(12288, 2048, 0.2),
        Stage.CRITIC: StageBudget(12288, 1024, 0.0),
        Stage.EXAMS: StageBudget(8192, 4096, 0.2),
        Stage.PODCAST: StageBudget(8192, 4096, 0.2),
        Stage.FLASHCARDS: StageBudget(8192, 2048, 0.2),
    }
)


class LlamaCppAdapter:
    """Gateway adapter that runs a local GGUF model through llama.cpp."""

    def __init__(
        self,
        completion_executable: str,
        tokenize_executable: str,
        model_path: Path | str,
        model_family: str,
        *,
        stage_budgets: Mapping[Stage, StageBudget] = DEFAULT_STAGE_BUDGETS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        maximum_output_bytes: int = DEFAULT_MAXIMUM_OUTPUT_BYTES,
    ) -> None:
        self._completion_executable = completion_executable
        self._tokenize_executable = tokenize_executable
        self._model_path = _resolve_model_file(model_path)
        self._model_family = _validated_model_family(model_family)
        self._stage_budgets = _validated_stage_budgets(stage_budgets)
        self._timeout_seconds = timeout_seconds
        self._maximum_output_bytes = maximum_output_bytes

    def describe(self) -> GatewayIdentity:
        return GatewayIdentity(
            AdapterName.LLAMA_CPP,
            model_identifier=f"{self._model_family}:{self._model_path.name}",
            model_path=self._model_path,
            engine_version=_read_engine_version(self._completion_executable),
        )

    def complete(self, request: LlmRequest) -> LlmResponse:
        budget = self._stage_budgets[request.stage]
        environment = _child_environment()
        prefix = f"llama-{request.stage.value}-"
        prompt_bytes = _encoded_prompt(request)
        with tempfile.TemporaryDirectory(prefix=prefix) as raw_dir:
            prompt_file, work_dir = _prepare_scratch(Path(raw_dir), prompt_bytes)
            self._require_prompt_fits(
                request, budget, prompt_file, work_dir, environment
            )
            completed = self._spawn(
                self._completion_argv(request, budget, prompt_file),
                request,
                environment,
                work_dir,
            )
        return LlmResponse(content=_response_bytes(completed.stdout))

    def _require_prompt_fits(
        self,
        request: LlmRequest,
        budget: StageBudget,
        prompt_file: Path,
        work_dir: Path,
        environment: Mapping[str, str],
    ) -> None:
        tokenized = self._spawn(
            self._tokenize_argv(prompt_file), request, environment, work_dir
        )
        prompt_budget = PromptBudget(
            input_tokens=_reported_token_count(request, tokenized.stdout),
            output_reserve_tokens=budget.completion_tokens,
            context_window_tokens=budget.context_tokens,
        )
        if not prompt_budget.fits:
            raise PromptTooLargeError(
                request.stage,
                required=prompt_budget.required_tokens,
                limit=budget.context_tokens,
                unit="tokens",
            )

    def _spawn(
        self,
        argv: list[str],
        request: LlmRequest,
        environment: Mapping[str, str],
        work_dir: Path,
    ) -> subprocess.CompletedProcess[str]:
        return run_adapter_process(
            argv,
            stage=request.stage,
            adapter_name=AdapterName.LLAMA_CPP,
            environment=environment,
            cwd=work_dir,
            timeout_seconds=(
                self._timeout_seconds
                if request.timeout_seconds is None
                else request.timeout_seconds
            ),
            maximum_output_bytes=self._maximum_output_bytes,
        )

    def _tokenize_argv(self, prompt_file: Path) -> list[str]:
        return [
            self._tokenize_executable,
            "-m",
            str(self._model_path),
            "-f",
            str(prompt_file),
            "--no-escape",
            "--ids",
            "--show-count",
        ]

    def _completion_argv(
        self, request: LlmRequest, budget: StageBudget, prompt_file: Path
    ) -> list[str]:
        argv = [
            self._completion_executable,
            "-m",
            str(self._model_path),
            "-f",
            str(prompt_file),
            "-c",
            str(budget.context_tokens),
            "-n",
            str(budget.completion_tokens),
            "--temp",
            str(budget.temperature),
            "--single-turn",
            "--no-display-prompt",
            "--no-escape",
            "--simple-io",
            "--offline",
            "--reasoning",
            "off",
        ]
        if request.is_retry:
            argv += [
                "--repeat-penalty",
                RETRY_REPEAT_PENALTY,
                "--repeat-last-n",
                RETRY_REPEAT_LAST_N,
            ]
        return argv


def _resolve_model_file(model_path: Path | str) -> Path:
    if _URL_SEPARATOR in os.fspath(model_path):
        raise GatewayConfigurationError("the llama model path must not be a URL")
    try:
        resolved_path = Path(model_path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise GatewayConfigurationError(
            "the llama model path does not resolve to an existing file"
        ) from None
    if not resolved_path.is_file():
        raise GatewayConfigurationError("the llama model path must be a regular file")
    return resolved_path


def _validated_model_family(model_family: str) -> str:
    if _MODEL_FAMILY_PATTERN.fullmatch(model_family) is None:
        raise GatewayConfigurationError(
            "the llama model family must match [a-z0-9][a-z0-9._-]*"
        )
    return model_family


def _validated_stage_budgets(
    stage_budgets: Mapping[Stage, StageBudget],
) -> Mapping[Stage, StageBudget]:
    missing_stages = [stage.value for stage in Stage if stage not in stage_budgets]
    if missing_stages:
        raise GatewayConfigurationError(
            f"the llama stage budgets lack: {', '.join(missing_stages)}"
        )
    return stage_budgets


def _read_engine_version(completion_executable: str) -> str | None:
    marker_path = Path(completion_executable).parent.parent / _BUILT_COMMIT_MARKER_NAME
    try:
        commit = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return commit or None


def _child_environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in sanitized_child_process_environment().items()
        if not name.startswith(_STRIPPED_VARIABLE_PREFIXES)
    }
    environment[_OFFLINE_VARIABLE] = "1"
    return environment


def _encoded_prompt(request: LlmRequest) -> bytes:
    try:
        return request.prompt.encode("utf-8")
    except UnicodeEncodeError:
        raise LlmCallError(
            request.stage, AdapterName.LLAMA_CPP, "prompt is not encodable as UTF-8"
        ) from None


def _prepare_scratch(scratch_dir: Path, prompt_bytes: bytes) -> tuple[Path, Path]:
    prompt_file = scratch_dir / _PROMPT_FILE_NAME
    prompt_file.write_bytes(prompt_bytes)
    work_dir = scratch_dir / _WORK_DIRECTORY_NAME
    work_dir.mkdir()
    return prompt_file, work_dir


def _reported_token_count(request: LlmRequest, tokenizer_stdout: str) -> int:
    match = _TOKEN_COUNT_PATTERN.search(tokenizer_stdout)
    if match is None:
        raise LlmCallError(
            request.stage, AdapterName.LLAMA_CPP, "token count not reported"
        )
    return int(match.group(1))


def _response_bytes(completion_stdout: str) -> bytes:
    trimmed_stdout = completion_stdout.rstrip()
    if trimmed_stdout.endswith(_END_OF_TEXT_MARKER):
        trimmed_stdout = trimmed_stdout[: -len(_END_OF_TEXT_MARKER)]
        return trimmed_stdout.encode("utf-8")
    return completion_stdout.encode("utf-8")
