"""Unit tests for the personal-study flashcard generator on the LLM gateway
(ADR 0046).

No model is ever called: a scripted gateway double replays canned responses.
The tests exercise fact formatting, per-unit card-count derivation, the
supplemental-backfill loop, the count-mismatch retry/error path, the critic
gate that must run before any gateway is built (ADR 0045), the stage's own
gateway-configuration validation (it runs outside the orchestrator), the run
record and the import boundary, mirroring
tests/test_generate_codex_mock_exam.py for the sibling generator."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from lecture_study.domain.models import (
    ArtifactExecutionMode,
    CriticReview,
    LearningUnitDraft,
    PersonalStudyFlashcard,
    SemanticFact,
    SemanticLedger,
    ValidatedLearningUnit,
)
from lecture_study.llm import Stage
from lecture_study.llm.config import ADAPTER_OVERRIDE_ENV

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


def _card_array_json(*cards: PersonalStudyFlashcard) -> bytes:
    return json.dumps([card.model_dump(mode="json") for card in cards]).encode("utf-8")


def test_request_cards_raises_after_two_count_mismatches(
    make_scripted_gateway,
) -> None:
    wrong_count = _card_array_json(_card("Primeira?"), _card("Segunda?"))
    gateway = make_scripted_gateway([wrong_count, wrong_count])

    with pytest.raises(_MODULE.LlmResponseContractError, match="expected 1 card"):
        _MODULE._request_cards(gateway=gateway, prompt="prompt", expected_count=1)

    assert len(gateway.requests) == 2
    assert all(request.stage is Stage.FLASHCARDS for request in gateway.requests)


def test_request_cards_retries_once_with_feedback_after_a_count_mismatch(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway(
        [
            _card_array_json(_card("Primeira?"), _card("Segunda?")),
            _card_array_json(_card()),
        ]
    )

    cards = _MODULE._request_cards(gateway=gateway, prompt="prompt", expected_count=1)

    assert len(cards) == 1
    assert [request.is_retry for request in gateway.requests] == [False, True]
    assert "Retry feedback:" in gateway.requests[1].prompt
    assert "Retry feedback:" not in gateway.requests[0].prompt


def test_request_cards_retries_when_the_student_guard_rejects_the_text(
    make_scripted_gateway,
) -> None:
    leak = _card().model_copy(update={"answer": "Segundo o material, usar dipirona."})
    gateway = make_scripted_gateway([_card_array_json(leak), _card_array_json(_card())])

    cards = _MODULE._request_cards(gateway=gateway, prompt="prompt", expected_count=1)

    assert cards[0].answer == "Resposta."
    assert len(gateway.requests) == 2
    assert "Retry feedback:" in gateway.requests[1].prompt


def test_generate_supplemental_cards_stops_once_floor_is_met(
    make_scripted_gateway,
) -> None:
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
            gateway=make_scripted_gateway([]),
            grounded_units=(ledger,),
            drafts_by_sequence={1: draft},
            existing_cards=[],
            minimum_total_cards=2,
        )

    assert len(supplemental) == 2
    assert all(card.source_unit_sequence == 1 for card in supplemental)
    assert generate.call_count == 2


def test_generate_supplemental_cards_gives_up_when_units_are_exhausted(
    make_scripted_gateway,
) -> None:
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
            gateway=make_scripted_gateway([]),
            grounded_units=(ledger,),
            drafts_by_sequence={1: draft},
            existing_cards=already_asked,
            minimum_total_cards=5,
        )

    assert supplemental == []
    generate.assert_not_called()


def test_generate_supplemental_cards_targets_the_densest_unit_first(
    make_scripted_gateway,
) -> None:
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
            gateway=make_scripted_gateway([]),
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


def _open_scripted_gateway(
    monkeypatch: pytest.MonkeyPatch, gateway: object
) -> list[Stage]:
    """Replace the module's gateway opener; return the stages it was asked for."""
    opened_stages: list[Stage] = []

    def open_stage_gateway(stage: Stage, **_kwargs: object) -> object:
        opened_stages.append(stage)
        return gateway

    monkeypatch.setattr(_MODULE, "open_stage_gateway", open_stage_gateway)
    return opened_stages


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
        "--llm-runs-root",
        str(tmp_path / "llm_runs"),
        "--minimum-total-cards",
        "1",
    ]
    with patch.object(sys, "argv", argv):
        return _MODULE.main()


def test_main_blocks_the_lecture_when_one_unit_failed_the_critic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_lecture_inputs(
        tmp_path,
        draft_titles={1: "Unidade aprovada", 2: "Unidade reprovada"},
        units=[
            _validated_unit(1, "Unidade aprovada"),
            _validated_unit(2, "Unidade reprovada", review_passed=False),
        ],
    )
    opened_stages = _open_scripted_gateway(monkeypatch, object())

    exit_code = _run_script(tmp_path)

    assert exit_code == 1
    assert opened_stages == []
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "llm_runs").exists()
    assert "aula bloqueada pelo crítico" in capsys.readouterr().err


def test_main_titles_cards_with_the_post_repair_unit_not_the_raw_draft(
    tmp_path: Path, make_scripted_gateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_lecture_inputs(
        tmp_path,
        draft_titles={1: "Título do rascunho bruto"},
        units=[_validated_unit(1, "Título reparado pelo crítico")],
    )
    gateway = make_scripted_gateway([_card_array_json(_card())])
    opened_stages = _open_scripted_gateway(monkeypatch, gateway)

    exit_code = _run_script(tmp_path)

    prompt = gateway.requests[0].prompt
    assert exit_code == 0
    assert opened_stages == [Stage.FLASHCARDS]
    assert "Título reparado pelo crítico" in prompt
    assert "Título do rascunho bruto" not in prompt
    assert (tmp_path / "output" / _LECTURE_ID / "deck.json").exists()


def test_main_writes_an_uncertified_deck_and_a_flashcards_run_record(
    tmp_path: Path, make_scripted_gateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_lecture_inputs(
        tmp_path,
        draft_titles={1: "Unidade 1"},
        units=[_validated_unit(1, "Unidade 1")],
    )
    gateway = make_scripted_gateway([_card_array_json(_card())])
    _open_scripted_gateway(monkeypatch, gateway)

    exit_code = _run_script(tmp_path)

    deck = json.loads(
        (tmp_path / "output" / _LECTURE_ID / "deck.json").read_text(encoding="utf-8")
    )
    record = json.loads(
        (tmp_path / "llm_runs" / _LECTURE_ID / "flashcards.json").read_text(
            encoding="utf-8"
        )
    )
    assert exit_code == 0
    assert deck["execution_mode"] == (
        ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY.value
    )
    assert not list((tmp_path / "output" / _LECTURE_ID).glob("*.tmp"))
    assert record["status"] == "succeeded"
    assert record["script_stage"] == "flashcards"
    assert record["gateways"][0]["calls"] == len(gateway.requests) == 1


def test_a_deck_write_failure_leaves_a_failed_run_record(
    tmp_path: Path, make_scripted_gateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_lecture_inputs(
        tmp_path,
        draft_titles={1: "Unidade 1"},
        units=[_validated_unit(1, "Unidade 1")],
    )
    _open_scripted_gateway(
        monkeypatch, make_scripted_gateway([_card_array_json(_card())])
    )

    def failing_write(_deck: object, _output_dir: Path) -> Path:
        raise FileNotFoundError("output tree unavailable")

    monkeypatch.setattr(_MODULE, "_write_deck_atomically", failing_write)

    exit_code = _run_script(tmp_path)

    record = json.loads(
        (tmp_path / "llm_runs" / _LECTURE_ID / "flashcards.json").read_text(
            encoding="utf-8"
        )
    )
    assert exit_code == 1
    assert record["status"] == "failed"


def test_main_with_an_unknown_adapter_exits_1_before_any_call_or_deck(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-12 second layer: stage 18 has no orchestrator preflight, so the
    script's own `open_stage_gateway` is the whole configuration check."""
    _write_lecture_inputs(
        tmp_path,
        draft_titles={1: "Unidade 1"},
        units=[_validated_unit(1, "Unidade 1")],
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ADAPTER_OVERRIDE_ENV, "bogus")

    exit_code = _run_script(tmp_path)

    error_text = capsys.readouterr().err
    assert exit_code == 1
    assert '"codex"' in error_text
    assert '"llama-cpp"' in error_text
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "llm_runs").exists()


def test_script_imports_no_process_or_scratch_directory_module() -> None:
    forbidden_modules = {
        "tempfile",
        "subprocess",
        "lecture_study.ops.process_lifecycle",
        "lecture_study.ops.process_environment",
    }
    source_text = Path(_MODULE.__file__).read_text(encoding="utf-8")

    assert _imported_module_names(source_text).isdisjoint(forbidden_modules)


def _imported_module_names(source_text: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source_text)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names
