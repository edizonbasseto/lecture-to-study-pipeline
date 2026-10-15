"""Blocked-lecture recovery of ADR 0045, reproduced on demand (ADR 0047).

The critic gate blocks a lecture's derivative scripts when a unit is rejected
or a draft changed after its review. This module induces both states with
documented, deterministic edits, and deletes the units that no longer have a
draft as the ADR's recovery procedure prescribes. The gate code itself is
never touched.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from lecture_study.demo.tree_snapshot import (
    TreeDifference,
    TreeSnapshot,
    difference,
    snapshot_tree,
)
from lecture_study.domain.models import ValidatedLearningUnit
from lecture_study.llm.gateway import AdapterName

__all__ = [
    "RecoveryEvidence",
    "RecoveryLayout",
    "RecoveryScenario",
    "RecoveryStep",
    "ScenarioEvidence",
    "ScriptOutcome",
    "ScriptRunner",
    "default_layout",
    "delete_units_without_draft",
    "induce_rejected_review",
    "induce_stale_draft",
    "run_recovery_scenario",
]

RecoveryStep = Literal["exam", "podcast", "flashcards", "study_guide", "critic"]
RecoveryScenario = Literal["stale", "rejected"]

_DERIVATIVE_STEPS: tuple[RecoveryStep, ...] = (
    "exam",
    "podcast",
    "flashcards",
    "study_guide",
)
_BLOCKED_EXIT_CODE = 1
_SUCCESS_EXIT_CODE = 0
_KEY_LABEL_SEPARATOR = ":"

_WORKSPACE_DATA_ROOT = Path("workspace/data")
_STUDY_GUIDES_ROOT = Path("workspace/study-guides")
_STALE_MARGIN_NS = 2_000_000_000

_DRAFT_PATTERN = "draft_{sequence:03d}.json"
_UNIT_PATTERN = "unit_{sequence:03d}.json"
_UNIT_GLOB = "unit_*.json"
_UNIT_PREFIX = "unit_"
_DRAFT_PREFIX = "draft_"


@dataclass(frozen=True)
class RecoveryLayout:
    """Where one lecture's drafts, units and derivative outputs live."""

    lecture_id: str
    drafts_dir: Path
    units_dir: Path
    output_roots: Mapping[str, Path]
    extra_snapshot_roots: Mapping[str, Path]

    def snapshot_roots(self) -> dict[str, Path]:
        return {**self.output_roots, **self.extra_snapshot_roots}


def default_layout(
    lecture_id: str, *, workspace_root: Path = Path(".")
) -> RecoveryLayout:
    """The workspace paths the pipeline scripts use for one lecture."""
    data_root = workspace_root / _WORKSPACE_DATA_ROOT
    return RecoveryLayout(
        lecture_id=lecture_id,
        drafts_dir=data_root / "08_drafts" / lecture_id,
        units_dir=data_root / "09_validated_units" / lecture_id,
        output_roots={
            "exam": data_root / "14_mock_exams" / lecture_id,
            "podcast": data_root / "15_podcast_scripts" / lecture_id,
            "flashcards": data_root / "18_personal_study_flashcards" / lecture_id,
            "study_guide": workspace_root / _STUDY_GUIDES_ROOT,
        },
        extra_snapshot_roots={
            "podcast_audio": data_root / "16_podcast_audio" / lecture_id,
            "llm_runs": data_root / "llm_runs" / lecture_id,
        },
    )


def induce_stale_draft(layout: RecoveryLayout, pack_sequence: int = 1) -> None:
    """Make a draft newer than its unit, as if it were redrafted after review."""
    draft_path = layout.drafts_dir / _DRAFT_PATTERN.format(sequence=pack_sequence)
    unit_path = layout.units_dir / _UNIT_PATTERN.format(sequence=pack_sequence)
    newer_ns = unit_path.stat().st_mtime_ns + _STALE_MARGIN_NS
    os.utime(draft_path, ns=(newer_ns, newer_ns))


def induce_rejected_review(layout: RecoveryLayout, pack_sequence: int = 1) -> None:
    """Rewrite a unit's review as rejected; the unit stays newer than its draft."""
    unit_path = layout.units_dir / _UNIT_PATTERN.format(sequence=pack_sequence)
    unit = ValidatedLearningUnit.model_validate_json(unit_path.read_text())
    rejected_review = unit.review.model_copy(update={"passed": False})
    rejected_unit = unit.model_copy(update={"review": rejected_review})
    _write_atomically(unit_path, rejected_unit.model_dump_json(indent=2))


def delete_units_without_draft(layout: RecoveryLayout) -> tuple[str, ...]:
    """Delete the units whose draft is gone (ADR 0045 Consequences).

    Returns the deleted file names in order.
    """
    orphaned = [
        unit_path
        for unit_path in sorted(layout.units_dir.glob(_UNIT_GLOB))
        if not _draft_path_of(layout, unit_path).exists()
    ]
    for unit_path in orphaned:
        unit_path.unlink()
    return tuple(unit_path.name for unit_path in orphaned)


def _draft_path_of(layout: RecoveryLayout, unit_path: Path) -> Path:
    draft_name = _DRAFT_PREFIX + unit_path.name.removeprefix(_UNIT_PREFIX)
    return layout.drafts_dir / draft_name


def _write_atomically(path: Path, text: str) -> None:
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(text)
    temporary_path.replace(path)


class ScriptRunner(Protocol):
    """Runs one pipeline script for the lecture and returns its exit code."""

    def __call__(self, step: RecoveryStep) -> int: ...


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ScriptOutcome(_FrozenStrictModel):
    """The exit code of one script run."""

    step: RecoveryStep
    exit_code: int


class ScenarioEvidence(_FrozenStrictModel):
    """What one induced blocked state, and its recovery, demonstrated."""

    scenario: RecoveryScenario
    blocked: tuple[ScriptOutcome, ...]
    blocked_tree_unchanged: bool
    blocked_changes: tuple[str, ...]
    units_deleted: tuple[str, ...]
    critic_exit_code: int
    recovered: tuple[ScriptOutcome, ...]
    outputs_refreshed: tuple[str, ...]
    passed: bool


class RecoveryEvidence(_FrozenStrictModel):
    """The recovery demonstration for one lecture and adapter."""

    lecture_id: str
    adapter: AdapterName
    scenarios: tuple[ScenarioEvidence, ...]

    def passed(self) -> bool:
        return bool(self.scenarios) and all(
            scenario.passed for scenario in self.scenarios
        )


_INDUCTIONS: Mapping[RecoveryScenario, Callable[[RecoveryLayout], None]] = {
    "stale": induce_stale_draft,
    "rejected": induce_rejected_review,
}


def run_recovery_scenario(
    scenario: RecoveryScenario, *, layout: RecoveryLayout, runner: ScriptRunner
) -> ScenarioEvidence:
    """Induce a blocked state, prove the scripts write nothing, then recover."""
    _INDUCTIONS[scenario](layout)
    snapshot_before_blocked = _snapshot(layout)
    blocked = _run_steps(_DERIVATIVE_STEPS, runner)
    blocked_changes = _changes_since(snapshot_before_blocked, layout).changed()
    units_deleted = delete_units_without_draft(layout)
    critic_exit_code = runner("critic")
    snapshot_before_recovered = _snapshot(layout)
    recovered = _run_steps(_DERIVATIVE_STEPS, runner)
    outputs_refreshed = _refreshed_outputs(
        layout, _changes_since(snapshot_before_recovered, layout)
    )
    unjudged = ScenarioEvidence(
        scenario=scenario,
        blocked=blocked,
        blocked_tree_unchanged=not blocked_changes,
        blocked_changes=blocked_changes,
        units_deleted=units_deleted,
        critic_exit_code=critic_exit_code,
        recovered=recovered,
        outputs_refreshed=outputs_refreshed,
        passed=False,
    )
    verdict = _follows_adr_0045(unjudged, expected_outputs=tuple(layout.output_roots))
    return unjudged.model_copy(update={"passed": verdict})


def _snapshot(layout: RecoveryLayout) -> TreeSnapshot:
    return snapshot_tree(layout.snapshot_roots())


def _changes_since(before: TreeSnapshot, layout: RecoveryLayout) -> TreeDifference:
    return difference(before, _snapshot(layout))


def _run_steps(
    steps: Sequence[RecoveryStep], runner: ScriptRunner
) -> tuple[ScriptOutcome, ...]:
    return tuple(ScriptOutcome(step=step, exit_code=runner(step)) for step in steps)


def _refreshed_outputs(
    layout: RecoveryLayout, changes: TreeDifference
) -> tuple[str, ...]:
    labels_with_new_content = {
        key.split(_KEY_LABEL_SEPARATOR, 1)[0]
        for key in (*changes.created, *changes.modified)
    }
    return tuple(
        label for label in layout.output_roots if label in labels_with_new_content
    )


def _follows_adr_0045(
    evidence: ScenarioEvidence, *, expected_outputs: tuple[str, ...]
) -> bool:
    return (
        _all_exit_with(evidence.blocked, _BLOCKED_EXIT_CODE)
        and evidence.blocked_tree_unchanged
        and evidence.critic_exit_code == _SUCCESS_EXIT_CODE
        and _all_exit_with(evidence.recovered, _SUCCESS_EXIT_CODE)
        and set(evidence.outputs_refreshed) == set(expected_outputs)
    )


def _all_exit_with(outcomes: tuple[ScriptOutcome, ...], exit_code: int) -> bool:
    return all(outcome.exit_code == exit_code for outcome in outcomes)
