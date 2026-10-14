"""Document Assembly (Phase 8): deterministic aggregation of every Phase 7
ValidatedLearningUnit belonging to a lecture into a single
CanonicalLectureDocument, in strict chronological (pack_sequence) order.

No llama.cpp, no model inference -- this is a pure data transformation.
Three zero-tolerance invariants gate assembly: the lecture's Phase 7 handoff
directory must exist (LectureNotFoundError), its pack_sequence values must
be a contiguous run starting at 1 (MissingSequenceError), and no unit may
carry a Critic-rejected review (UnresolvedReviewError). Any violation aborts
assembly instead of producing an incomplete or clinically unvetted canonical
document.
"""

import json
import re
import time
from pathlib import Path

from pydantic import TypeAdapter

from lecture_study.domain.errors import (
    LectureNotFoundError,
    MissingSequenceError,
    UnresolvedReviewError,
)
from lecture_study.domain.models import (
    CanonicalLectureDocument,
    LearningUnitDraft,
    SafeIdentifier,
    ValidatedLearningUnit,
)

VALIDATED_UNITS_INPUT_DIR = Path("workspace/data/09_validated_units")
CANONICAL_DOCUMENTS_OUTPUT_DIR = Path("workspace/data/10_canonical_documents")

_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)


class DocumentAssembler:
    """Assembles the ValidatedLearningUnits of one lecture, found under
    VALIDATED_UNITS_INPUT_DIR, into its CanonicalLectureDocument."""

    def assemble_lecture(self, lecture_id: str) -> CanonicalLectureDocument:
        # Validated before any Path splice (matches critic/semantic/drafting
        # pipeline.py's _LECTURE_ID_ADAPTER convention): lecture_id reaches
        # _load_units as a plain str, and Path("x") / "../../etc" silently
        # escapes VALIDATED_UNITS_INPUT_DIR unless rejected first.
        lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
        units = self._load_units(lecture_id)
        ordered_units = sorted(units, key=lambda unit: unit.pack_sequence)
        _validate_contiguous_sequence(lecture_id, ordered_units)
        _validate_no_unresolved_review(lecture_id, ordered_units)
        document = CanonicalLectureDocument(
            lecture_id=lecture_id,
            total_units=len(ordered_units),
            units=ordered_units,
            assembled_at=time.time(),
        )
        self._save_document(document)
        return document

    @staticmethod
    def _load_units(lecture_id: SafeIdentifier) -> list[ValidatedLearningUnit]:
        return _read_validated_units(VALIDATED_UNITS_INPUT_DIR, lecture_id)

    @staticmethod
    def _save_document(document: CanonicalLectureDocument) -> None:
        lecture_dir = CANONICAL_DOCUMENTS_OUTPUT_DIR / document.lecture_id
        lecture_dir.mkdir(parents=True, exist_ok=True)
        output_path = lecture_dir / "canonical_document.json"
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False)
        )
        tmp_path.replace(output_path)


def load_critic_approved_drafts(
    validated_units_root: Path,
    drafts_root: Path,
    lecture_id: str,
) -> tuple[LearningUnitDraft, ...]:
    """The critic-approved, post-repair draft of every unit of a lecture, in
    pack_sequence order, for the personal-study derivative scripts (ADR 0045).

    Stricter than assembly: besides a gap or a failed review, the whole
    lecture is blocked unless its Phase 6 drafts and Phase 7 units are the
    same set and every unit is newer than its draft. Sequence numbers come
    from the file names the pipeline writes, never from the pack_sequence a
    model echoed inside a file.
    """
    lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    unit_paths = _paths_by_sequence(validated_units_root / lecture_id, "unit")
    draft_paths = _paths_by_sequence(drafts_root / lecture_id, "draft")
    _validate_units_cover_exactly_the_drafts(lecture_id, unit_paths, draft_paths)
    for sequence, draft_path in draft_paths.items():
        _validate_draft_reviewed_as_written(
            lecture_id, sequence, draft_path, unit_paths[sequence]
        )
    units = [
        _read_unit_filed_under(lecture_id, sequence, unit_paths[sequence])
        for sequence in sorted(unit_paths)
    ]
    _validate_contiguous_sequence(lecture_id, units)
    _validate_no_unresolved_review(lecture_id, units)
    return tuple(unit.content for unit in units)


def _read_validated_units(
    validated_units_root: Path, lecture_id: SafeIdentifier
) -> list[ValidatedLearningUnit]:
    lecture_dir = validated_units_root / lecture_id
    if not lecture_dir.exists():
        raise LectureNotFoundError(
            f"no {lecture_dir} directory for lecture {lecture_id!r}: "
            "Phase 7 has not produced a handoff for this lecture"
        )
    return [
        ValidatedLearningUnit.model_validate_json(unit_path.read_text())
        for unit_path in sorted(lecture_dir.glob("unit_*.json"))
    ]


def _paths_by_sequence(lecture_dir: Path, prefix: str) -> dict[int, Path]:
    if not lecture_dir.is_dir():
        raise LectureNotFoundError(f"no {lecture_dir} directory")
    paths: dict[int, Path] = {}
    for path in lecture_dir.glob(f"{prefix}_*.json"):
        match = re.fullmatch(rf"{prefix}_([0-9]+)\.json", path.name)
        if match is None:
            raise MissingSequenceError(f"{path.name} carries no pack sequence")
        sequence = int(match.group(1))
        if sequence in paths:
            raise MissingSequenceError(
                f"{lecture_dir} holds two {prefix} files for pack_sequence {sequence}"
            )
        paths[sequence] = path
    if not paths:
        raise LectureNotFoundError(f"{lecture_dir} holds no {prefix} file")
    return paths


def _validate_units_cover_exactly_the_drafts(
    lecture_id: str, unit_paths: dict[int, Path], draft_paths: dict[int, Path]
) -> None:
    unreviewed = draft_paths.keys() - unit_paths.keys()
    orphaned = unit_paths.keys() - draft_paths.keys()
    if unreviewed or orphaned:
        raise UnresolvedReviewError(
            f"lecture {lecture_id!r} drafts and critic reviews differ: drafts "
            f"never reviewed {sorted(unreviewed)}, reviews with no current "
            f"draft {sorted(orphaned)}"
        )


def _validate_draft_reviewed_as_written(
    lecture_id: str, sequence: int, draft_path: Path, unit_path: Path
) -> None:
    # The critic picks the evidence pack by the pack_sequence echoed inside
    # the draft, so an echo that disagrees with the file name means the
    # draft was reviewed against another pack.
    echoed = LearningUnitDraft.model_validate_json(draft_path.read_text())
    if echoed.pack_sequence != sequence:
        raise UnresolvedReviewError(
            f"lecture {lecture_id!r} {draft_path.name} claims pack_sequence "
            f"{echoed.pack_sequence}; its review may cover another pack"
        )
    if draft_path.stat().st_mtime_ns > unit_path.stat().st_mtime_ns:
        raise UnresolvedReviewError(
            f"lecture {lecture_id!r} {draft_path.name} changed after its critic review"
        )


def _read_unit_filed_under(
    lecture_id: str, sequence: int, unit_path: Path
) -> ValidatedLearningUnit:
    unit = ValidatedLearningUnit.model_validate_json(unit_path.read_text())
    if (unit.lecture_id, unit.pack_sequence) != (lecture_id, sequence):
        raise UnresolvedReviewError(
            f"{unit_path.name} of lecture {lecture_id!r} belongs to lecture "
            f"{unit.lecture_id!r} pack_sequence {unit.pack_sequence}"
        )
    # A repaired draft is model output whose echoed identity may drift; the
    # unit's identity, anchored by the critic to the evidence pack, wins.
    trusted_content = unit.content.model_copy(
        update={"lecture_id": lecture_id, "pack_sequence": sequence}
    )
    return unit.model_copy(update={"content": trusted_content})


def _validate_contiguous_sequence(
    lecture_id: str, units: list[ValidatedLearningUnit]
) -> None:
    expected = set(range(1, len(units) + 1))
    actual = {unit.pack_sequence for unit in units}
    if len(actual) != len(units):
        raise MissingSequenceError(
            f"lecture {lecture_id!r} has a duplicate pack_sequence: found "
            f"{len(units)} units but only {len(actual)} distinct pack_sequence "
            f"values ({sorted(unit.pack_sequence for unit in units)})"
        )
    if actual != expected:
        raise MissingSequenceError(
            f"lecture {lecture_id!r} has a gap in its pack sequence: found "
            f"{sorted(actual)}, expected a contiguous run 1..{len(units)}"
        )


def _validate_no_unresolved_review(
    lecture_id: str, units: list[ValidatedLearningUnit]
) -> None:
    for unit in units:
        if not unit.review.passed:
            raise UnresolvedReviewError(
                f"lecture {lecture_id!r} pack_sequence {unit.pack_sequence} "
                "has an unresolved Critic review (review.passed is False); "
                "it cannot enter the canonical document"
            )
