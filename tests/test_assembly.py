"""Document Assembly (Phase 8) tests: deterministic aggregation of
ValidatedLearningUnits into a CanonicalLectureDocument, in strict
pack_sequence order. No LLM/subprocess involved -- pure data
transformation."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.assembly import assembler as assembler_module
from lecture_study.assembly.assembler import DocumentAssembler
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
