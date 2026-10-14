"""Unit tests for the personal-study mock-exam generator on the LLM gateway
(ADR 0046).

No model is ever called: a scripted gateway double replays canned responses.
The tests exercise fact formatting, per-unit question-count derivation, the
supplemental-backfill loop, the count-mismatch and student-guard retry path
(2026-09-15 security+code review findings), the critic gate that must run
before any gateway is built (ADR 0045), the run record and the import
boundary that keeps process code out of the script."""

from __future__ import annotations

import argparse
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
    MockQuestion,
    SemanticFact,
    SemanticLedger,
    ValidatedLearningUnit,
)
from lecture_study.llm import Stage


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


def _question_array_json(*questions: MockQuestion) -> bytes:
    return json.dumps(
        [question.model_dump(mode="json") for question in questions]
    ).encode("utf-8")


def test_request_questions_raises_after_two_count_mismatches(
    make_scripted_gateway,
) -> None:
    wrong_count = _question_array_json(_question(), _question())
    gateway = make_scripted_gateway([wrong_count, wrong_count])

    with pytest.raises(_MODULE.LlmResponseContractError, match="expected 1 question"):
        _MODULE._request_questions(gateway=gateway, prompt="prompt", expected_count=1)

    assert len(gateway.requests) == 2
    assert all(request.stage is Stage.EXAMS for request in gateway.requests)


def test_request_questions_retries_once_with_feedback_after_a_count_mismatch(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway(
        [
            _question_array_json(_question(), _question()),
            _question_array_json(_question()),
        ]
    )

    questions = _MODULE._request_questions(
        gateway=gateway, prompt="prompt", expected_count=1
    )

    assert len(questions) == 1
    assert [request.is_retry for request in gateway.requests] == [False, True]
    assert "Retry feedback:" in gateway.requests[1].prompt
    assert "Retry feedback:" not in gateway.requests[0].prompt


def test_request_questions_retries_when_the_student_guard_rejects_the_text(
    make_scripted_gateway,
) -> None:
    english = _question("A 6-year-old dog presents with a dermal mass.")
    gateway = make_scripted_gateway(
        [_question_array_json(english), _question_array_json(_question())]
    )

    questions = _MODULE._request_questions(
        gateway=gateway, prompt="prompt", expected_count=1
    )

    assert questions[0].question_text == "Pergunta?"
    assert len(gateway.requests) == 2
    assert "Retry feedback:" in gateway.requests[1].prompt


def test_generate_supplemental_questions_stops_once_floor_is_met(
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
        "_generate_supplemental_unit_question",
        return_value=[_question()],
    ) as generate:
        supplemental = _MODULE._generate_supplemental_questions(
            gateway=make_scripted_gateway([]),
            grounded_units=(ledger,),
            drafts_by_sequence={1: draft},
            existing_questions=[],
            minimum_total_questions=2,
        )

    assert len(supplemental) == 2
    assert all(question.pack_sequence == 1 for question in supplemental)
    assert generate.call_count == 2


def test_generate_supplemental_questions_gives_up_when_units_are_exhausted(
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
    already_asked = [_question().model_copy(update={"pack_sequence": 1})]

    with patch.object(_MODULE, "_generate_supplemental_unit_question") as generate:
        supplemental = _MODULE._generate_supplemental_questions(
            gateway=make_scripted_gateway([]),
            grounded_units=(ledger,),
            drafts_by_sequence={1: draft},
            existing_questions=already_asked,
            minimum_total_questions=5,
        )

    assert supplemental == []
    generate.assert_not_called()


def test_generate_supplemental_questions_targets_the_densest_unit_first(
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
        "_generate_supplemental_unit_question",
        return_value=[_question()],
    ) as generate:
        supplemental = _MODULE._generate_supplemental_questions(
            gateway=make_scripted_gateway([]),
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
        llm_runs_root=root / "llm_runs",
        codex_executable=None,
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


def _command_line(args: argparse.Namespace) -> list[str]:
    return [
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
        "--llm-runs-root",
        str(args.llm_runs_root),
    ]


class TestRunUsesOnlyCriticApprovedUnits:
    def test_a_unit_that_failed_the_critic_blocks_the_whole_lecture(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        args = _write_lecture_inputs(
            tmp_path, "lecture-blocked", unit_count=3, failing_sequences=frozenset({2})
        )
        opened_stages = _open_scripted_gateway(monkeypatch, object())

        with patch.object(sys, "argv", _command_line(args)):
            exit_code = _MODULE.main()

        assert exit_code == 1
        assert opened_stages == []
        assert not (args.output_root / args.lecture_id / "exam.json").exists()
        assert not args.llm_runs_root.exists()
        assert "aula bloqueada pelo gate do crítico" in capsys.readouterr().err

    def test_questions_are_grounded_in_the_post_repair_unit_not_the_draft(
        self, tmp_path: Path, make_scripted_gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = _write_lecture_inputs(tmp_path, "lecture-repaired", unit_count=2)
        _open_scripted_gateway(monkeypatch, make_scripted_gateway([]))

        with (
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


class TestRunThroughTheGateway:
    def test_two_approved_units_produce_an_uncertified_exam_and_a_run_record(
        self, tmp_path: Path, make_scripted_gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lecture_id = "lecture-tracer"
        args = _write_lecture_inputs(tmp_path, lecture_id, unit_count=2)
        gateway = make_scripted_gateway(
            [_question_array_json(_question()), _question_array_json(_question())]
        )
        opened_stages = _open_scripted_gateway(monkeypatch, gateway)

        exit_code = _MODULE._run(args)

        assert exit_code == 0
        assert opened_stages == [Stage.EXAMS]
        exam = json.loads(
            (args.output_root / lecture_id / "exam.json").read_text(encoding="utf-8")
        )
        assert exam["execution_mode"] == (
            ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY.value
        )
        assert len(exam["questions"]) == 2
        record = json.loads(
            (args.llm_runs_root / lecture_id / "exams.json").read_text(encoding="utf-8")
        )
        assert record["status"] == "succeeded"
        assert record["script_stage"] == "exams"
        assert record["gateways"][0]["calls"] == len(gateway.requests) == 2

    def test_a_gateway_that_never_answers_a_valid_array_leaves_no_exam(
        self,
        tmp_path: Path,
        make_scripted_gateway,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = _write_lecture_inputs(tmp_path, "lecture-bad", unit_count=1)
        _open_scripted_gateway(monkeypatch, make_scripted_gateway([b"[]", b"[]"]))
        monkeypatch.setattr(sys, "argv", _command_line(args))

        exit_code = _MODULE.main()

        assert exit_code == 1
        assert not (args.output_root / args.lecture_id / "exam.json").exists()
        record = json.loads(
            (args.llm_runs_root / args.lecture_id / "exams.json").read_text(
                encoding="utf-8"
            )
        )
        assert record["status"] == "failed"
        assert "expected 1 question" in capsys.readouterr().err


class TestRunRecordClosesAfterTheExamIsWritten:
    def test_an_exam_write_failure_leaves_a_failed_run_record(
        self, tmp_path: Path, make_scripted_gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = _write_lecture_inputs(tmp_path, "lecture-write", unit_count=1)
        _open_scripted_gateway(
            monkeypatch, make_scripted_gateway([_question_array_json(_question())])
        )

        def failing_write(*_arguments: object) -> Path:
            raise FileNotFoundError("output tree unavailable")

        monkeypatch.setattr(_MODULE, "_write_exam", failing_write)
        monkeypatch.setattr(sys, "argv", _command_line(args))

        exit_code = _MODULE.main()

        record = json.loads(
            (args.llm_runs_root / args.lecture_id / "exams.json").read_text(
                encoding="utf-8"
            )
        )
        assert exit_code == 1
        assert record["status"] == "failed"


class TestRunShufflesOptionsBeforeWriting:
    def test_written_exam_json_is_not_still_clustered_on_the_drafted_letter(
        self, tmp_path: Path, make_scripted_gateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for the audited real-world defect: the model habitually
        drafts the correct option first. `_run` must reassign option
        letters before persisting exam.json, not just before some other
        step -- proven end to end by writing real drafts/ledgers to disk,
        stubbing out the two question-generating functions to return
        deliberately unshuffled ("A" always correct) questions, running
        `_run` for real, and reading back the file it actually wrote."""
        lecture_id = "lecture-shuffle"
        args = _write_lecture_inputs(tmp_path, lecture_id, unit_count=8)
        _open_scripted_gateway(monkeypatch, make_scripted_gateway([]))

        with (
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
            (args.output_root / lecture_id / "exam.json").read_text(encoding="utf-8")
        )
        correct_letters = {
            question["correct_answer"] for question in written["questions"]
        }
        assert correct_letters != {"A"}, (
            "every question is still correct-at-A: _run did not shuffle "
            "before writing exam.json"
        )


def test_main_refuses_a_malformed_lecture_id_before_opening_a_gateway(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = [
        "generate_codex_mock_exam.py",
        "../escape",
        "--drafts-root",
        str(tmp_path / "drafts"),
        "--validated-units-root",
        str(tmp_path / "validated"),
    ]
    opened_stages = _open_scripted_gateway(monkeypatch, object())

    with patch.object(sys, "argv", argv):
        exit_code = _MODULE.main()

    assert exit_code == 1
    assert opened_stages == []
    assert capsys.readouterr().err.startswith("error: ")


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
