"""Bounded child-process spawn shared by every process-backed adapter.

Process failures surface as ``LlmCallError`` raised ``from None`` with a reason
built from exit codes, limits and output digests only. The reason never
contains the argv or any prompt text, because both embed lecture content.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from lecture_study.llm.gateway import AdapterName, LlmCallError, Stage
from lecture_study.ops.llm_response_contract import llm_output_diagnostic
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    run_bounded_process,
)

type ProcessFailure = (
    subprocess.CalledProcessError
    | subprocess.TimeoutExpired
    | ProcessOutputLimitExceeded
    | OSError
    | RuntimeError
    | ValueError
)


def run_adapter_process(
    argv: Sequence[str],
    *,
    stage: Stage,
    adapter_name: AdapterName,
    environment: Mapping[str, str],
    cwd: Path,
    timeout_seconds: float,
    maximum_output_bytes: int,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded child and map every process failure to ``LlmCallError``.

    ``ValueError`` (which includes ``UnicodeEncodeError``) and ``RuntimeError``
    are mapped too; only the exception type name is reported, because their
    messages can embed argv text.
    """
    try:
        return run_bounded_process(
            argv,
            env=environment,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            maximum_stdout_bytes=maximum_output_bytes,
            maximum_stderr_bytes=maximum_output_bytes,
            check=True,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ProcessOutputLimitExceeded,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        raise LlmCallError(
            stage, adapter_name, describe_process_failure(error)
        ) from None


def describe_process_failure(error: ProcessFailure) -> str:
    """Describe a process failure without argv or prompt content."""
    if isinstance(error, subprocess.CalledProcessError):
        stderr_bytes = (error.stderr or "").encode()
        return (
            f"non-zero exit code {error.returncode}; "
            f"stderr {llm_output_diagnostic(stderr_bytes)}"
        )
    if isinstance(error, subprocess.TimeoutExpired):
        return f"timed out after {error.timeout}s"
    if isinstance(error, ProcessOutputLimitExceeded):
        return f"output exceeded {error.maximum_bytes} bytes on {error.stream_name}"
    if isinstance(error, OSError):
        return f"could not start the process: {error.strerror or type(error).__name__}"
    return f"process helper failed: {type(error).__name__}"
