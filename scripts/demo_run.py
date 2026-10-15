#!/usr/bin/env python3
"""Run the synthetic demo lecture end to end with one LLM adapter (ADR 0046, ADR 0047).

The driver runs, in order, `run-all` (stages 2-9 complete, stage 10 refuses by
design because no certificate is issued), `run-personal-study`
(8, 9, 13, 14, 15), the stage 18 flashcards script and the study-guide script.
Each step's output is kept under `workspace/demo/logs/<lecture id>/` and each
step must meet its acceptance rule before the next one starts.

Egress safety: the adapter is forced through `LECTURE_STUDY_LLM_ADAPTER`, the
gateway of every stage is built (preflight) before any process starts, and the
remote `codex` adapter additionally requires `--confirm-remote-egress`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lecture_study.cli.llm_gateway_wiring import preflight_stage_gateways
from lecture_study.demo.harvest import DEMO_LOGS_ROOT
from lecture_study.demo.phase_timing import (
    PhaseLogError,
    parse_phase_log,
    require_completed,
    require_stopped_at_certification,
)
from lecture_study.llm.config import ADAPTER_OVERRIDE_ENV
from lecture_study.llm.gateway import AdapterName, LlmGatewayError, Stage
from lecture_study.ops.process_lifecycle import run_bounded_process

Acceptance = Literal["exit_zero", "stopped_at_certification"]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
DEFAULT_WHISPER_MODEL_PATH = Path("engines/whisper.cpp/models/ggml-large-v3-turbo.bin")
STUDY_GUIDE_ARGUMENTS = (
    "--targets-path",
    "config/targets.example.yaml",
    "--schedule-path",
    "demo/site/academic-calendar.yaml",
)
PERSONAL_STUDY_PHASES = (8, 9, 13, 14, 15)

_PIPELINE_TIMEOUT_SECONDS = 14400.0
_FLASHCARDS_TIMEOUT_SECONDS = 7200.0
_GUIDE_TIMEOUT_SECONDS = 3600.0
_MAXIMUM_STDOUT_BYTES = 16 * 1024 * 1024
_MAXIMUM_STDERR_BYTES = 64 * 1024 * 1024
_REFUSED_EXIT_CODE = 2
_CERTIFICATION_REFUSAL_EXIT_CODE = 1


@dataclass(frozen=True, slots=True)
class DemoRunStep:
    """One child process of the demo run and the rule its outcome must meet."""

    name: str
    argv: tuple[str, ...]
    log_name: str
    acceptance: Acceptance
    timeout_seconds: float
    required_phases: tuple[int, ...] = ()


def pipeline_steps(
    lecture_id: str, whisper_model_path: Path
) -> tuple[DemoRunStep, ...]:
    """The four steps of the demo run, in execution order."""

    cli = (sys.executable, "-m", "lecture_study.cli.main")
    model = ("--whisper-model-path", str(whisper_model_path))
    return (
        DemoRunStep(
            name="run_all",
            argv=(*cli, "run-all", lecture_id, *model),
            log_name="run-all.log",
            acceptance="stopped_at_certification",
            timeout_seconds=_PIPELINE_TIMEOUT_SECONDS,
        ),
        DemoRunStep(
            name="personal_study",
            argv=(*cli, "run-personal-study", lecture_id, *model),
            log_name="personal-study.log",
            acceptance="exit_zero",
            timeout_seconds=_PIPELINE_TIMEOUT_SECONDS,
            required_phases=PERSONAL_STUDY_PHASES,
        ),
        DemoRunStep(
            name="flashcards",
            argv=_script_argv(
                "generate_codex_personal_study_flashcards.py", lecture_id
            ),
            log_name="flashcards.log",
            acceptance="exit_zero",
            timeout_seconds=_FLASHCARDS_TIMEOUT_SECONDS,
        ),
        DemoRunStep(
            name="study_guide",
            argv=_script_argv(
                "generate_personal_study_guides.py", lecture_id, *STUDY_GUIDE_ARGUMENTS
            ),
            log_name="guide.log",
            acceptance="exit_zero",
            timeout_seconds=_GUIDE_TIMEOUT_SECONDS,
        ),
    )


def steps_from(
    steps: Sequence[DemoRunStep], first_step_name: str
) -> tuple[DemoRunStep, ...]:
    """The steps from `first_step_name` on, so a failed step can be rerun alone."""

    names = [step.name for step in steps]
    if first_step_name not in names:
        raise ValueError(f"unknown step {first_step_name!r}; expected one of {names}")
    return tuple(steps[names.index(first_step_name) :])


def rejection_reason(
    step: DemoRunStep, *, exit_code: int, log_text: str, lecture_id: str
) -> str | None:
    """Why the step's outcome is not acceptable, or None when it is."""

    if step.acceptance == "stopped_at_certification":
        return _certification_stop_rejection(exit_code, log_text, lecture_id)
    if exit_code != 0:
        return f"exit code {exit_code}"
    if step.required_phases:
        return _phases_rejection(step.required_phases, log_text, lecture_id)
    return None


def _script_argv(script_name: str, *arguments: str) -> tuple[str, ...]:
    return (sys.executable, str(SCRIPTS_DIRECTORY / script_name), *arguments)


def _certification_stop_rejection(
    exit_code: int, log_text: str, lecture_id: str
) -> str | None:
    if exit_code not in (0, _CERTIFICATION_REFUSAL_EXIT_CODE):
        return f"exit code {exit_code}"
    try:
        require_stopped_at_certification(parse_phase_log(log_text, lecture_id))
    except PhaseLogError as error:
        return str(error)
    return None


def _phases_rejection(
    phase_numbers: Sequence[int], log_text: str, lecture_id: str
) -> str | None:
    try:
        require_completed(parse_phase_log(log_text, lecture_id), phase_numbers)
    except PhaseLogError as error:
        return str(error)
    return None


def child_environment(adapter: AdapterName) -> dict[str, str]:
    """The current environment with the adapter variable forced to `adapter`."""

    return {**os.environ, ADAPTER_OVERRIDE_ENV: adapter.value}


def preflight_passes(environment: Mapping[str, str], lecture_id: str) -> bool:
    """Build the gateway of every stage; report the outcome on stdout."""

    try:
        preflight_stage_gateways(
            tuple(Stage), lecture_id=lecture_id, environ=environment
        )
    except LlmGatewayError as error:
        print(f"preflight=refused {error}")
        return False
    print("preflight=ok")
    return True


def run_step(
    step: DemoRunStep, *, environment: Mapping[str, str], lecture_id: str
) -> bool:
    """Run one step, keep its log and report whether its outcome is acceptable."""

    completed = run_bounded_process(
        step.argv,
        env=environment,
        timeout_seconds=step.timeout_seconds,
        maximum_stdout_bytes=_MAXIMUM_STDOUT_BYTES,
        maximum_stderr_bytes=_MAXIMUM_STDERR_BYTES,
        check=False,
    )
    log_text = completed.stdout + completed.stderr
    log_path = _write_log(lecture_id, step.log_name, log_text)
    reason = rejection_reason(
        step, exit_code=completed.returncode, log_text=log_text, lecture_id=lecture_id
    )
    if reason is None:
        print(f"step={step.name} status=ok exit={completed.returncode} log={log_path}")
        return True
    print(f"step={step.name} status=failed reason={reason} log={log_path}")
    return False


def _write_log(lecture_id: str, log_name: str, log_text: str) -> Path:
    log_path = DEMO_LOGS_ROOT / lecture_id / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _archive_previous_attempt(log_path)
    log_path.write_text(log_text)
    return log_path


def _archive_previous_attempt(log_path: Path) -> None:
    """Keep the log of an earlier attempt: its wall times are measurements."""

    if not log_path.exists():
        return
    attempt = 1
    while (archived := _attempt_path(log_path, attempt)).exists():
        attempt += 1
    log_path.rename(archived)


def _attempt_path(log_path: Path, attempt: int) -> Path:
    return log_path.with_name(f"{log_path.stem}.attempt-{attempt}{log_path.suffix}")


def run_pipeline(
    steps: Sequence[DemoRunStep], *, environment: Mapping[str, str], lecture_id: str
) -> int:
    """Run the steps in order; stop at the first one that is not acceptable."""

    for step in steps:
        if not run_step(step, environment=environment, lecture_id=lecture_id):
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    adapter = AdapterName(arguments.adapter)
    print(f"adapter={adapter.value} lecture={arguments.lecture_id}")
    if adapter is AdapterName.CODEX and not arguments.confirm_remote_egress:
        print(
            "refused: the codex adapter sends lecture content to a remote provider; "
            "pass --confirm-remote-egress to confirm",
            file=sys.stderr,
        )
        return _REFUSED_EXIT_CODE
    environment = child_environment(adapter)
    if not preflight_passes(environment, arguments.lecture_id):
        return 1
    if arguments.preflight_only:
        return 0
    steps = steps_from(
        pipeline_steps(arguments.lecture_id, arguments.whisper_model_path),
        arguments.from_step,
    )
    return run_pipeline(steps, environment=environment, lecture_id=arguments.lecture_id)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adapter", choices=[member.value for member in AdapterName], required=True
    )
    parser.add_argument("--lecture-id", required=True)
    parser.add_argument(
        "--whisper-model-path", type=Path, default=DEFAULT_WHISPER_MODEL_PATH
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--from-step",
        choices=[step.name for step in pipeline_steps("0", Path("."))],
        default="run_all",
        help="first step to run; earlier steps are skipped and their logs kept",
    )
    parser.add_argument(
        "--confirm-remote-egress",
        action="store_true",
        help="required for the codex adapter, which sends lecture content to a remote provider",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
