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
    CriticReview,
    LearningUnitDraft,
    MockQuestion,
    SemanticFact,
    SemanticLedger,
    ValidatedLearningUnit,
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


def _write_lecture_inputs(
    root: Path,
    lecture_id: str,
    unit_count: int,
    failing_sequences: frozenset[int] = frozenset(),
) -> argparse.Namespace:
    """Write one ledger, one Phase 6 draft and one Phase 7 validated unit
    per pack_sequence, and return the `_run` arguments pointing at them.
    The validated unit's post-repair title differs from the draft's, so a
    test can tell which of the two the script consumed."""
    roots = {name: root / name for name in ("ledgers", "drafts", "validated", "exams")}
    for name in ("ledgers", "drafts", "validated"):
        (roots[name] / lecture_id).mkdir(parents=True)

    for sequence in range(1, unit_count + 1):
        _write_unit_inputs(
            roots, lecture_id, sequence, passed=sequence not in failing_sequences
        )

    return argparse.Namespace(
        lecture_id=lecture_id,
        ledgers_root=roots["ledgers"],
        drafts_root=roots["drafts"],
        validated_units_root=roots["validated"],
        output_root=roots["exams"],
        codex_executable="codex",
        questions_per_unit_cap=1,
        minimum_total_questions=1,
    )


def _write_unit_inputs(
    roots: dict[str, Path], lecture_id: str, sequence: int, *, passed: bool
) -> None:
    ledger = SemanticLedger(
        lecture_id=lecture_id,
        pack_sequence=sequence,
        facts=[_fact("Estrutura", sequence)],
    )
    (roots["ledgers"] / lecture_id / f"ledger_{sequence:03d}.json").write_text(
        ledger.model_dump_json()
    )
    draft = LearningUnitDraft(
        lecture_id=lecture_id,
        pack_sequence=sequence,
        title=f"Rascunho {sequence}",
        body_markdown="corpo do rascunho",
        clinical_warnings=[],
    )
    (roots["drafts"] / lecture_id / f"draft_{sequence:03d}.json").write_text(
        draft.model_dump_json()
    )
    unit = ValidatedLearningUnit(
        lecture_id=lecture_id,
        pack_sequence=sequence,
        content=draft.model_copy(
            update={"title": f"Unidade {sequence}", "body_markdown": "corpo reparado"}
        ),
        review=CriticReview(
            passed=passed, hallucination_score=0.0, feedback="Fundamentado."
        ),
    )
    (roots["validated"] / lecture_id / f"unit_{sequence:03d}.json").write_text(
        unit.model_dump_json()
    )


class TestRunUsesOnlyCriticApprovedUnits:
    def test_a_unit_that_failed_the_critic_blocks_the_whole_lecture(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = _write_lecture_inputs(
            tmp_path, "lecture-blocked", unit_count=3, failing_sequences=frozenset({2})
        )
        argv = [
            "generate_codex_mock_exam.py",
            args.lecture_id,
            "--ledgers-root",
            str(args.ledgers_root),
            "--drafts-root",
            str(args.drafts_root),
            "--validated-units-root",
            str(args.validated_units_root),
            "--output-root",
            str(args.output_root),
        ]

        with (
            patch.object(sys, "argv", argv),
            patch.object(_MODULE, "resolve_trusted_executable", return_value="codex"),
            patch.object(_MODULE, "run_bounded_process") as run_codex,
        ):
            exit_code = _MODULE.main()

        assert exit_code == 1
        run_codex.assert_not_called()
        assert not (args.output_root / args.lecture_id / "exam.json").exists()
        assert "aula bloqueada pelo gate do crítico" in capsys.readouterr().err

    def test_questions_are_grounded_in_the_post_repair_unit_not_the_draft(
        self, tmp_path: Path
    ) -> None:
        args = _write_lecture_inputs(tmp_path, "lecture-repaired", unit_count=2)

        with (
            patch.object(_MODULE, "resolve_trusted_executable", return_value="codex"),
            patch.object(
                _MODULE, "_generate_unit_questions", return_value=[_question()]
            ) as generate,
            patch.object(_MODULE, "_generate_supplemental_questions", return_value=[]),
        ):
            exit_code = _MODULE._run(args)

        assert exit_code == 0
        assert [call.kwargs["unit_title"] for call in generate.call_args_list] == [
            "Unidade 1",
            "Unidade 2",
        ]


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
        args = _write_lecture_inputs(tmp_path, lecture_id, unit_count=8)
        output_root = args.output_root

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


def test_main_refuses_a_malformed_lecture_id_before_locating_codex(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [
        "generate_codex_mock_exam.py",
        "../escape",
        "--drafts-root",
        str(tmp_path / "drafts"),
        "--validated-units-root",
        str(tmp_path / "validated"),
    ]

    with (
        patch.object(sys, "argv", argv),
        patch.object(_MODULE, "resolve_trusted_executable") as locate_codex,
    ):
        exit_code = _MODULE.main()

    assert exit_code == 1
    locate_codex.assert_not_called()
    assert capsys.readouterr().err.startswith("error: ")
