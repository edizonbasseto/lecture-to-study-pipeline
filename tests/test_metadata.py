"""Global Metadata Aggregation (Phase 9) tests: deterministic, no-LLM
deduplication of a lecture's SemanticLedgers into a LectureGlobalIndex."""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.errors import (
    LectureNotFoundError,
    SemanticLedgersNotFoundError,
)
from lecture_study.domain.models import LectureGlobalIndex, SemanticFact, SemanticLedger
from lecture_study.metadata import aggregator as aggregator_module
from lecture_study.metadata.aggregator import MetadataAggregator


def _setup_aggregator_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        aggregator_module,
        "SEMANTIC_LEDGERS_INPUT_DIR",
        tmp_path / "07_semantic_ledgers",
    )
    monkeypatch.setattr(
        aggregator_module,
        "CANONICAL_DOCUMENTS_INPUT_DIR",
        tmp_path / "10_canonical_documents",
    )
    monkeypatch.setattr(
        aggregator_module, "METADATA_OUTPUT_DIR", tmp_path / "11_metadata"
    )


def _mark_lecture_canonical(tmp_path: Path, lecture_id: str) -> None:
    # MetadataAggregator only checks that this file exists (Phase 8
    # completed for this lecture); its content is never parsed.
    lecture_dir = tmp_path / "10_canonical_documents" / lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / "canonical_document.json").write_text("{}")


def _make_fact(**overrides: object) -> SemanticFact:
    defaults: dict[str, object] = dict(
        entity_name="Aspirina",
        category="dosagem",
        clinical_context="100mg via oral a cada 8 horas.",
        timestamp_ref=12.5,
    )
    defaults.update(overrides)
    return SemanticFact(**defaults)


def _write_ledger(
    tmp_path: Path, lecture_id: str, pack_sequence: int, facts: list[SemanticFact]
) -> None:
    lecture_dir = tmp_path / "07_semantic_ledgers" / lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    ledger = SemanticLedger(
        lecture_id=lecture_id, pack_sequence=pack_sequence, facts=facts
    )
    (lecture_dir / f"ledger_{pack_sequence:03d}.json").write_text(
        ledger.model_dump_json()
    )


def test_aggregate_lecture_raises_lecture_not_found_when_canonical_document_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)

    with pytest.raises(LectureNotFoundError, match="aula-001"):
        MetadataAggregator().aggregate_lecture("aula-001")


def test_aggregate_lecture_raises_semantic_ledgers_not_found_when_directory_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # A canonically-complete lecture always had a 07_semantic_ledgers/
    # directory at Phase 5 time (one ledger file per pack, written
    # unconditionally); a directory missing outright is data loss, not a
    # lecture with no clinical content.
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")

    with pytest.raises(SemanticLedgersNotFoundError, match="aula-001"):
        MetadataAggregator().aggregate_lecture("aula-001")


def test_aggregate_lecture_returns_empty_index_when_ledgers_directory_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # An *existing* but empty directory (Phase 5 ran, produced ledgers
    # elsewhere but none survived, or the lecture had zero packs) is a
    # valid, not an error, outcome -- distinct from the missing-directory
    # case above, matching Phase 8's own directory-exists-but-empty
    # precedent.
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    (tmp_path / "07_semantic_ledgers" / "aula-001").mkdir(parents=True)

    index = MetadataAggregator().aggregate_lecture("aula-001")

    assert index.lecture_id == "aula-001"
    assert index.glossary_terms == []
    assert index.dosages == []
    assert index.red_flags == []


def test_aggregate_lecture_persists_global_index_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    _write_ledger(tmp_path, "aula-001", pack_sequence=1, facts=[_make_fact()])

    index = MetadataAggregator().aggregate_lecture("aula-001")

    output_path = tmp_path / "11_metadata" / "aula-001" / "global_index.json"
    assert output_path.exists()
    persisted = LectureGlobalIndex.model_validate_json(output_path.read_text())
    assert persisted == index


def test_aggregate_lecture_does_not_write_index_when_canonical_document_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)

    with pytest.raises(LectureNotFoundError):
        MetadataAggregator().aggregate_lecture("aula-001")

    output_path = tmp_path / "11_metadata" / "aula-001" / "global_index.json"
    assert not output_path.exists()


def test_aggregate_lecture_deduplicates_glossary_terms_via_text_normalization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    first_fact = _make_fact(entity_name="Aspirina 100mg ", category="informacao_geral")
    second_fact = _make_fact(entity_name="aspirina 100mg", category="informacao_geral")
    _write_ledger(tmp_path, "aula-001", pack_sequence=1, facts=[first_fact])
    _write_ledger(tmp_path, "aula-001", pack_sequence=2, facts=[second_fact])

    index = MetadataAggregator().aggregate_lecture("aula-001")

    # Equality on the full SemanticFact proves clinical_context (not just
    # entity_name) survives glossary deduplication.
    assert index.glossary_terms == [first_fact]


def test_aggregate_lecture_deduplicates_dosages_via_entity_and_context_signature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    first_fact = _make_fact(
        entity_name="Aspirina 100mg ",
        category="dosagem",
        clinical_context="Via oral a cada 8 horas.",
    )
    second_fact = _make_fact(
        entity_name="aspirina 100mg",
        category="posologia",
        clinical_context="via oral a cada 8 horas",
    )
    _write_ledger(tmp_path, "aula-001", pack_sequence=1, facts=[first_fact])
    _write_ledger(tmp_path, "aula-001", pack_sequence=2, facts=[second_fact])

    index = MetadataAggregator().aggregate_lecture("aula-001")

    assert len(index.dosages) == 1
    assert index.dosages[0] == first_fact


def test_aggregate_lecture_keeps_distinct_dosages_with_same_entity_different_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    lower_dose = _make_fact(
        entity_name="Aspirina",
        category="dosagem",
        clinical_context="100mg a cada 8 horas.",
    )
    higher_dose = _make_fact(
        entity_name="Aspirina",
        category="dosagem",
        clinical_context="500mg a cada 6 horas.",
    )
    _write_ledger(
        tmp_path, "aula-001", pack_sequence=1, facts=[lower_dose, higher_dose]
    )

    index = MetadataAggregator().aggregate_lecture("aula-001")

    assert len(index.dosages) == 2


def test_aggregate_lecture_routes_facts_by_category(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    dosage_fact = _make_fact(
        entity_name="Aspirina", category="Dosagem", clinical_context="100mg."
    )
    red_flag_fact = _make_fact(
        entity_name="Gravidez",
        category="Contraindicacao",
        clinical_context="Evitar uso na gravidez.",
    )
    neutral_fact = _make_fact(
        entity_name="Ciclo-oxigenase",
        category="glossario",
        clinical_context="Enzima alvo do farmaco.",
    )
    _write_ledger(
        tmp_path,
        "aula-001",
        pack_sequence=1,
        facts=[dosage_fact, red_flag_fact, neutral_fact],
    )

    index = MetadataAggregator().aggregate_lecture("aula-001")

    assert [fact.entity_name for fact in index.dosages] == ["Aspirina"]
    assert [fact.entity_name for fact in index.red_flags] == ["Gravidez"]
    assert neutral_fact in index.glossary_terms
    assert neutral_fact.entity_name not in [fact.entity_name for fact in index.dosages]
    assert neutral_fact.entity_name not in [
        fact.entity_name for fact in index.red_flags
    ]


def test_aggregate_lecture_logs_facts_matching_neither_category(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    neutral_fact = _make_fact(
        entity_name="Ciclo-oxigenase", category="glossario", clinical_context="Enzima."
    )
    _write_ledger(tmp_path, "aula-001", pack_sequence=1, facts=[neutral_fact])

    with caplog.at_level(logging.DEBUG, logger="lecture_study.metadata.aggregator"):
        MetadataAggregator().aggregate_lecture("aula-001")

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "aula-001" in message
    assert neutral_fact.entity_name in message
    assert neutral_fact.category in message


def test_aggregate_lecture_truncates_long_category_before_logging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    long_category = "x" * 250
    neutral_fact = _make_fact(category=long_category)
    _write_ledger(tmp_path, "aula-001", pack_sequence=1, facts=[neutral_fact])

    with caplog.at_level(logging.DEBUG, logger="lecture_study.metadata.aggregator"):
        MetadataAggregator().aggregate_lecture("aula-001")

    message = caplog.records[0].getMessage()
    assert long_category not in message
    assert "truncated, 250 chars total" in message


def test_aggregate_lecture_does_not_log_facts_matching_a_category(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    dosage_fact = _make_fact(category="dosagem")
    _write_ledger(tmp_path, "aula-001", pack_sequence=1, facts=[dosage_fact])

    with caplog.at_level(logging.DEBUG, logger="lecture_study.metadata.aggregator"):
        MetadataAggregator().aggregate_lecture("aula-001")

    assert caplog.records == []


def test_aggregate_lecture_orders_facts_by_pack_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    # Written out of pack_sequence order to prove the aggregator re-sorts
    # ledgers instead of trusting glob() iteration order.
    _write_ledger(
        tmp_path, "aula-001", pack_sequence=2, facts=[_make_fact(entity_name="Segundo")]
    )
    _write_ledger(
        tmp_path,
        "aula-001",
        pack_sequence=1,
        facts=[_make_fact(entity_name="Primeiro")],
    )

    index = MetadataAggregator().aggregate_lecture("aula-001")

    assert [fact.entity_name for fact in index.glossary_terms] == [
        "Primeiro",
        "Segundo",
    ]


def test_aggregate_lecture_deduplicates_glossary_terms_via_edge_punctuation_strip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    first_fact = _make_fact(entity_name="Aspirina.", category="informacao_geral")
    second_fact = _make_fact(entity_name="Aspirina", category="informacao_geral")
    _write_ledger(
        tmp_path, "aula-001", pack_sequence=1, facts=[first_fact, second_fact]
    )

    index = MetadataAggregator().aggregate_lecture("aula-001")

    assert index.glossary_terms == [first_fact]


def test_aggregate_lecture_can_route_a_fact_into_both_dosages_and_red_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # A category matching keywords from both tuples (e.g. "Tratamento de
    # risco") is not disambiguated by design: the fact is clinically both
    # a treatment note and a risk alert, so it is intentionally indexed
    # in both lists rather than forced into one.
    _setup_aggregator_dirs(monkeypatch, tmp_path)
    _mark_lecture_canonical(tmp_path, "aula-001")
    dual_fact = _make_fact(
        entity_name="Varfarina",
        category="Tratamento de risco",
        clinical_context="Ajustar dose com cautela.",
    )
    _write_ledger(tmp_path, "aula-001", pack_sequence=1, facts=[dual_fact])

    index = MetadataAggregator().aggregate_lecture("aula-001")

    assert index.dosages == [dual_fact]
    assert index.red_flags == [dual_fact]


def test_aggregate_lecture_rejects_malformed_lecture_id_before_touching_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_aggregator_dirs(monkeypatch, tmp_path)

    with pytest.raises(ValidationError):
        MetadataAggregator().aggregate_lecture("../../etc")

    # Only the aggregator's three configured roots may exist under
    # tmp_path; a path-traversal lecture_id must never reach
    # Path.glob/.exists.
    assert list(tmp_path.iterdir()) == []
