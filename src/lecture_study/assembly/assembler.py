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
        lecture_dir = VALIDATED_UNITS_INPUT_DIR / lecture_id
        if not lecture_dir.exists():
            raise LectureNotFoundError(
                f"no {lecture_dir} directory for lecture {lecture_id!r}: "
                "Phase 7 has not produced a handoff for this lecture"
            )
        return [
            ValidatedLearningUnit.model_validate_json(unit_path.read_text())
            for unit_path in sorted(lecture_dir.glob("unit_*.json"))
        ]

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
