"""Blocked-lecture inductions, checked against the real ADR 0045 gate."""

import os
from pathlib import Path

import pytest

from lecture_study.assembly.assembler import load_critic_approved_drafts
from lecture_study.demo.recovery import (
    RecoveryLayout,
    default_layout,
    delete_units_without_draft,
    induce_rejected_review,
    induce_stale_draft,
)
from lecture_study.demo.tree_snapshot import difference, snapshot_tree
from lecture_study.domain.errors import UnresolvedReviewError
from lecture_study.domain.models import (
    CriticReview,
    LearningUnitDraft,
    ValidatedLearningUnit,
)

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
