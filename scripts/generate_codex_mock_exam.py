#!/usr/bin/env python3
"""Generate one lecture's personal-study mock exam (ADR 0024's uncertified
personal-study route) through the LLM gateway (ADR 0046), grounded per
learning unit.

Questions are drawn only from critic-approved units (ADR 0045): the unit
titles come from the post-repair content of
`workspace/data/09_validated_units/<lecture>/unit_*.json`, never from the
Phase 6 `08_drafts` text, and a lecture with any unit whose critic review
did not pass, any Phase 6 draft the critic never reviewed, or a gap in its
pack sequence is blocked as a whole before any gateway is built.

Execution is lecture-scoped (D-12): this script takes exactly one lecture
identifier, has no batch mode, and never discovers over the ledgers root.

Question count is not a fixed target (2026-09-15 operator decision): each
unit asks for one question per grounded clinical fact in that unit's own
Semantic Ledger, capped per unit (`--questions-per-unit-cap`) to avoid
near-duplicate questions from a single dense unit. If the resulting total
falls short of the floor (`--minimum-total-questions`), the densest units
are asked for one additional question each, in descending fact-count
order, until the floor is met.

The adapter selected by the gateway configuration (ADR 0046) owns the child
process, its environment and its scratch directory; this script holds no
process code. The JSON schema is embedded as text in the prompt rather than
passed to the adapter: `MockQuestion.options` is `dict[str, str]`, which an
earlier session found made a structured-output flag choke. Each run writes a
metadata-only run record under
`workspace/data/llm_runs/<lecture_id>/exams.json`.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from lecture_study.assembly.assembler import load_critic_approved_drafts
from lecture_study.cli.llm_gateway_wiring import open_stage_gateway
from lecture_study.domain.errors import (
    LectureNotFoundError,
    MissingSequenceError,
    UnresolvedReviewError,
)
from lecture_study.domain.models import (
    ArtifactExecutionMode,
    LearningUnitDraft,
    MockExam,
    MockQuestion,
    SemanticFact,
    SemanticLedger,
)
from lecture_study.exams.option_shuffle import shuffle_question_options
from lecture_study.llm import LlmGateway, LlmGatewayError, Stage, complete_json
from lecture_study.llm.run_record import LLM_RUNS_ROOT, record_llm_run
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    parse_llm_json_response,
)
from lecture_study.quality.student_facing_text import (
    describe_student_facing_text_defects,
    student_facing_text_defects,
)

DEFAULT_LEDGERS_ROOT = Path("workspace/data/07_semantic_ledgers")
DEFAULT_DRAFTS_ROOT = Path("workspace/data/08_drafts")
DEFAULT_VALIDATED_UNITS_ROOT = Path("workspace/data/09_validated_units")
DEFAULT_OUTPUT_ROOT = Path("workspace/data/14_mock_exams")
_CRITIC_GATE_ERRORS = (
    LectureNotFoundError,
    MissingSequenceError,
    UnresolvedReviewError,
)
_DEFAULT_QUESTIONS_PER_UNIT_CAP = 5
_DEFAULT_MINIMUM_TOTAL_QUESTIONS = 20
_QUESTION_ARRAY_ADAPTER: TypeAdapter[list[MockQuestion]] = TypeAdapter(
    list[MockQuestion]
)
_QUESTION_ARRAY_SCHEMA = json.dumps(_QUESTION_ARRAY_ADAPTER.json_schema())

_EXAM_INSTRUCTION = (
    "You are an expert veterinary professor writing clinical mock-exam "
    "questions for one learning unit of a lecture. Use ONLY the clinical "
    "facts listed below as your source of truth: NEVER use outside "
    "clinical knowledge, and never invent a fact not listed. Produce "
    "exactly {question_count} multiple-choice questions, each a short "
    "clinical scenario grounded in a DIFFERENT one of the facts below, "
    'with four options keyed "A"-"D", the correct option\'s letter, and '
    "a clinical explanation grounded in that same fact. "
    "Write every textual output field -- scenario, options and "
    "explanation -- entirely in Brazilian Portuguese (pt-BR), with no "
    "English words and no sentence mixing two languages. "
    "Write for a student who never sees this instruction: a question, "
    "option or explanation states its clinical facts directly and never "
    "refers to the lecture material, the source text, the transcript, "
    "the slides, this instruction, or any internal name for them. A "
    'referring phrase such as "segundo o material", "conforme o '
    'texto", "mencionado no material" or "de acordo com a aula" '
    "makes the question unusable: state the clinical fact itself "
    "instead. "
    "Output valid JSON matching the provided schema: a JSON array of "
    "question objects, nothing else.\n\n"
    "Learning unit: {unit_title}\n\n"
    "Clinical facts:\n{facts}\n\n"
    "JSON Schema (the response MUST validate against this schema "
    "exactly):\n{schema}\n\n"
    "Respond with ONLY the JSON array matching the schema above -- no "
    "prose, no markdown fences."
)

_SUPPLEMENTAL_INSTRUCTION = (
    "You are an expert veterinary professor writing one additional "
    "clinical mock-exam question for a learning unit that already has "
    "questions. Use ONLY the clinical facts listed below as your source "
    "of truth: NEVER use outside clinical knowledge. Produce exactly ONE "
    "multiple-choice question, grounded in a fact from the list below "
    "that is NOT already covered by the existing questions, with four "
    'options keyed "A"-"D", the correct option\'s letter, and a '
    "clinical explanation grounded in that fact. "
    "Write every textual output field entirely in Brazilian Portuguese "
    "(pt-BR). Write for a student who never sees this instruction: never "
    "refer to the lecture material, the source text, the transcript, the "
    "slides, this instruction, or any internal name for them. "
    "Output valid JSON matching the provided schema: a JSON array "
    "containing exactly one question object, nothing else.\n\n"
    "Learning unit: {unit_title}\n\n"
    "Clinical facts:\n{facts}\n\n"
    "Questions already written for this unit (write about a different "
    "fact):\n{existing_questions}\n\n"
    "JSON Schema (the response MUST validate against this schema "
    "exactly):\n{schema}\n\n"
    "Respond with ONLY the JSON array matching the schema above -- no "
    "prose, no markdown fences."
)


def main() -> int:
    args = _build_argument_parser().parse_args()
    try:
        return _run(args)
    except _CRITIC_GATE_ERRORS as error:
        print(
            f"error: aula bloqueada pelo gate do crítico (ADR 0045), simulado não gerado: {error}",
            file=sys.stderr,
        )
        return 1
    except (
        LlmResponseContractError,
        LlmGatewayError,
        FileNotFoundError,
        ValidationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    drafts = load_critic_approved_drafts(
        args.validated_units_root, args.drafts_root, args.lecture_id
    )
    ledgers = _load_semantic_ledgers(args.ledgers_root, args.lecture_id)
    drafts_by_sequence = {draft.pack_sequence: draft for draft in drafts}
    grounded_units = tuple(
        ledger
        for ledger in sorted(ledgers, key=lambda ledger: ledger.pack_sequence)
        if ledger.facts and ledger.pack_sequence in drafts_by_sequence
    )

    gateway = open_stage_gateway(
        Stage.EXAMS,
        lecture_id=args.lecture_id,
        codex_executable_override=args.codex_executable,
    )
    with record_llm_run(
        lecture_id=args.lecture_id,
        script_stage=Stage.EXAMS,
        schema_name="list[MockQuestion]",
        root=args.llm_runs_root,
    ) as recorder:
        questions = _generate_all_questions(
            gateway=recorder.track(gateway, stage=Stage.EXAMS),
            grounded_units=grounded_units,
            drafts_by_sequence=drafts_by_sequence,
            questions_per_unit_cap=args.questions_per_unit_cap,
            minimum_total_questions=args.minimum_total_questions,
        )
        # The record closes as succeeded only after the exam is built and
        # written, so a failure below never leaves a succeeded record next to
        # a missing or stale exam.
        output_path = _write_exam(args.lecture_id, args.output_root, questions)
    print(f"gerado(s) {len(questions)} pergunta(s) para a aula {args.lecture_id!r}")
    print(f"escrito em {output_path}")
    return 0


def _write_exam(
    lecture_id: str, output_root: Path, questions: list[MockQuestion]
) -> Path:
    # The model habitually drafts the correct option first (audited
    # 2026-09-18: 61% at letter "A", 5% at "D", across 2172 questions) --
    # reassigning each question's option letters uniformly at random,
    # after generation, is the one place this is fixed for every exam
    # regardless of which machine ran this script.
    random_source = random.Random()
    exam = MockExam(
        lecture_id=lecture_id,
        questions=[
            shuffle_question_options(question, random_source) for question in questions
        ],
        execution_mode=ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY,
    )
    output_path = output_root / lecture_id / "exam.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(exam.model_dump_json(indent=2), encoding="utf-8")
    return output_path


def _generate_all_questions(
    *,
    gateway: LlmGateway,
    grounded_units: tuple[SemanticLedger, ...],
    drafts_by_sequence: dict[int, LearningUnitDraft],
    questions_per_unit_cap: int,
    minimum_total_questions: int,
) -> list[MockQuestion]:
    questions: list[MockQuestion] = []
    for ledger in grounded_units:
        draft = drafts_by_sequence[ledger.pack_sequence]
        question_count = min(len(ledger.facts), questions_per_unit_cap)
        unit_questions = _generate_unit_questions(
            gateway=gateway,
            unit_title=draft.title,
            facts=ledger.facts,
            question_count=question_count,
        )
        questions.extend(
            question.model_copy(update={"pack_sequence": ledger.pack_sequence})
            for question in unit_questions
        )
        print(
            f"unidade {ledger.pack_sequence} ({draft.title!r}): "
            f"{len(unit_questions)} pergunta(s)"
        )

    questions.extend(
        _generate_supplemental_questions(
            gateway=gateway,
            grounded_units=grounded_units,
            drafts_by_sequence=drafts_by_sequence,
            existing_questions=questions,
            minimum_total_questions=minimum_total_questions,
        )
    )
    return questions


def _generate_supplemental_questions(
    *,
    gateway: LlmGateway,
    grounded_units: tuple[SemanticLedger, ...],
    drafts_by_sequence: dict[int, LearningUnitDraft],
    existing_questions: list[MockQuestion],
    minimum_total_questions: int,
) -> list[MockQuestion]:
    """Ask the densest units for one more question each, in descending
    fact-count order, until the exam-wide floor is met."""

    supplemental: list[MockQuestion] = []
    densest_first = sorted(
        grounded_units, key=lambda ledger: len(ledger.facts), reverse=True
    )
    while len(existing_questions) + len(supplemental) < minimum_total_questions:
        remaining_capacity = False
        for ledger in densest_first:
            if len(existing_questions) + len(supplemental) >= minimum_total_questions:
                break
            unit_existing = [
                question
                for question in (*existing_questions, *supplemental)
                if question.pack_sequence == ledger.pack_sequence
            ]
            if len(unit_existing) >= len(ledger.facts):
                continue
            remaining_capacity = True
            draft = drafts_by_sequence[ledger.pack_sequence]
            [extra_question] = _generate_supplemental_unit_question(
                gateway=gateway,
                unit_title=draft.title,
                facts=ledger.facts,
                existing_questions=unit_existing,
            )
            supplemental.append(
                extra_question.model_copy(
                    update={"pack_sequence": ledger.pack_sequence}
                )
            )
        if not remaining_capacity:
            break
    return supplemental


def _generate_unit_questions(
    *,
    gateway: LlmGateway,
    unit_title: str,
    facts: Sequence[SemanticFact],
    question_count: int,
) -> list[MockQuestion]:
    prompt = _EXAM_INSTRUCTION.format(
        question_count=question_count,
        unit_title=unit_title,
        facts=_format_facts(facts),
        schema=_QUESTION_ARRAY_SCHEMA,
    )
    return _request_questions(
        gateway=gateway, prompt=prompt, expected_count=question_count
    )


def _generate_supplemental_unit_question(
    *,
    gateway: LlmGateway,
    unit_title: str,
    facts: Sequence[SemanticFact],
    existing_questions: list[MockQuestion],
) -> list[MockQuestion]:
    prompt = _SUPPLEMENTAL_INSTRUCTION.format(
        unit_title=unit_title,
        facts=_format_facts(facts),
        existing_questions="\n".join(
            f"- {question.question_text}" for question in existing_questions
        ),
        schema=_QUESTION_ARRAY_SCHEMA,
    )
    return _request_questions(gateway=gateway, prompt=prompt, expected_count=1)


def _request_questions(
    *, gateway: LlmGateway, prompt: str, expected_count: int
) -> list[MockQuestion]:
    """Request questions; the student guard and the count check run inside
    the gateway's single bounded retry."""

    return complete_json(
        gateway,
        stage=Stage.EXAMS,
        prompt=prompt,
        parse=_parse_question_array,
        validate=lambda questions: _validate_questions(questions, expected_count),
    )


def _validate_questions(questions: list[MockQuestion], expected_count: int) -> None:
    _reject_questions_unfit_for_a_student(questions)
    if len(questions) != expected_count:
        raise LlmResponseContractError(
            f"expected {expected_count} question(s), got {len(questions)}",
            retry_category="contract",
        )


def _reject_questions_unfit_for_a_student(
    questions: Sequence[MockQuestion],
) -> None:
    """Fail the completion when any student-facing exam text is not pt-BR
    or names an internal pipeline abstraction. ADR 0027's own evidence is
    that prompt wording alone is not enforcement -- lecture 10002's first
    real exam shipped English questions with an "Evidence Pack" leak --
    so the parsed output runs back through the detector before
    acceptance, inside the same bounded retry path as the other contract
    checks (mirroring the podcast script's own listener guard)."""
    passages: list[tuple[str, str]] = []
    for index, question in enumerate(questions, start=1):
        passages.append((f"question {index} stem", question.question_text))
        for letter, option in sorted(question.options.items()):
            passages.append((f"question {index} option {letter}", option))
        passages.append((f"question {index} explanation", question.explanation))
    defects = student_facing_text_defects(passages)
    if defects:
        raise LlmResponseContractError(
            "exam text is unfit for a student: "
            f"{describe_student_facing_text_defects(defects)}",
            retry_category="contract",
        )


def _parse_question_array(output_bytes: bytes) -> list[MockQuestion]:
    return parse_llm_json_response(
        output_bytes,
        expected_kind="array",
        response_adapter=_QUESTION_ARRAY_ADAPTER,
        declared_type=list[MockQuestion],
    )


def _format_facts(facts: Sequence[SemanticFact]) -> str:
    return "\n".join(
        f"- {fact.entity_name} ({fact.category}): {fact.clinical_context}"
        for fact in facts
    )


def _load_semantic_ledgers(
    ledgers_root: Path,
    lecture_id: str,
) -> tuple[SemanticLedger, ...]:
    lecture_dir = ledgers_root / lecture_id
    ledger_paths = sorted(lecture_dir.glob("ledger_*.json"))
    if not ledger_paths:
        raise FileNotFoundError(f"nenhum ledger semântico encontrado em {lecture_dir}")
    return tuple(
        SemanticLedger.model_validate_json(path.read_text()) for path in ledger_paths
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gera o simulado de estudo pessoal de uma aula via gateway LLM, "
            "com perguntas fundamentadas por unidade de aprendizagem."
        )
    )
    parser.add_argument("lecture_id")
    parser.add_argument("--ledgers-root", type=Path, default=DEFAULT_LEDGERS_ROOT)
    parser.add_argument(
        "--validated-units-root",
        type=Path,
        default=DEFAULT_VALIDATED_UNITS_ROOT,
        help=(
            "Unidades aprovadas pelo crítico (Fase 7), fonte do conteúdo "
            "pós-reparo usado no simulado."
        ),
    )
    parser.add_argument(
        "--drafts-root",
        type=Path,
        default=DEFAULT_DRAFTS_ROOT,
        help=(
            "Rascunhos da Fase 6, usados apenas para conferir que cada um "
            "foi revisado pelo crítico na versão atual."
        ),
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--codex-executable",
        default=None,
        help=(
            "sobrescreve `codex.executable` da configuração do gateway "
            "(padrão: o valor da configuração)"
        ),
    )
    parser.add_argument("--llm-runs-root", type=Path, default=LLM_RUNS_ROOT)
    parser.add_argument(
        "--questions-per-unit-cap",
        type=int,
        default=_DEFAULT_QUESTIONS_PER_UNIT_CAP,
    )
    parser.add_argument(
        "--minimum-total-questions",
        type=int,
        default=_DEFAULT_MINIMUM_TOTAL_QUESTIONS,
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
