"""Blocked-lecture recovery of ADR 0045, reproduced on demand (ADR 0047).

The critic gate blocks a lecture's derivative scripts when a unit is rejected
or a draft changed after its review. This module induces both states with
documented, deterministic edits, and deletes the units that no longer have a
draft as the ADR's recovery procedure prescribes. The gate code itself is
never touched.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from lecture_study.domain.models import ValidatedLearningUnit

__all__ = [
    "RecoveryLayout",
    "default_layout",
    "delete_units_without_draft",
    "induce_rejected_review",
    "induce_stale_draft",
]

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
