#!/usr/bin/env python3
"""Generate one lecture's personal-study mock exam (ADR 0024's uncertified
personal-study route) with the Codex CLI (`codex exec`), grounded per
learning unit.

Execution is lecture-scoped (D-12): this script takes exactly one lecture
identifier, has no batch mode, and never discovers over the ledgers root.

Question count is not a fixed target (2026-09-15 operator decision): each
unit asks for one question per grounded clinical fact in that unit's own
Semantic Ledger, capped per unit (`--questions-per-unit-cap`) to avoid
near-duplicate questions from a single dense unit. If the resulting total
falls short of the floor (`--minimum-total-questions`), the densest units
are asked for one additional question each, in descending fact-count
order, until the floor is met.

Codex authenticates via the operator's ChatGPT subscription login (not an
API key), so `sanitized_child_process_environment()` stripping ambient
credentials from the child process is safe: Codex does not read one from
the environment here. `--output-schema` is deliberately not used --
`MockQuestion.options` is `dict[str, str]`, which an earlier session
found made that flag choke; the schema is embedded as text in the prompt
instead, matching `exams/generator.py`'s own local-LLM approach.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

from lecture_study.domain.models import (
    ArtifactExecutionMode,
    LearningUnitDraft,
    MockExam,
    MockQuestion,
    SemanticFact,
    SemanticLedger,
)
from lecture_study.exams.option_shuffle import shuffle_question_options
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    build_bounded_retry_prompt_section,
    build_llm_retry_feedback,
    parse_llm_json_response,
)
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import run_bounded_process
from lecture_study.quality.student_facing_text import (
    describe_student_facing_text_defects,
    student_facing_text_defects,
)

DEFAULT_LEDGERS_ROOT = Path("workspace/data/07_semantic_ledgers")
DEFAULT_DRAFTS_ROOT = Path("workspace/data/08_drafts")
DEFAULT_OUTPUT_ROOT = Path("workspace/data/14_mock_exams")
_DEFAULT_QUESTIONS_PER_UNIT_CAP = 5
_DEFAULT_MINIMUM_TOTAL_QUESTIONS = 20
_SUBPROCESS_TIMEOUT_SECONDS = 900.0
_MAXIMUM_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
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
    except (LlmResponseContractError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    codex_executable = resolve_trusted_executable(args.codex_executable)
    environment = dict(sanitized_child_process_environment())
    # HOME is not on sanitized_child_process_environment()'s inherited
    # whitelist (it only forwards locale/PATH/temp-dir variables), but the
    # codex CLI needs it to locate its ChatGPT-subscription auth session
    # under ~/.codex. Restoring it here (a plain path, not a credential
    # itself) is scoped to this one call, not the shared whitelist every
    # other subprocess caller in this codebase relies on.
    home_directory = os.environ.get("HOME")
    if home_directory is not None:
        environment["HOME"] = home_directory

    drafts = _load_drafts(args.drafts_root, args.lecture_id)
    ledgers = _load_semantic_ledgers(args.ledgers_root, args.lecture_id)
    drafts_by_sequence = {draft.pack_sequence: draft for draft in drafts}

    grounded_units = tuple(
        ledger
        for ledger in sorted(ledgers, key=lambda ledger: ledger.pack_sequence)
        if ledger.facts and ledger.pack_sequence in drafts_by_sequence
    )

    questions: list[MockQuestion] = []
    for ledger in grounded_units:
        draft = drafts_by_sequence[ledger.pack_sequence]
        question_count = min(len(ledger.facts), args.questions_per_unit_cap)
        unit_questions = _generate_unit_questions(
            codex_executable=codex_executable,
            environment=environment,
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
            codex_executable=codex_executable,
            environment=environment,
            grounded_units=grounded_units,
            drafts_by_sequence=drafts_by_sequence,
            existing_questions=questions,
            minimum_total_questions=args.minimum_total_questions,
        )
    )

    # The model habitually drafts the correct option first (audited
    # 2026-09-18: 61% at letter "A", 5% at "D", across 2172 questions) --
    # reassigning each question's option letters uniformly at random,
    # after generation, is the one place this is fixed for every exam
    # regardless of which machine ran this script.
    random_source = random.Random()
    questions = [
        shuffle_question_options(question, random_source) for question in questions
    ]
    exam = MockExam(
        lecture_id=args.lecture_id,
        questions=questions,
        execution_mode=ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY,
    )
    output_path = args.output_root / args.lecture_id / "exam.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(exam.model_dump_json(indent=2), encoding="utf-8")
    print(f"gerado(s) {len(questions)} pergunta(s) para a aula {args.lecture_id!r}")
    print(f"escrito em {output_path}")
    return 0


def _generate_supplemental_questions(
    *,
    codex_executable: str,
    environment: dict[str, str],
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
                codex_executable=codex_executable,
                environment=environment,
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
    codex_executable: str,
    environment: dict[str, str],
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
    return _run_codex_for_questions(
        codex_executable=codex_executable,
        environment=environment,
        prompt=prompt,
        expected_count=question_count,
    )


def _generate_supplemental_unit_question(
    *,
    codex_executable: str,
    environment: dict[str, str],
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
    return _run_codex_for_questions(
        codex_executable=codex_executable,
        environment=environment,
        prompt=prompt,
        expected_count=1,
    )


def _run_codex_for_questions(
    *,
    codex_executable: str,
    environment: dict[str, str],
    prompt: str,
    expected_count: int,
) -> list[MockQuestion]:
    try:
        return _call_codex_for_questions(
            codex_executable=codex_executable,
            environment=environment,
            prompt=prompt,
            expected_count=expected_count,
        )
    except LlmResponseContractError as error:
        retry_prompt = prompt + build_bounded_retry_prompt_section(
            heading="Retry feedback:",
            feedback=build_llm_retry_feedback(error),
        )
        return _call_codex_for_questions(
            codex_executable=codex_executable,
            environment=environment,
            prompt=retry_prompt,
            expected_count=expected_count,
        )


def _reject_questions_unfit_for_a_student(
    questions: Sequence[MockQuestion],
) -> None:
    """Fail the codex call when any student-facing exam text is not pt-BR
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


def _call_codex_for_questions(
    *,
    codex_executable: str,
    environment: dict[str, str],
    prompt: str,
    expected_count: int,
) -> list[MockQuestion]:
    # The subprocess's cwd (and the `-C` working root handed to codex) is a
    # fresh, empty scratch directory, never the repository checkout: the
    # prompt is fully self-contained (facts are inlined as text), the
    # process has no functional need to read any repository file, and the
    # repository root holds `.env` (2026-09-15 security review finding --
    # least-privilege, and a defense line against a prompt-injection
    # payload in lecture content asking the model to read and echo a local
    # file).
    with tempfile.TemporaryDirectory(prefix="codex-mock-exam-") as raw_scratch_dir:
        scratch_dir = Path(raw_scratch_dir)
        output_path = scratch_dir / "response.txt"
        run_bounded_process(
            [
                codex_executable,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-C",
                str(scratch_dir),
                "-o",
                str(output_path),
                prompt,
            ],
            env=environment,
            cwd=scratch_dir,
            timeout_seconds=_SUBPROCESS_TIMEOUT_SECONDS,
            maximum_stdout_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
            maximum_stderr_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
            check=True,
        )
        output_bytes = output_path.read_bytes()
    questions = _parse_question_array(output_bytes)
    _reject_questions_unfit_for_a_student(questions)
    if len(questions) != expected_count:
        raise LlmResponseContractError(
            f"expected {expected_count} question(s), got {len(questions)}",
            retry_category="contract",
        )
    return questions


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


def _load_drafts(
    drafts_root: Path,
    lecture_id: str,
) -> tuple[LearningUnitDraft, ...]:
    lecture_dir = drafts_root / lecture_id
    draft_paths = sorted(lecture_dir.glob("draft_*.json"))
    if not draft_paths:
        raise FileNotFoundError(f"nenhum rascunho encontrado em {lecture_dir}")
    return tuple(
        LearningUnitDraft.model_validate_json(path.read_text()) for path in draft_paths
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
            "Gera o simulado de estudo pessoal de uma aula via Codex CLI, "
            "com perguntas fundamentadas por unidade de aprendizagem."
        )
    )
    parser.add_argument("lecture_id")
    parser.add_argument("--ledgers-root", type=Path, default=DEFAULT_LEDGERS_ROOT)
    parser.add_argument("--drafts-root", type=Path, default=DEFAULT_DRAFTS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--codex-executable", default="codex")
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
