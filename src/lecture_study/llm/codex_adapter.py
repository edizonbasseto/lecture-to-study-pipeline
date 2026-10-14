"""Codex CLI adapter: today's Codex call, byte for byte, behind the gateway port.

The child runs ``codex exec`` with a read-only sandbox inside a fresh scratch
directory (never the repository root, which holds local credentials). The
environment is the sanitized allowlist plus ``HOME``, which Codex needs to
find its session directory. The adapter never parses the response (D-01).

Every failure surfaces as a typed gateway error raised ``from None`` with a
reason that never contains the argv or any prompt text.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from lecture_study.llm.gateway import (
    AdapterName,
    GatewayIdentity,
    LlmCallError,
    LlmRequest,
    LlmResponse,
    PromptTooLargeError,
)
from lecture_study.ops.llm_response_contract import llm_output_diagnostic
from lecture_study.ops.process_environment import sanitized_child_process_environment
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    run_bounded_process,
)

DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_MAXIMUM_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_ARGV_BUDGET_BYTES = 900 * 1024

_RESPONSE_FILE_NAME = "response.txt"
_SCRATCH_RANDOM_SUFFIX_LENGTH = 8
_MODEL_IDENTIFIER = "codex-cli"


class CodexCliAdapter:
    """Gateway adapter that delegates a completion to the Codex CLI."""

    def __init__(
        self,
        executable: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        maximum_output_bytes: int = DEFAULT_MAXIMUM_OUTPUT_BYTES,
        argv_budget_bytes: int = DEFAULT_ARGV_BUDGET_BYTES,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._maximum_output_bytes = maximum_output_bytes
        self._argv_budget_bytes = argv_budget_bytes

    def describe(self) -> GatewayIdentity:
        return GatewayIdentity(AdapterName.CODEX, _MODEL_IDENTIFIER)

    def complete(self, request: LlmRequest) -> LlmResponse:
        environment = _child_environment()
        self._require_argv_within_budget(request, environment)
        with tempfile.TemporaryDirectory(prefix=_scratch_prefix(request)) as raw_dir:
            scratch_dir = Path(raw_dir)
            response_path = scratch_dir / _RESPONSE_FILE_NAME
            self._run_child(request, scratch_dir, response_path, environment)
            return LlmResponse(content=_read_response(request, response_path))

    def _run_child(
        self,
        request: LlmRequest,
        scratch_dir: Path,
        response_path: Path,
        environment: Mapping[str, str],
    ) -> None:
        arguments = self._build_arguments(request, scratch_dir, response_path)
        timeout_seconds = (
            self._timeout_seconds
            if request.timeout_seconds is None
            else request.timeout_seconds
        )
        try:
            run_bounded_process(
                arguments,
                env=environment,
                cwd=scratch_dir,
                timeout_seconds=timeout_seconds,
                maximum_stdout_bytes=self._maximum_output_bytes,
                maximum_stderr_bytes=self._maximum_output_bytes,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            stderr_bytes = (error.stderr or "").encode()
            reason = (
                f"non-zero exit code {error.returncode}; "
                f"stderr {llm_output_diagnostic(stderr_bytes)}"
            )
            raise _call_error(request, reason) from None
        except subprocess.TimeoutExpired as error:
            raise _call_error(request, f"timed out after {error.timeout}s") from None
        except ProcessOutputLimitExceeded as error:
            reason = (
                f"output exceeded {error.maximum_bytes} bytes on {error.stream_name}"
            )
            raise _call_error(request, reason) from None
        except OSError as error:
            reason = f"could not start the process: {_describe_os_error(error)}"
            raise _call_error(request, reason) from None

    def _require_argv_within_budget(
        self, request: LlmRequest, environment: Mapping[str, str]
    ) -> None:
        probe_scratch_dir = _representative_scratch_dir(request)
        probe_arguments = self._build_arguments(
            request, probe_scratch_dir, probe_scratch_dir / _RESPONSE_FILE_NAME
        )
        cost = _argv_cost_bytes(probe_arguments, environment)
        if cost > self._argv_budget_bytes:
            raise PromptTooLargeError(
                request.stage,
                required=cost,
                limit=self._argv_budget_bytes,
                unit="bytes",
            )

    def _build_arguments(
        self, request: LlmRequest, scratch_dir: Path, response_path: Path
    ) -> list[str]:
        return [
            self._executable,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-C",
            str(scratch_dir),
            "-o",
            str(response_path),
            request.prompt,
        ]


def _child_environment() -> dict[str, str]:
    environment = dict(sanitized_child_process_environment())
    home_directory = os.environ.get("HOME")
    if home_directory is not None:
        environment["HOME"] = home_directory
    return environment


def _scratch_prefix(request: LlmRequest) -> str:
    return f"codex-{request.stage.value}-"


def _representative_scratch_dir(request: LlmRequest) -> Path:
    placeholder_name = _scratch_prefix(request) + "x" * _SCRATCH_RANDOM_SUFFIX_LENGTH
    return Path(tempfile.gettempdir()) / placeholder_name


def _argv_cost_bytes(arguments: Sequence[str], environment: Mapping[str, str]) -> int:
    argument_cost = sum(len(argument.encode()) + 1 for argument in arguments)
    environment_cost = sum(
        len(name.encode()) + len(value.encode()) + 2
        for name, value in environment.items()
    )
    return argument_cost + environment_cost


def _read_response(request: LlmRequest, response_path: Path) -> bytes:
    try:
        return response_path.read_bytes()
    except FileNotFoundError:
        raise _call_error(request, "the process wrote no response file") from None


def _call_error(request: LlmRequest, reason: str) -> LlmCallError:
    return LlmCallError(request.stage, AdapterName.CODEX, reason)


def _describe_os_error(error: OSError) -> str:
    return error.strerror or type(error).__name__
