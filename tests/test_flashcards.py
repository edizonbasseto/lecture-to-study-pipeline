"""Anki Flashcard Generation (Phase 12) tests: deterministic, no-LLM
conversion of a lecture's LectureGlobalIndex into an Anki-importable CSV
deck."""

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.errors import GlobalIndexNotFoundError, LectureNotFoundError
from lecture_study.domain.models import LectureGlobalIndex, SemanticFact
from lecture_study.flashcards import generator as generator_module
from lecture_study.flashcards.generator import AnkiDeckGenerator
from lecture_study.quality.delivery import (
    DeliveryArtifactKind,
    DeliveryVerificationError,
)


pytestmark = pytest.mark.usefixtures("legacy_certification_bypass")


def _setup_generator_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        generator_module, "METADATA_INPUT_DIR", tmp_path / "11_metadata"
    )
    monkeypatch.setattr(
        generator_module, "FLASHCARDS_OUTPUT_DIR", tmp_path / "13_flashcards"
    )


def _make_fact(**overrides: object) -> SemanticFact:
    defaults: dict[str, object] = dict(
        entity_name="Aspirina",
        category="dosagem",
        clinical_context="100mg via oral a cada 8 horas.",
        timestamp_ref=12.5,
    )
    defaults.update(overrides)
    return SemanticFact(**defaults)


def _write_global_index(
    tmp_path: Path, lecture_id: str, index: LectureGlobalIndex
) -> None:
    lecture_dir = tmp_path / "11_metadata" / lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / "global_index.json").write_text(index.model_dump_json())


def _read_deck_rows(deck_path: Path) -> list[list[str]]:
    with deck_path.open(newline="", encoding="utf-8") as deck_file:
        return list(csv.reader(deck_file))


def test_generate_deck_raises_global_index_not_found_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_generator_dirs(monkeypatch, tmp_path)

    with pytest.raises(GlobalIndexNotFoundError, match="aula-001"):
        AnkiDeckGenerator().generate_deck("aula-001")


def test_global_index_not_found_error_is_catchable_as_lecture_not_found_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # GlobalIndexNotFoundError subclasses LectureNotFoundError so a caller
    # already doing `except LectureNotFoundError` (matching every other
    # phase's missing-upstream-artifact handling) also catches Phase 12's
    # own missing-global_index.json condition, instead of it escaping as
    # an unhandled disjoint exception type.
    _setup_generator_dirs(monkeypatch, tmp_path)

    with pytest.raises(LectureNotFoundError):
        AnkiDeckGenerator().generate_deck("aula-001")


def test_generate_deck_does_not_write_deck_when_global_index_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_generator_dirs(monkeypatch, tmp_path)

    with pytest.raises(GlobalIndexNotFoundError):
        AnkiDeckGenerator().generate_deck("aula-001")

    assert not (tmp_path / "13_flashcards").exists()


def test_generate_deck_rejects_malformed_lecture_id_before_touching_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_generator_dirs(monkeypatch, tmp_path)

    with pytest.raises(ValidationError):
        AnkiDeckGenerator().generate_deck("../../etc")

    # Only the generator's two configured roots may exist under tmp_path;
    # a path-traversal lecture_id must never reach Path.exists()/mkdir.
    assert list(tmp_path.iterdir()) == []


def test_generate_deck_maps_glossary_dosage_and_red_flag_categories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_generator_dirs(monkeypatch, tmp_path)
    glossary_fact = _make_fact(
        entity_name="Bradicardia", clinical_context="Frequencia cardiaca reduzida."
    )
    dosage_fact = _make_fact(entity_name="Aspirina", clinical_context="100mg VO 8/8h.")
    red_flag_fact = _make_fact(
        entity_name="Gravidez", clinical_context="Evitar uso na gravidez."
    )
    index = LectureGlobalIndex(
        lecture_id="aula-001",
        glossary_terms=[glossary_fact],
        dosages=[dosage_fact],
        red_flags=[red_flag_fact],
    )
    _write_global_index(tmp_path, "aula-001", index)

    output_path = AnkiDeckGenerator().generate_deck("aula-001")

    assert output_path == tmp_path / "13_flashcards" / "aula-001" / "anki_deck.csv"
    rows = _read_deck_rows(output_path)
    assert rows == [
        [
            "O que é Bradicardia?",
            "Frequencia cardiaca reduzida.",
            "glossario",
        ],
        [
            "Qual a diretriz/dosagem para Aspirina?",
            "100mg VO 8/8h.",
            "dosagem",
        ],
        [
            "🚨 ALERTA CLÍNICO: Quais as contraindicações/alertas para Gravidez?",
            "Evitar uso na gravidez.",
            "red_flag",
        ],
    ]


def test_issuer_preflight_failure_preserves_previous_flashcard_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _setup_generator_dirs(monkeypatch, tmp_path)
    index = LectureGlobalIndex(
        lecture_id="aula-001",
        glossary_terms=[],
        dosages=[_make_fact()],
        red_flags=[],
    )
    _write_global_index(tmp_path, "aula-001", index)
    output_dir = tmp_path / "13_flashcards" / "aula-001"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "anki_deck.csv"
    previous_output_bytes = b"previously,certified,deck\r\n"
    output_path.write_bytes(previous_output_bytes)

    def reject_preflight(
        _lecture_id: str,
        _artifact_kind: DeliveryArtifactKind,
        *,
        expected_output_bytes: dict[str, bytes],
    ) -> None:
        assert expected_output_bytes["anki_deck.csv"] != previous_output_bytes
        raise DeliveryVerificationError("injected issuer preflight failure")

    monkeypatch.setattr(
        generator_module,
        "issue_deterministic_delivery",
        reject_preflight,
    )

    with pytest.raises(
        DeliveryVerificationError,
        match="injected issuer preflight failure",
    ):
        AnkiDeckGenerator().generate_deck("aula-001")

    assert output_path.read_bytes() == previous_output_bytes


def test_generate_deck_writes_no_rows_when_index_has_no_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_generator_dirs(monkeypatch, tmp_path)
    index = LectureGlobalIndex(
        lecture_id="aula-001", glossary_terms=[], dosages=[], red_flags=[]
    )
    _write_global_index(tmp_path, "aula-001", index)

    output_path = AnkiDeckGenerator().generate_deck("aula-001")

    assert output_path.exists()
    assert _read_deck_rows(output_path) == []


def test_generate_deck_sanitizes_quotes_commas_and_newlines_in_csv_structure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Critical test: entity_name/clinical_context are LLM-extracted free
    # text (Phase 5) that may contain commas, double quotes, and embedded
    # newlines. csv.writer already quotes fields with commas/quotes per
    # RFC 4180; newlines must additionally become "<br>" (Anki's own
    # multi-line-field convention) so a raw newline never splits a card
    # across CSV rows.
    _setup_generator_dirs(monkeypatch, tmp_path)
    adversarial_fact = _make_fact(
        entity_name='Aspirina, 100mg "forte"',
        clinical_context='Linha 1\nLinha 2, com "aspas".',
    )
    index = LectureGlobalIndex(
        lecture_id="aula-001",
        glossary_terms=[],
        dosages=[adversarial_fact],
        red_flags=[],
    )
    _write_global_index(tmp_path, "aula-001", index)

    output_path = AnkiDeckGenerator().generate_deck("aula-001")

    rows = _read_deck_rows(output_path)
    assert rows == [
        [
            'Qual a diretriz/dosagem para Aspirina, 100mg "forte"?',
            'Linha 1<br>Linha 2, com "aspas".',
            "dosagem",
        ]
    ]


def test_generate_deck_neutralizes_leading_formula_trigger_character(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # A field beginning with "=", "+", "-", "@", or a tab is interpreted as
    # a formula by spreadsheet applications (Excel, Numbers, LibreOffice)
    # if this CSV is ever opened outside Anki (OWASP's CSV/formula-
    # injection trigger set, CWE-1236). A leading single quote is the
    # standard neutralization: it stays valid, human-readable text in
    # Anki while stopping formula evaluation elsewhere.
    _setup_generator_dirs(monkeypatch, tmp_path)
    formula_fact = _make_fact(entity_name="Varfarina", clinical_context="=SOMA(A1:A2)")
    index = LectureGlobalIndex(
        lecture_id="aula-001",
        glossary_terms=[],
        dosages=[formula_fact],
        red_flags=[],
    )
    _write_global_index(tmp_path, "aula-001", index)

    output_path = AnkiDeckGenerator().generate_deck("aula-001")

    rows = _read_deck_rows(output_path)
    assert rows[0][1] == "'=SOMA(A1:A2)"


@pytest.mark.parametrize("trigger_char", ["=", "+", "-", "@", "\t"])
def test_generate_deck_neutralizes_every_formula_trigger_character(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, trigger_char: str
):
    _setup_generator_dirs(monkeypatch, tmp_path)
    formula_fact = _make_fact(clinical_context=f"{trigger_char}CMD('/c calc')")
    index = LectureGlobalIndex(
        lecture_id="aula-001", glossary_terms=[], dosages=[formula_fact], red_flags=[]
    )
    _write_global_index(tmp_path, "aula-001", index)

    output_path = AnkiDeckGenerator().generate_deck("aula-001")

    rows = _read_deck_rows(output_path)
    assert rows[0][1] == f"'{trigger_char}CMD('/c calc')"


def test_generate_deck_neutralizes_formula_trigger_after_leading_whitespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # A bare startswith() check on the trigger characters misses
    # " =SOMA(...)" or "\t=SOMA(...)": the trigger character is not
    # literally first, but several spreadsheet parsers still evaluate it
    # as a formula once leading whitespace is skipped.
    _setup_generator_dirs(monkeypatch, tmp_path)
    formula_fact = _make_fact(clinical_context=" =SOMA(A1:A2)")
    index = LectureGlobalIndex(
        lecture_id="aula-001", glossary_terms=[], dosages=[formula_fact], red_flags=[]
    )
    _write_global_index(tmp_path, "aula-001", index)

    output_path = AnkiDeckGenerator().generate_deck("aula-001")

    rows = _read_deck_rows(output_path)
    assert rows[0][1] == "' =SOMA(A1:A2)"


def test_generate_deck_leaves_non_formula_text_unprefixed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_generator_dirs(monkeypatch, tmp_path)
    plain_fact = _make_fact(clinical_context="100mg via oral a cada 8 horas.")
    index = LectureGlobalIndex(
        lecture_id="aula-001", glossary_terms=[], dosages=[plain_fact], red_flags=[]
    )
    _write_global_index(tmp_path, "aula-001", index)

    output_path = AnkiDeckGenerator().generate_deck("aula-001")

    rows = _read_deck_rows(output_path)
    assert rows[0][1] == "100mg via oral a cada 8 horas."
