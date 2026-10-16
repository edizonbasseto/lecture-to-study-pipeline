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

from lecture_study.demo.lecture_id import parse_lecture_id
from lecture_study.demo.recovery import (
    RecoveryEvidence,
    RecoveryScenario,
    RecoveryStep,
    ScenarioEvidence,
    default_layout,
    run_recovery_scenario,
)
from lecture_study.llm.config import ADAPTER_OVERRIDE_ENV
from lecture_study.demo.site_access import require_inside
from lecture_study.llm.gateway import AdapterName
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    run_bounded_process,
)

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


class ScriptAborted(RuntimeError):
    """A pipeline script timed out, overflowed its output or could not start."""


_SCRIPT_ABORTS = (subprocess.TimeoutExpired, ProcessOutputLimitExceeded, OSError)


def script_command(
    step: RecoveryStep, lecture_id: str, flashcards_minimum_total_cards: int | None
) -> list[str]:
    """The argv of one pipeline script; the deck floor only reaches flashcards."""
    command = [sys.executable, str(SCRIPTS_DIRECTORY / SCRIPT_NAMES[step]), lecture_id]
    if step == "study_guide":
        command.extend(STUDY_GUIDE_ARGUMENTS)
    if step == "flashcards" and flashcards_minimum_total_cards is not None:
        command.extend(["--minimum-total-cards", str(flashcards_minimum_total_cards)])
    return command


class ProcessScriptRunner:
    """Runs the real pipeline scripts of one scenario and logs their output."""

    def __init__(
        self,
        lecture_id: str,
        scenario: RecoveryScenario,
        log_dir: Path,
        flashcards_minimum_total_cards: int | None = None,
    ):
        self._lecture_id = lecture_id
        self._scenario = scenario
        self._log_dir = log_dir
        self._flashcards_minimum_total_cards = flashcards_minimum_total_cards
        self._environment = {**os.environ, ADAPTER_OVERRIDE_ENV: ADAPTER.value}
        self._logged_steps: set[RecoveryStep] = set()

    def __call__(self, step: RecoveryStep) -> int:
        try:
            completed = run_bounded_process(
                self._command(step),
                env=self._environment,
                timeout_seconds=SCRIPT_TIMEOUT_SECONDS,
                maximum_stdout_bytes=MAXIMUM_OUTPUT_BYTES,
                maximum_stderr_bytes=MAXIMUM_OUTPUT_BYTES,
                check=False,
            )
        except _SCRIPT_ABORTS as error:
            self._append_text_log(step, f"=== aborted: {error!r} ===\n")
            raise ScriptAborted(
                f"{self._scenario}/{step}: {type(error).__name__}"
            ) from error
        self._append_log(step, completed)
        return completed.returncode

    def _command(self, step: RecoveryStep) -> list[str]:
        return script_command(
            step, self._lecture_id, self._flashcards_minimum_total_cards
        )

    def _append_log(
        self, step: RecoveryStep, completed: subprocess.CompletedProcess[str]
    ) -> None:
        self._append_text_log(
            step,
            f"=== exit={completed.returncode} ===\n"
            f"{completed.stdout}{completed.stderr}",
        )

    def _append_text_log(self, step: RecoveryStep, text: str) -> None:
        log_path = self._log_dir / f"{self._scenario}-{step}.log"
        mode = "a" if step in self._logged_steps else "w"
        self._logged_steps.add(step)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open(mode) as log:
            log.write(text)


def main(argv: Sequence[str] | None = None) -> int:
    os.chdir(REPOSITORY_ROOT)
    arguments = _argument_parser().parse_args(argv)
    if not HARVESTED_CRITIC_RECORD.is_file():
        print(_harvest_guard_message(), file=sys.stderr)
        return GUARD_EXIT_CODE
    try:
        evidence_path = require_inside(
            arguments.evidence_path, REPOSITORY_ROOT / "workspace"
        )
    except ValueError as error:
        print(f"refusing to start: {error}", file=sys.stderr)
        return GUARD_EXIT_CODE
    evidence, completed = _run_scenarios(
        arguments.lecture_id,
        _selected(arguments.scenario),
        arguments.flashcards_minimum_total_cards,
    )
    _write_evidence(
        evidence, evidence_path if completed else _partial_path(evidence_path)
    )
    return 0 if completed and evidence.passed() else 1


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Demonstrate the ADR 0045 blocked-lecture recovery."
    )
    parser.add_argument(
        "--lecture-id", type=parse_lecture_id, default=DEFAULT_LECTURE_ID
    )
    parser.add_argument("--scenario", choices=(*ALL_SCENARIOS, "both"), default="both")
    parser.add_argument("--evidence-path", type=Path, default=DEFAULT_EVIDENCE_PATH)
    parser.add_argument(
        "--flashcards-minimum-total-cards",
        type=int,
        default=None,
        help="deck floor of the flashcards script; omit to keep the script default",
    )
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
    lecture_id: str,
    scenarios: Sequence[RecoveryScenario],
    flashcards_minimum_total_cards: int | None,
) -> tuple[RecoveryEvidence, bool]:
    """The evidence of the scenarios that finished and whether all of them did."""
    layout = default_layout(lecture_id)
    results: list[ScenarioEvidence] = []
    completed = True
    for scenario in scenarios:
        runner = ProcessScriptRunner(
            lecture_id, scenario, RECOVERY_WORKSPACE, flashcards_minimum_total_cards
        )
        try:
            result = run_recovery_scenario(scenario, layout=layout, runner=runner)
        except (ScriptAborted, OSError) as error:
            print(_aborted_warning(scenario, error), file=sys.stderr)
            completed = False
            break
        print(_scenario_line(result))
        results.append(result)
    evidence = RecoveryEvidence(
        lecture_id=lecture_id, adapter=ADAPTER, scenarios=tuple(results)
    )
    return evidence, completed


def _aborted_warning(scenario: RecoveryScenario, error: Exception) -> str:
    return (
        f"warning: scenario {scenario} aborted ({error}); lecture may still be in "
        f"the {scenario} induced state and needs a critic rerun before further use. "
        "Partial evidence is written."
    )


def _scenario_line(result: ScenarioEvidence) -> str:
    blocked_codes = [outcome.exit_code for outcome in result.blocked]
    recovered_codes = [outcome.exit_code for outcome in result.recovered]
    return (
        f"scenario={result.scenario} passed={result.passed} "
        f"blocked_exit_codes={blocked_codes} recovered_exit_codes={recovered_codes}"
    )


def _partial_path(evidence_path: Path) -> Path:
    return evidence_path.with_name(evidence_path.stem + ".partial.json")


def _write_evidence(evidence: RecoveryEvidence, evidence_path: Path) -> None:
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(evidence.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
