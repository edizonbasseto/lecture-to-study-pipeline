"""Document Assembly (Phase 8) tests: deterministic aggregation of
ValidatedLearningUnits into a CanonicalLectureDocument, in strict
pack_sequence order. No LLM/subprocess involved -- pure data
transformation."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.assembly import assembler as assembler_module
from lecture_study.assembly.assembler import (
    DocumentAssembler,
    load_critic_approved_drafts,
)
from lecture_study.domain.errors import (
    LectureNotFoundError,
    MissingSequenceError,
    UnresolvedReviewError,
)
from lecture_study.domain.models import (
    CanonicalLectureDocument,
    CriticReview,
    LearningUnitDraft,
    ValidatedLearningUnit,
)


def _setup_assembler_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        assembler_module, "VALIDATED_UNITS_INPUT_DIR", tmp_path / "09_validated_units"
    )
    monkeypatch.setattr(
        assembler_module,
        "CANONICAL_DOCUMENTS_OUTPUT_DIR",
        tmp_path / "10_canonical_documents",
    )


def _make_validated_unit(
    lecture_id: str = "aula-001", pack_sequence: int = 1, review_passed: bool = True
) -> ValidatedLearningUnit:
    return ValidatedLearningUnit(
        lecture_id=lecture_id,
        pack_sequence=pack_sequence,
        content=LearningUnitDraft(
            lecture_id=lecture_id,
            pack_sequence=pack_sequence,
            title="Bradicardia sinusal",
            body_markdown="## Bradicardia sinusal\n\nFC 40bpm ao exame.",
            clinical_warnings=[],
        ),
        review=CriticReview(
            passed=review_passed,
            hallucination_score=0.0,
            feedback="ok" if review_passed else "unsupported claim vs ledger",
        ),
    )


def _write_unit(units_dir: Path, unit: ValidatedLearningUnit) -> None:
    lecture_dir = units_dir / unit.lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / f"unit_{unit.pack_sequence:03d}.json").write_text(
        json.dumps(unit.model_dump(mode="json"))
    )


def test_assemble_lecture_orders_units_strictly_by_pack_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)
    units_dir = tmp_path / "09_validated_units"
    # Written out of order on disk to prove the assembler re-sorts, not
    # merely trusts glob() iteration order.
    _write_unit(units_dir, _make_validated_unit(pack_sequence=2))
    _write_unit(units_dir, _make_validated_unit(pack_sequence=1))
    _write_unit(units_dir, _make_validated_unit(pack_sequence=3))

    document = DocumentAssembler().assemble_lecture("aula-001")

    assert [unit.pack_sequence for unit in document.units] == [1, 2, 3]
    assert document.lecture_id == "aula-001"
    assert document.total_units == 3
    assert document.assembled_at > 0


def test_assemble_lecture_persists_canonical_document_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)
    units_dir = tmp_path / "09_validated_units"
    _write_unit(units_dir, _make_validated_unit(pack_sequence=1))

    document = DocumentAssembler().assemble_lecture("aula-001")

    output_path = (
        tmp_path / "10_canonical_documents" / "aula-001" / "canonical_document.json"
    )
    assert output_path.exists()
    persisted = CanonicalLectureDocument.model_validate_json(output_path.read_text())
    assert persisted == document


def test_assemble_lecture_raises_missing_sequence_error_on_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)
    units_dir = tmp_path / "09_validated_units"
    _write_unit(units_dir, _make_validated_unit(pack_sequence=1))
    _write_unit(units_dir, _make_validated_unit(pack_sequence=3))

    with pytest.raises(MissingSequenceError, match="aula-001"):
        DocumentAssembler().assemble_lecture("aula-001")


def test_assemble_lecture_does_not_write_document_when_sequence_has_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)
    units_dir = tmp_path / "09_validated_units"
    _write_unit(units_dir, _make_validated_unit(pack_sequence=1))
    _write_unit(units_dir, _make_validated_unit(pack_sequence=3))

    with pytest.raises(MissingSequenceError):
        DocumentAssembler().assemble_lecture("aula-001")

    output_path = (
        tmp_path / "10_canonical_documents" / "aula-001" / "canonical_document.json"
    )
    assert not output_path.exists()


def test_assemble_lecture_raises_when_sequence_does_not_start_at_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)
    units_dir = tmp_path / "09_validated_units"
    _write_unit(units_dir, _make_validated_unit(pack_sequence=2))
    _write_unit(units_dir, _make_validated_unit(pack_sequence=3))

    with pytest.raises(MissingSequenceError):
        DocumentAssembler().assemble_lecture("aula-001")


def test_assemble_lecture_handles_empty_lecture_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)
    units_dir = tmp_path / "09_validated_units"
    (units_dir / "aula-001").mkdir(parents=True)

    document = DocumentAssembler().assemble_lecture("aula-001")

    assert document.units == []
    assert document.total_units == 0
    output_path = (
        tmp_path / "10_canonical_documents" / "aula-001" / "canonical_document.json"
    )
    assert output_path.exists()


def test_assemble_lecture_raises_lecture_not_found_when_directory_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)

    with pytest.raises(LectureNotFoundError, match="aula-001"):
        DocumentAssembler().assemble_lecture("aula-001")

    output_path = (
        tmp_path / "10_canonical_documents" / "aula-001" / "canonical_document.json"
    )
    assert not output_path.exists()


def test_assemble_lecture_raises_unresolved_review_error_when_a_unit_failed_critic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)
    units_dir = tmp_path / "09_validated_units"
    _write_unit(units_dir, _make_validated_unit(pack_sequence=1, review_passed=True))
    _write_unit(units_dir, _make_validated_unit(pack_sequence=2, review_passed=False))

    with pytest.raises(UnresolvedReviewError, match="pack_sequence 2"):
        DocumentAssembler().assemble_lecture("aula-001")

    output_path = (
        tmp_path / "10_canonical_documents" / "aula-001" / "canonical_document.json"
    )
    assert not output_path.exists()


def test_assemble_lecture_raises_missing_sequence_error_on_duplicate_pack_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)
    units_dir = tmp_path / "09_validated_units"
    lecture_dir = units_dir / "aula-001"
    lecture_dir.mkdir(parents=True)
    # Two distinct files both carrying pack_sequence=1, so the assembler
    # cannot rely on glob() de-duplicating -- it must detect this from the
    # ValidatedLearningUnit content itself, not from the filesystem listing.
    (lecture_dir / "unit_001.json").write_text(
        json.dumps(_make_validated_unit(pack_sequence=1).model_dump(mode="json"))
    )
    (lecture_dir / "unit_001b.json").write_text(
        json.dumps(_make_validated_unit(pack_sequence=1).model_dump(mode="json"))
    )
    (lecture_dir / "unit_003.json").write_text(
        json.dumps(_make_validated_unit(pack_sequence=3).model_dump(mode="json"))
    )

    with pytest.raises(MissingSequenceError, match="duplicate pack_sequence"):
        DocumentAssembler().assemble_lecture("aula-001")


def test_assemble_lecture_rejects_malformed_lecture_id_before_touching_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_assembler_dirs(monkeypatch, tmp_path)

    with pytest.raises(ValidationError):
        DocumentAssembler().assemble_lecture("../../etc")

    # Only the assembler's two configured roots may exist under tmp_path;
    # a path-traversal lecture_id must never reach Path.glob/.exists.
    assert list(tmp_path.iterdir()) == []


def _write_draft(drafts_dir: Path, draft: LearningUnitDraft) -> Path:
    lecture_dir = drafts_dir / draft.lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    path = lecture_dir / f"draft_{draft.pack_sequence:03d}.json"
    path.write_text(draft.model_dump_json())
    return path


def _write_reviewed_lecture(tmp_path: Path, *units: ValidatedLearningUnit) -> None:
    for unit in units:
        _write_draft(tmp_path / "08_drafts", unit.content)
        _write_unit(tmp_path / "09_validated_units", unit)


def _load_approved(tmp_path: Path) -> tuple[LearningUnitDraft, ...]:
    return load_critic_approved_drafts(
        tmp_path / "09_validated_units", tmp_path / "08_drafts", "aula-001"
    )


def _with_content(
    unit: ValidatedLearningUnit, **changes: object
) -> ValidatedLearningUnit:
    return unit.model_copy(update={"content": unit.content.model_copy(update=changes)})


def test_approved_drafts_are_the_post_repair_content_in_sequence_order(
    tmp_path: Path,
):
    first = _make_validated_unit(pack_sequence=1)
    _write_reviewed_lecture(tmp_path, _make_validated_unit(pack_sequence=2), first)
    _write_unit(
        tmp_path / "09_validated_units",
        _with_content(first, body_markdown="## Versao reparada"),
    )

    drafts = _load_approved(tmp_path)

    assert [draft.pack_sequence for draft in drafts] == [1, 2]
    assert drafts[0].body_markdown == "## Versao reparada"


def test_approved_drafts_block_the_whole_lecture_when_one_unit_failed_critic(
    tmp_path: Path,
):
    _write_reviewed_lecture(
        tmp_path,
        _make_validated_unit(pack_sequence=1),
        _make_validated_unit(pack_sequence=2, review_passed=False),
    )

    with pytest.raises(UnresolvedReviewError):
        _load_approved(tmp_path)


def test_approved_drafts_block_a_draft_the_critic_never_reviewed(tmp_path: Path):
    _write_reviewed_lecture(tmp_path, _make_validated_unit(pack_sequence=1))
    _write_draft(tmp_path / "08_drafts", _make_validated_unit(pack_sequence=2).content)

    with pytest.raises(UnresolvedReviewError, match=r"never reviewed \[2\]"):
        _load_approved(tmp_path)


def test_approved_drafts_block_a_review_whose_draft_no_longer_exists(tmp_path: Path):
    _write_reviewed_lecture(
        tmp_path,
        _make_validated_unit(pack_sequence=1),
        _make_validated_unit(pack_sequence=2),
    )
    (tmp_path / "08_drafts" / "aula-001" / "draft_002.json").unlink()

    with pytest.raises(UnresolvedReviewError, match=r"no current draft \[2\]"):
        _load_approved(tmp_path)


def test_approved_drafts_block_a_draft_rewritten_after_its_review(tmp_path: Path):
    unit = _make_validated_unit(pack_sequence=1)
    _write_reviewed_lecture(tmp_path, unit)
    unit_path = tmp_path / "09_validated_units" / "aula-001" / "unit_001.json"
    draft_path = _write_draft(tmp_path / "08_drafts", unit.content)
    reviewed_at = unit_path.stat().st_mtime_ns
    os.utime(draft_path, ns=(reviewed_at + 1_000_000, reviewed_at + 1_000_000))

    with pytest.raises(UnresolvedReviewError, match="changed after"):
        _load_approved(tmp_path)


def test_approved_drafts_block_a_draft_whose_echoed_sequence_disagrees(
    tmp_path: Path,
):
    unit = _make_validated_unit(pack_sequence=1)
    second = _make_validated_unit(pack_sequence=2)
    _write_reviewed_lecture(tmp_path, unit, second)
    misnumbered = second.content.model_copy(update={"pack_sequence": 1})
    (tmp_path / "08_drafts" / "aula-001" / "draft_002.json").write_text(
        misnumbered.model_dump_json()
    )
    _write_unit(tmp_path / "09_validated_units", second)

    with pytest.raises(UnresolvedReviewError, match="another pack"):
        _load_approved(tmp_path)


def test_approved_drafts_block_a_unit_filed_under_another_lecture(tmp_path: Path):
    _write_reviewed_lecture(tmp_path, _make_validated_unit(pack_sequence=1))
    stray = _make_validated_unit(lecture_id="aula-999", pack_sequence=1)
    (tmp_path / "09_validated_units" / "aula-001" / "unit_001.json").write_text(
        stray.model_dump_json()
    )

    with pytest.raises(UnresolvedReviewError, match="belongs to lecture"):
        _load_approved(tmp_path)


def test_approved_drafts_block_a_gap_in_the_reviewed_sequence(tmp_path: Path):
    _write_reviewed_lecture(
        tmp_path,
        _make_validated_unit(pack_sequence=1),
        _make_validated_unit(pack_sequence=3),
    )

    with pytest.raises(MissingSequenceError):
        _load_approved(tmp_path)


@pytest.mark.parametrize("missing", ["09_validated_units", "08_drafts"])
def test_approved_drafts_block_a_lecture_with_a_missing_stage_directory(
    tmp_path: Path, missing: str
):
    _write_reviewed_lecture(tmp_path, _make_validated_unit(pack_sequence=1))
    for path in (tmp_path / missing / "aula-001").iterdir():
        path.unlink()
    (tmp_path / missing / "aula-001").rmdir()

    with pytest.raises(LectureNotFoundError):
        _load_approved(tmp_path)


def test_approved_drafts_block_a_lecture_without_phase_7_units(tmp_path: Path):
    _write_draft(tmp_path / "08_drafts", _make_validated_unit().content)
    (tmp_path / "09_validated_units" / "aula-001").mkdir(parents=True)

    with pytest.raises(LectureNotFoundError, match="no unit file"):
        _load_approved(tmp_path)


def test_approved_drafts_take_identity_from_the_unit_not_the_repaired_draft(
    tmp_path: Path,
):
    unit = _make_validated_unit(pack_sequence=1)
    _write_reviewed_lecture(tmp_path, unit)
    _write_unit(
        tmp_path / "09_validated_units",
        _with_content(unit, lecture_id="outra-aula", pack_sequence=7),
    )

    (draft,) = _load_approved(tmp_path)

    assert (draft.lecture_id, draft.pack_sequence) == ("aula-001", 1)


def test_approved_drafts_reject_a_malformed_lecture_id(tmp_path: Path):
    with pytest.raises(ValidationError):
        load_critic_approved_drafts(
            tmp_path / "09_validated_units", tmp_path / "08_drafts", "../escape"
        )
