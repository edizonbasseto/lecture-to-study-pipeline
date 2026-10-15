#!/usr/bin/env python3
"""Demonstrate the blocked-lecture recovery of ADR 0045 on the demo lecture.

For each scenario (a stale draft, a rejected review) the four derivative
scripts must exit 1 and write nothing, the critic script must then
re-approve the lecture, and the four scripts must succeed and refresh their
outputs (ADR 0045 Consequences, ADR 0047). Every script runs with the local
adapter: ``LECTURE_STUDY_LLM_ADAPTER`` is forced to ``llama-cpp``, so the
demonstration sends nothing to a remote service.

A successful critic rerun replaces the measured critic record, so the run
refuses to start until that record has been harvested. Run it from the
repository root. Logs and the evidence file stay under the gitignored
``workspace/demo/recovery``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import get_args

from lecture_study.demo.recovery import (
    RecoveryEvidence,
    RecoveryScenario,
    RecoveryStep,
    ScenarioEvidence,
    default_layout,
    run_recovery_scenario,
)
from lecture_study.llm.config import ADAPTER_OVERRIDE_ENV
from lecture_study.llm.gateway import AdapterName
from lecture_study.ops.process_lifecycle import run_bounded_process

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
RECOVERY_WORKSPACE = Path("workspace/demo/recovery")
HARVESTED_CRITIC_RECORD = Path("workspace/demo/runs/llama-cpp/llm_runs/critic.json")
DEFAULT_LECTURE_ID = "925100"
DEFAULT_EVIDENCE_PATH = RECOVERY_WORKSPACE / "evidence.json"
ADAPTER = AdapterName.LLAMA_CPP

SCRIPT_NAMES: dict[RecoveryStep, str] = {
    "exam": "generate_codex_mock_exam.py",
    "podcast": "generate_codex_podcast.py",
    "flashcards": "generate_codex_personal_study_flashcards.py",
    "study_guide": "generate_personal_study_guides.py",
    "critic": "generate_codex_critic_review.py",
}
STUDY_GUIDE_ARGUMENTS = (
    "--targets-path",
    "config/targets.example.yaml",
    "--schedule-path",
    "demo/site/academic-calendar.yaml",
)
SCRIPT_TIMEOUT_SECONDS = 3600.0
MAXIMUM_OUTPUT_BYTES = 32 * 1024 * 1024
GUARD_EXIT_CODE = 2

ALL_SCENARIOS: tuple[RecoveryScenario, ...] = get_args(RecoveryScenario)


class ProcessScriptRunner:
    """Runs the real pipeline scripts of one scenario and logs their output."""

    def __init__(self, lecture_id: str, scenario: RecoveryScenario, log_dir: Path):
        self._lecture_id = lecture_id
        self._scenario = scenario
        self._log_dir = log_dir
        self._environment = {**os.environ, ADAPTER_OVERRIDE_ENV: ADAPTER.value}
        self._logged_steps: set[RecoveryStep] = set()

    def __call__(self, step: RecoveryStep) -> int:
        completed = run_bounded_process(
            self._command(step),
            env=self._environment,
            timeout_seconds=SCRIPT_TIMEOUT_SECONDS,
            maximum_stdout_bytes=MAXIMUM_OUTPUT_BYTES,
            maximum_stderr_bytes=MAXIMUM_OUTPUT_BYTES,
            check=False,
        )
        self._append_log(step, completed)
        return completed.returncode

    def _command(self, step: RecoveryStep) -> list[str]:
        command = [
            sys.executable,
            str(SCRIPTS_DIRECTORY / SCRIPT_NAMES[step]),
            self._lecture_id,
        ]
        if step == "study_guide":
            command.extend(STUDY_GUIDE_ARGUMENTS)
        return command

    def _append_log(
        self, step: RecoveryStep, completed: subprocess.CompletedProcess[str]
    ) -> None:
        log_path = self._log_dir / f"{self._scenario}-{step}.log"
        mode = "a" if step in self._logged_steps else "w"
        self._logged_steps.add(step)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open(mode) as log:
            log.write(f"=== exit={completed.returncode} ===\n")
            log.write(completed.stdout)
            log.write(completed.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    if not HARVESTED_CRITIC_RECORD.is_file():
        print(_harvest_guard_message(), file=sys.stderr)
        return GUARD_EXIT_CODE
    evidence = _run_scenarios(arguments.lecture_id, _selected(arguments.scenario))
    _write_evidence(evidence, arguments.evidence_path)
    return 0 if evidence.passed() else 1


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Demonstrate the ADR 0045 blocked-lecture recovery."
    )
    parser.add_argument("--lecture-id", default=DEFAULT_LECTURE_ID)
    parser.add_argument("--scenario", choices=(*ALL_SCENARIOS, "both"), default="both")
    parser.add_argument("--evidence-path", type=Path, default=DEFAULT_EVIDENCE_PATH)
    return parser


def _selected(scenario_choice: str) -> tuple[RecoveryScenario, ...]:
    if scenario_choice == "both":
        return ALL_SCENARIOS
    return tuple(name for name in ALL_SCENARIOS if name == scenario_choice)


def _harvest_guard_message() -> str:
    return (
        f"refusing to start: {HARVESTED_CRITIC_RECORD} is absent. A successful "
        "critic rerun replaces the measured critic record, so the local "
        "measurements must be harvested first."
    )


def _run_scenarios(
    lecture_id: str, scenarios: Sequence[RecoveryScenario]
) -> RecoveryEvidence:
    layout = default_layout(lecture_id)
    results: list[ScenarioEvidence] = []
    for scenario in scenarios:
        runner = ProcessScriptRunner(lecture_id, scenario, RECOVERY_WORKSPACE)
        result = run_recovery_scenario(scenario, layout=layout, runner=runner)
        print(_scenario_line(result))
        results.append(result)
    return RecoveryEvidence(
        lecture_id=lecture_id, adapter=ADAPTER, scenarios=tuple(results)
    )


def _scenario_line(result: ScenarioEvidence) -> str:
    blocked_codes = [outcome.exit_code for outcome in result.blocked]
    recovered_codes = [outcome.exit_code for outcome in result.recovered]
    return (
        f"scenario={result.scenario} passed={result.passed} "
        f"blocked_exit_codes={blocked_codes} recovered_exit_codes={recovered_codes}"
    )


def _write_evidence(evidence: RecoveryEvidence, evidence_path: Path) -> None:
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(evidence.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
