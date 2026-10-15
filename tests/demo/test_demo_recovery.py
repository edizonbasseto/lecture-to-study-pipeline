"""Blocked-lecture inductions, checked against the real ADR 0045 gate."""

import importlib.util
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lecture_study.assembly.assembler import load_critic_approved_drafts
from lecture_study.demo.recovery import (
    RecoveryEvidence,
    RecoveryLayout,
    RecoveryScenario,
    RecoveryStep,
    ScenarioEvidence,
    default_layout,
    delete_units_without_draft,
    induce_rejected_review,
    induce_stale_draft,
    run_recovery_scenario,
)
from lecture_study.demo.tree_snapshot import difference, snapshot_tree
from lecture_study.domain.errors import UnresolvedReviewError
from lecture_study.domain.models import (
    CriticReview,
    LearningUnitDraft,
    ValidatedLearningUnit,
)
from lecture_study.llm.gateway import AdapterName

LECTURE_ID = "925100"
_REVIEW_MARGIN_NS = 1_000_000_000


def _draft(pack_sequence: int) -> LearningUnitDraft:
    return LearningUnitDraft(
        lecture_id=LECTURE_ID,
        pack_sequence=pack_sequence,
        title="Fascia profunda",
        body_markdown="## Fascia profunda\n\nA fascia envolve o musculo.",
        clinical_warnings=[],
    )


def _write_reviewed_unit(layout: RecoveryLayout, pack_sequence: int) -> None:
    draft = _draft(pack_sequence)
    draft_path = layout.drafts_dir / f"draft_{pack_sequence:03d}.json"
    unit_path = layout.units_dir / f"unit_{pack_sequence:03d}.json"
    draft_path.write_text(draft.model_dump_json())
    unit = ValidatedLearningUnit(
        lecture_id=LECTURE_ID,
        pack_sequence=pack_sequence,
        content=draft,
        review=CriticReview(passed=True, hallucination_score=0.0, feedback="ok"),
    )
    unit_path.write_text(unit.model_dump_json())
    draft_ns = draft_path.stat().st_mtime_ns
    os.utime(unit_path, ns=(draft_ns + _REVIEW_MARGIN_NS,) * 2)


def _reviewed_lecture(workspace_root: Path, units: int = 1) -> RecoveryLayout:
    layout = default_layout(LECTURE_ID, workspace_root=workspace_root)
    layout.drafts_dir.mkdir(parents=True)
    layout.units_dir.mkdir(parents=True)
    for pack_sequence in range(1, units + 1):
        _write_reviewed_unit(layout, pack_sequence)
    return layout


def _load_with_real_gate(layout: RecoveryLayout) -> tuple[LearningUnitDraft, ...]:
    return load_critic_approved_drafts(
        layout.units_dir.parent, layout.drafts_dir.parent, LECTURE_ID
    )


def test_reviewed_lecture_passes_the_real_gate(tmp_path: Path) -> None:
    layout = _reviewed_lecture(tmp_path)

    assert len(_load_with_real_gate(layout)) == 1


def test_stale_draft_blocks_the_real_gate(tmp_path: Path) -> None:
    layout = _reviewed_lecture(tmp_path)

    induce_stale_draft(layout)

    with pytest.raises(UnresolvedReviewError, match="changed after its critic review"):
        _load_with_real_gate(layout)


def test_rejected_review_blocks_the_real_gate(tmp_path: Path) -> None:
    layout = _reviewed_lecture(tmp_path)

    induce_rejected_review(layout)

    with pytest.raises(UnresolvedReviewError, match="unresolved Critic review"):
        _load_with_real_gate(layout)


def test_rejected_review_leaves_the_unit_newer_than_its_draft(tmp_path: Path) -> None:
    layout = _reviewed_lecture(tmp_path)

    induce_rejected_review(layout)

    draft_ns = (layout.drafts_dir / "draft_001.json").stat().st_mtime_ns
    unit_ns = (layout.units_dir / "unit_001.json").stat().st_mtime_ns
    assert unit_ns > draft_ns


def test_blocked_gate_call_changes_nothing_on_disk(tmp_path: Path) -> None:
    layout = _reviewed_lecture(tmp_path)
    induce_stale_draft(layout)
    roots = {
        **layout.snapshot_roots(),
        "drafts": layout.drafts_dir,
        "units": layout.units_dir,
    }
    before = snapshot_tree(roots)

    with pytest.raises(UnresolvedReviewError):
        _load_with_real_gate(layout)

    assert difference(before, snapshot_tree(roots)).is_empty()


def test_delete_units_without_draft_keeps_the_others(tmp_path: Path) -> None:
    layout = _reviewed_lecture(tmp_path, units=2)
    (layout.drafts_dir / "draft_002.json").unlink()

    deleted = delete_units_without_draft(layout)

    assert deleted == ("unit_002.json",)
    assert (layout.units_dir / "unit_001.json").exists()
    assert not (layout.units_dir / "unit_002.json").exists()


def test_delete_units_without_draft_deletes_nothing_when_all_drafts_exist(
    tmp_path: Path,
) -> None:
    layout = _reviewed_lecture(tmp_path, units=2)

    assert delete_units_without_draft(layout) == ()


OUTPUT_LABELS = ("exam", "podcast", "flashcards", "study_guide")
SCENARIOS: tuple[RecoveryScenario, ...] = ("stale", "rejected")


@dataclass
class FakePipeline:
    """Stands in for the five scripts, applying the real gate to fixtures."""

    layout: RecoveryLayout
    blocked_exit_code: int = 1
    blocked_step_writes: bool = False
    critic_exit_code: int = 0
    silent_step: RecoveryStep | None = None
    calls: list[RecoveryStep] = field(default_factory=list)

    def __call__(self, step: RecoveryStep) -> int:
        self.calls.append(step)
        if step == "critic":
            return self._critic()
        return self._derivative(step)

    def _derivative(self, step: RecoveryStep) -> int:
        try:
            _load_with_real_gate(self.layout)
        except UnresolvedReviewError:
            if self.blocked_step_writes:
                self._write_output(step)
            return self.blocked_exit_code
        if step != self.silent_step:
            self._write_output(step)
        return 0

    def _write_output(self, step: RecoveryStep) -> None:
        root = self.layout.output_roots[step]
        root.mkdir(parents=True, exist_ok=True)
        (root / "output.json").write_text(f"call {len(self.calls)}")

    def _critic(self) -> int:
        if self.critic_exit_code == 0:
            self._approve_every_unit()
        return self.critic_exit_code

    def _approve_every_unit(self) -> None:
        for unit_path in sorted(self.layout.units_dir.glob("unit_*.json")):
            unit = ValidatedLearningUnit.model_validate_json(unit_path.read_text())
            approved = unit.review.model_copy(update={"passed": True})
            unit_path.write_text(
                unit.model_copy(update={"review": approved}).model_dump_json()
            )
            draft_path = self.layout.drafts_dir / unit_path.name.replace(
                "unit_", "draft_"
            )
            reviewed_ns = draft_path.stat().st_mtime_ns + _REVIEW_MARGIN_NS
            os.utime(unit_path, ns=(reviewed_ns, reviewed_ns))


def _run(
    tmp_path: Path, scenario: RecoveryScenario, **pipeline_options: Any
) -> ScenarioEvidence:
    layout = _reviewed_lecture(tmp_path)
    pipeline = FakePipeline(layout, **pipeline_options)
    return run_recovery_scenario(scenario, layout=layout, runner=pipeline)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_passes_when_blocked_scripts_write_nothing_and_recovery_works(
    tmp_path: Path, scenario: RecoveryScenario
) -> None:
    evidence = _run(tmp_path, scenario)

    assert evidence.passed
    assert evidence.scenario == scenario
    assert [outcome.exit_code for outcome in evidence.blocked] == [1, 1, 1, 1]
    assert evidence.blocked_tree_unchanged
    assert evidence.units_deleted == ()
    assert evidence.critic_exit_code == 0
    assert [outcome.exit_code for outcome in evidence.recovered] == [0, 0, 0, 0]
    assert evidence.outputs_refreshed == OUTPUT_LABELS


def test_scenario_fails_when_a_blocked_step_writes_a_file(tmp_path: Path) -> None:
    evidence = _run(tmp_path, "stale", blocked_step_writes=True)

    assert not evidence.passed
    assert not evidence.blocked_tree_unchanged
    assert any(path.startswith("exam:") for path in evidence.blocked_changes)


def test_scenario_fails_when_a_blocked_step_exits_zero(tmp_path: Path) -> None:
    evidence = _run(tmp_path, "stale", blocked_exit_code=0)

    assert not evidence.passed


def test_scenario_fails_when_the_critic_exits_non_zero(tmp_path: Path) -> None:
    evidence = _run(tmp_path, "rejected", critic_exit_code=1)

    assert not evidence.passed
    assert evidence.critic_exit_code == 1


def test_scenario_fails_when_a_recovered_step_leaves_its_output_unchanged(
    tmp_path: Path,
) -> None:
    evidence = _run(tmp_path, "stale", silent_step="podcast")

    assert not evidence.passed
    assert "podcast" not in evidence.outputs_refreshed


def _evidence_of(*results: ScenarioEvidence) -> RecoveryEvidence:
    return RecoveryEvidence(
        lecture_id=LECTURE_ID, adapter=AdapterName.LLAMA_CPP, scenarios=results
    )


def test_recovery_evidence_round_trips_through_json(tmp_path: Path) -> None:
    evidence = _evidence_of(_run(tmp_path, "stale"))

    restored = RecoveryEvidence.model_validate_json(evidence.model_dump_json())

    assert restored == evidence
    assert restored.passed()


def test_recovery_evidence_fails_when_any_scenario_failed(tmp_path: Path) -> None:
    passing = _run(tmp_path / "first", "stale")
    failing = _run(tmp_path / "second", "rejected", critic_exit_code=1)

    assert not _evidence_of(passing, failing).passed()
    assert not _evidence_of().passed()


def _load_driver() -> ModuleType:
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / ("demo_blocked_recovery.py")
    )
    spec = importlib.util.spec_from_file_location("demo_blocked_recovery", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_driver_refuses_to_start_before_the_critic_record_is_harvested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    driver = _load_driver()
    monkeypatch.setattr(
        driver,
        "ProcessScriptRunner",
        _forbidden_runner,
    )

    exit_code = driver.main([])

    assert exit_code == 2
    assert "critic.json" in capsys.readouterr().err
    assert not (tmp_path / "workspace/demo/recovery").exists()


def _forbidden_runner(*_arguments: object) -> Callable[[RecoveryStep], int]:
    raise AssertionError("no script may run before the harvest")


def test_driver_runs_both_scenarios_and_writes_the_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    layout = _reviewed_lecture(tmp_path)
    harvested = tmp_path / "workspace/demo/runs/llama-cpp/llm_runs/critic.json"
    harvested.parent.mkdir(parents=True)
    harvested.write_text("{}")
    driver = _load_driver()
    monkeypatch.setattr(
        driver, "ProcessScriptRunner", lambda *_arguments: FakePipeline(layout)
    )

    exit_code = driver.main([])

    evidence = RecoveryEvidence.model_validate_json(
        (tmp_path / "workspace/demo/recovery/evidence.json").read_text()
    )
    assert exit_code == 0
    assert evidence.passed()
    assert [scenario.scenario for scenario in evidence.scenarios] == list(SCENARIOS)
    assert capsys.readouterr().out.count("passed=True") == 2


def test_deck_floor_reaches_only_the_flashcards_script() -> None:
    driver = _load_driver()

    flashcards = driver.script_command("flashcards", "925100", 5)
    exam = driver.script_command("exam", "925100", 5)

    assert flashcards[-2:] == ["--minimum-total-cards", "5"]
    assert "--minimum-total-cards" not in exam


def test_flashcards_script_keeps_its_default_floor_when_none_is_given() -> None:
    driver = _load_driver()

    assert "--minimum-total-cards" not in driver.script_command(
        "flashcards", "925100", None
    )
