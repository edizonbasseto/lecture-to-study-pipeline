"""Unit tests for the Codex-based personal-study flashcard generator.

The `codex` subprocess itself is never invoked here -- these tests stub
`_call_codex_for_cards`/`run_bounded_process` and exercise the pure
logic: fact formatting, per-unit card-count derivation, the
supplemental-backfill loop, and the count-mismatch retry/error path,
mirroring tests/test_generate_codex_mock_exam.py's own coverage for the
sibling Codex-based generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from lecture_study.domain.models import (
    CriticReview,
    LearningUnitDraft,
    PersonalStudyFlashcard,
    SemanticFact,
    SemanticLedger,
    ValidatedLearningUnit,
)

_LECTURE_ID = "lecture-001"


def _load_module() -> ModuleType:
    script_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "generate_codex_personal_study_flashcards.py"
    )
    spec = importlib.util.spec_from_file_location(
        "generate_codex_personal_study_flashcards",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _fact(entity_name: str, index: int) -> SemanticFact:
    return SemanticFact(
        entity_name=entity_name,
        category="anatomy",
        clinical_context=f"contexto clínico {index}",
        timestamp_ref=float(index),
    )


def _card(prompt: str = "Pergunta?") -> PersonalStudyFlashcard:
    return PersonalStudyFlashcard(
        prompt=prompt, answer="Resposta.", source_unit_sequence=1
    )


def test_format_facts_lists_entity_category_and_context() -> None:
    facts = (_fact("Nervo facial", 1), _fact("Carótida", 2))

    formatted = _MODULE._format_facts(facts)

    assert formatted == (
        "- Nervo facial (anatomy): contexto clínico 1\n"
        "- Carótida (anatomy): contexto clínico 2"
    )


def test_rejects_a_card_unfit_for_a_student() -> None:
    # ADR 0027 enforcement for the Codex flashcard route (security review
    # BLOCK, 2026-09-16): the deleted local generator's guard must not be
    # lost with it.
    import pytest

    leak = _card().model_copy(update={"answer": "Segundo o material, usar dipirona."})
    with pytest.raises(_MODULE.LlmResponseContractError, match="unfit for a student"):
        _MODULE._reject_cards_unfit_for_a_student([leak])


def test_accepts_clean_ptbr_cards() -> None:
    _MODULE._reject_cards_unfit_for_a_student([_card()])


def test_call_codex_for_cards_raises_on_count_mismatch() -> None:
    def fake_run_bounded_process(args, **_kwargs):
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(b"[]")

    with (
        patch.object(
            _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
        ),
        patch.object(_MODULE, "_parse_card_array", return_value=[_card(), _card()]),
        pytest.raises(_MODULE.LlmResponseContractError, match="expected 1 card"),
    ):
        _MODULE._call_codex_for_cards(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            expected_count=1,
        )


def test_call_codex_for_cards_runs_with_a_scratch_cwd_not_the_repo_root() -> None:
    """Mirrors test_generate_codex_mock_exam.py's own security-review
    coverage: the subprocess must never run with the repository checkout
    (which holds .env) as its cwd/-C working root."""

    captured: dict[str, object] = {}

    def fake_run_bounded_process(args, *, cwd, **kwargs):
        captured["cwd"] = cwd
        captured["dash_c"] = args[args.index("-C") + 1]
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(
            b'[{"prompt":"p","answer":"a","source_unit_sequence":1}]'
        )

    with patch.object(
        _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
    ):
        cards = _MODULE._call_codex_for_cards(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            expected_count=1,
        )

    repo_root = Path(__file__).parents[1].resolve()
    assert len(cards) == 1
    assert Path(captured["cwd"]).resolve() != repo_root
    assert str(captured["cwd"]) == captured["dash_c"]


def test_generate_supplemental_cards_stops_once_floor_is_met() -> None:
    ledger = SemanticLedger(
        lecture_id="lecture-001",
        pack_sequence=1,
        facts=[_fact("a", 1), _fact("b", 2), _fact("c", 3)],
    )
    draft = LearningUnitDraft(
        lecture_id="lecture-001",
        pack_sequence=1,
        title="Unidade 1",
        body_markdown="corpo",
        clinical_warnings=[],
    )

    with patch.object(
        _MODULE,
        "_generate_supplemental_unit_card",
        return_value=[_card()],
    ) as generate:
        supplemental = _MODULE._generate_supplemental_cards(
            codex_executable="codex",
            environment={},
            grounded_units=(ledger,),
            drafts_by_sequence={1: draft},
            existing_cards=[],
            minimum_total_cards=2,
        )

    assert len(supplemental) == 2
    assert all(card.source_unit_sequence == 1 for card in supplemental)
    assert generate.call_count == 2


def test_generate_supplemental_cards_gives_up_when_units_are_exhausted() -> None:
    ledger = SemanticLedger(
        lecture_id="lecture-001",
        pack_sequence=1,
        facts=[_fact("a", 1)],
    )
    draft = LearningUnitDraft(
        lecture_id="lecture-001",
        pack_sequence=1,
        title="Unidade 1",
        body_markdown="corpo",
        clinical_warnings=[],
    )
    already_asked = [_card().model_copy(update={"source_unit_sequence": 1})]

    with patch.object(_MODULE, "_generate_supplemental_unit_card") as generate:
        supplemental = _MODULE._generate_supplemental_cards(
            codex_executable="codex",
            environment={},
            grounded_units=(ledger,),
            drafts_by_sequence={1: draft},
            existing_cards=already_asked,
            minimum_total_cards=5,
        )

    assert supplemental == []
    generate.assert_not_called()


def test_generate_supplemental_cards_targets_the_densest_unit_first() -> None:
    sparse_ledger = SemanticLedger(
        lecture_id="lecture-001", pack_sequence=1, facts=[_fact("a", 1)]
    )
    dense_ledger = SemanticLedger(
        lecture_id="lecture-001",
        pack_sequence=2,
        facts=[_fact("b", 1), _fact("c", 2)],
    )
    drafts_by_sequence = {
        1: LearningUnitDraft(
            lecture_id="lecture-001",
            pack_sequence=1,
            title="Esparsa",
            body_markdown="corpo",
            clinical_warnings=[],
        ),
        2: LearningUnitDraft(
            lecture_id="lecture-001",
            pack_sequence=2,
            title="Densa",
            body_markdown="corpo",
            clinical_warnings=[],
        ),
    }

    with patch.object(
        _MODULE,
        "_generate_supplemental_unit_card",
        return_value=[_card()],
    ) as generate:
        supplemental = _MODULE._generate_supplemental_cards(
            codex_executable="codex",
            environment={},
            grounded_units=(sparse_ledger, dense_ledger),
            drafts_by_sequence=drafts_by_sequence,
            existing_cards=[],
            minimum_total_cards=1,
        )

    assert len(supplemental) == 1
    assert supplemental[0].source_unit_sequence == 2
    assert generate.call_args.kwargs["unit_title"] == "Densa"


def test_build_deck_wraps_a_cross_unit_duplicate_prompt_as_a_domain_error() -> None:
    """Regression (principal-code-reviewer BLOCK): PersonalStudyFlashcard
    Deck rejects two cards sharing the same `prompt` text deck-wide, but
    each unit's own generation call only steers Codex away from
    duplicates within that same unit -- a cross-unit collision surfaced
    as an uncaught pydantic.ValidationError before this fix, discarding
    every already-completed Codex call with no clean error."""
    colliding_cards = [
        _card("Mesma pergunta?").model_copy(update={"source_unit_sequence": 1}),
        _card("Mesma pergunta?").model_copy(update={"source_unit_sequence": 2}),
    ]

    with pytest.raises(_MODULE.LlmResponseContractError, match="failed validation"):
        _MODULE._build_deck("lecture-001", colliding_cards)


def _draft(pack_sequence: int, title: str) -> LearningUnitDraft:
    return LearningUnitDraft(
        lecture_id=_LECTURE_ID,
        pack_sequence=pack_sequence,
        title=title,
        body_markdown="corpo",
        clinical_warnings=[],
    )


def _validated_unit(
    pack_sequence: int, title: str, *, review_passed: bool = True
) -> ValidatedLearningUnit:
    return ValidatedLearningUnit(
        lecture_id=_LECTURE_ID,
        pack_sequence=pack_sequence,
        content=_draft(pack_sequence, title),
        review=CriticReview(
            passed=review_passed,
            hallucination_score=0.0,
            feedback="ok" if review_passed else "afirmação sem suporte no ledger",
        ),
    )


def _write_lecture_inputs(
    tmp_path: Path,
    *,
    draft_titles: dict[int, str],
    units: list[ValidatedLearningUnit],
) -> None:
    """Write one ledger and one Phase 6 draft per entry of `draft_titles`,
    plus the given Phase 7 units, under the per-stage roots of tmp_path."""
    for pack_sequence, title in draft_titles.items():
        ledger = SemanticLedger(
            lecture_id=_LECTURE_ID,
            pack_sequence=pack_sequence,
            facts=[_fact("Nervo facial", pack_sequence)],
        )
        _write_json(tmp_path / "ledgers", f"ledger_{pack_sequence:03d}.json", ledger)
        draft = _draft(pack_sequence, title)
        _write_json(tmp_path / "drafts", f"draft_{pack_sequence:03d}.json", draft)
    for unit in units:
        _write_json(
            tmp_path / "validated_units", f"unit_{unit.pack_sequence:03d}.json", unit
        )


def _write_json(
    root: Path,
    file_name: str,
    model: SemanticLedger | LearningUnitDraft | ValidatedLearningUnit,
) -> None:
    lecture_dir = root / _LECTURE_ID
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / file_name).write_text(model.model_dump_json())


def _run_script(tmp_path: Path) -> int:
    argv = [
        "generate_codex_personal_study_flashcards.py",
        _LECTURE_ID,
        "--ledgers-root",
        str(tmp_path / "ledgers"),
        "--drafts-root",
        str(tmp_path / "drafts"),
        "--validated-units-root",
        str(tmp_path / "validated_units"),
        "--output-root",
        str(tmp_path / "output"),
        "--minimum-total-cards",
        "1",
    ]
    with (
        patch.object(sys, "argv", argv),
        patch.object(_MODULE, "resolve_trusted_executable", return_value="codex"),
    ):
        return _MODULE.main()


def test_main_blocks_the_lecture_when_one_unit_failed_the_critic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_lecture_inputs(
        tmp_path,
        draft_titles={1: "Unidade aprovada", 2: "Unidade reprovada"},
        units=[
            _validated_unit(1, "Unidade aprovada"),
            _validated_unit(2, "Unidade reprovada", review_passed=False),
        ],
    )

    with patch.object(_MODULE, "_call_codex_for_cards") as call_codex:
        exit_code = _run_script(tmp_path)

    assert exit_code == 1
    call_codex.assert_not_called()
    assert not (tmp_path / "output").exists()
    assert "aula bloqueada pelo crítico" in capsys.readouterr().err


def test_main_titles_cards_with_the_post_repair_unit_not_the_raw_draft(
    tmp_path: Path,
) -> None:
    _write_lecture_inputs(
        tmp_path,
        draft_titles={1: "Título do rascunho bruto"},
        units=[_validated_unit(1, "Título reparado pelo crítico")],
    )

    with patch.object(
        _MODULE, "_call_codex_for_cards", return_value=[_card()]
    ) as call_codex:
        exit_code = _run_script(tmp_path)

    prompt = call_codex.call_args.kwargs["prompt"]
    assert exit_code == 0
    assert "Título reparado pelo crítico" in prompt
    assert "Título do rascunho bruto" not in prompt
    assert (tmp_path / "output" / _LECTURE_ID / "deck.json").exists()
