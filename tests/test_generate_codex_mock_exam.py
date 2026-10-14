"""Unit tests for the Codex-based personal-study mock-exam generator.

The `codex` subprocess itself is never invoked here -- these tests stub
`_call_codex_for_questions`/`run_bounded_process` and exercise the pure
logic: fact formatting, per-unit question-count derivation, the
supplemental-backfill loop, and the count-mismatch retry/error path
(2026-09-15 security+code review findings)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from lecture_study.domain.models import (
    LearningUnitDraft,
    MockQuestion,
    SemanticFact,
    SemanticLedger,
)


def _load_module() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "generate_codex_mock_exam.py"
    spec = importlib.util.spec_from_file_location(
        "generate_codex_mock_exam",
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


def _question(text: str = "Pergunta?") -> MockQuestion:
    return MockQuestion(
        question_text=text,
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_answer="A",
        explanation="Explicação.",
    )


def test_format_facts_lists_entity_category_and_context() -> None:
    facts = (_fact("Nervo facial", 1), _fact("Carótida", 2))

    formatted = _MODULE._format_facts(facts)

    assert formatted == (
        "- Nervo facial (anatomy): contexto clínico 1\n"
        "- Carótida (anatomy): contexto clínico 2"
    )


def test_rejects_a_question_unfit_for_a_student() -> None:
    # ADR 0027 enforcement restored for the Codex route (security review
    # BLOCK, 2026-09-16): deleting the local generator removed the last
    # mechanical guard; prompt wording alone is not enforcement.
    import pytest

    english = _question("A 6-year-old dog presents with a dermal mass.")
    with pytest.raises(_MODULE.LlmResponseContractError, match="unfit for a student"):
        _MODULE._reject_questions_unfit_for_a_student([english])

    leak = _question()
    leak = leak.model_copy(
        update={"explanation": "O Evidence Pack indica biópsia incisional."}
    )
    with pytest.raises(_MODULE.LlmResponseContractError, match="unfit for a student"):
        _MODULE._reject_questions_unfit_for_a_student([leak])


def test_accepts_clean_ptbr_questions() -> None:
    questions = [_question("Qual a conduta para bradicardia sinusal?")]

    _MODULE._reject_questions_unfit_for_a_student(questions)


def test_call_codex_for_questions_raises_on_count_mismatch(
    tmp_path: Path,
) -> None:
    def fake_run_bounded_process(args, **_kwargs):
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(b"[]")

    with (
        patch.object(
            _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
        ),
        patch.object(
            _MODULE, "_parse_question_array", return_value=[_question(), _question()]
        ),
        pytest.raises(_MODULE.LlmResponseContractError, match="expected 1 question"),
    ):
        _MODULE._call_codex_for_questions(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            expected_count=1,
        )


def test_call_codex_for_questions_runs_with_a_scratch_cwd_not_the_repo_root(
    tmp_path: Path,
) -> None:
    """2026-09-15 security review: the subprocess must never run with the
    repository checkout (which holds .env) as its cwd/-C working root."""

    captured: dict[str, object] = {}

    def fake_run_bounded_process(args, *, cwd, **kwargs):
        captured["cwd"] = cwd
        captured["dash_c"] = args[args.index("-C") + 1]
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(
            b'[{"question_text":"q","options":{"A":"1","B":"2","C":"3","D":"4"},"correct_answer":"A","explanation":"e"}]'
        )

    with patch.object(
        _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
    ):
        questions = _MODULE._call_codex_for_questions(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            expected_count=1,
        )

    repo_root = Path(__file__).parents[1].resolve()
    assert len(questions) == 1
    assert Path(captured["cwd"]).resolve() != repo_root
    assert str(captured["cwd"]) == captured["dash_c"]


def test_generate_supplemental_questions_stops_once_floor_is_met() -> None:
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
        "_generate_supplemental_unit_question",
        return_value=[_question()],
    ) as generate:
        supplemental = _MODULE._generate_supplemental_questions(
            codex_executable="codex",
            environment={},
            grounded_units=(ledger,),
            drafts_by_sequence={1: draft},
            existing_questions=[],
            minimum_total_questions=2,
        )

    assert len(supplemental) == 2
    assert all(question.pack_sequence == 1 for question in supplemental)
    assert generate.call_count == 2


def test_generate_supplemental_questions_gives_up_when_units_are_exhausted() -> None:
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
    already_asked = [_question().model_copy(update={"pack_sequence": 1})]

    with patch.object(_MODULE, "_generate_supplemental_unit_question") as generate:
        supplemental = _MODULE._generate_supplemental_questions(
            codex_executable="codex",
            environment={},
            grounded_units=(ledger,),
            drafts_by_sequence={1: draft},
            existing_questions=already_asked,
            minimum_total_questions=5,
        )

    assert supplemental == []
    generate.assert_not_called()


def test_generate_supplemental_questions_targets_the_densest_unit_first() -> None:
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
        "_generate_supplemental_unit_question",
        return_value=[_question()],
    ) as generate:
        supplemental = _MODULE._generate_supplemental_questions(
            codex_executable="codex",
            environment={},
            grounded_units=(sparse_ledger, dense_ledger),
            drafts_by_sequence=drafts_by_sequence,
            existing_questions=[],
            minimum_total_questions=1,
        )

    assert len(supplemental) == 1
    assert supplemental[0].pack_sequence == 2
    assert generate.call_args.kwargs["unit_title"] == "Densa"


class TestRunShufflesOptionsBeforeWriting:
    def test_written_exam_json_is_not_still_clustered_on_the_drafted_letter(
        self, tmp_path: Path
    ) -> None:
        """Regression for the audited real-world defect: Codex habitually
        drafts the correct option first. `_run` must reassign option
        letters before persisting exam.json, not just before some other
        step -- proven end to end by writing real drafts/ledgers to disk,
        stubbing out the two Codex-calling functions to return
        deliberately unshuffled ("A" always correct) questions, running
        `_run` for real, and reading back the file it actually wrote."""
        lecture_id = "lecture-shuffle"
        ledgers_root = tmp_path / "ledgers"
        drafts_root = tmp_path / "drafts"
        output_root = tmp_path / "exams"
        (ledgers_root / lecture_id).mkdir(parents=True)
        (drafts_root / lecture_id).mkdir(parents=True)

        for sequence in range(1, 9):
            ledger = SemanticLedger(
                lecture_id=lecture_id,
                pack_sequence=sequence,
                facts=[_fact("Estrutura", sequence)],
            )
            (ledgers_root / lecture_id / f"ledger_{sequence:03d}.json").write_text(
                ledger.model_dump_json()
            )
            draft = LearningUnitDraft(
                lecture_id=lecture_id,
                pack_sequence=sequence,
                title=f"Unidade {sequence}",
                body_markdown="corpo",
                clinical_warnings=[],
            )
            (drafts_root / lecture_id / f"draft_{sequence:03d}.json").write_text(
                draft.model_dump_json()
            )

        args = argparse.Namespace(
            lecture_id=lecture_id,
            ledgers_root=ledgers_root,
            drafts_root=drafts_root,
            output_root=output_root,
            codex_executable="codex",
            questions_per_unit_cap=1,
            minimum_total_questions=1,
        )

        with (
            patch.object(_MODULE, "resolve_trusted_executable", return_value="codex"),
            patch.object(
                _MODULE,
                "_generate_unit_questions",
                return_value=[_question()],
            ),
            patch.object(_MODULE, "_generate_supplemental_questions", return_value=[]),
        ):
            exit_code = _MODULE._run(args)

        assert exit_code == 0
        written = json.loads(
            (output_root / lecture_id / "exam.json").read_text(encoding="utf-8")
        )
        correct_letters = {
            question["correct_answer"] for question in written["questions"]
        }
        assert correct_letters != {"A"}, (
            "every question is still correct-at-A: _run did not shuffle "
            "before writing exam.json"
        )
