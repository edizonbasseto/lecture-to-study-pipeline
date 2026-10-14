"""Server-graded interactive mock exam (SITE-09).

Phase 13's `MockExam` (`exams/compiler.py`) already persists everything a
quiz needs at `workspace/data/14_mock_exams/{lecture_id}/exam.json`, but
that file carries `correct_answer` and `explanation` -- the pipeline
never reads it back for the site, which only copies the prose
`simulado.md`/`simulado.pdf` view (ADR 0011-style data/view split). This
module reads `exam.json` directly, on the same "outside the site tree,
mounted as its own directory" pattern `site.access`/`site.signing` already
use for the credentials file and the session key (ADR 0039), and never
exposes it as a static asset: only two shapes ever leave this module --
`public_view`, which strips every answer-bearing field, and
`GradedExam`, which is built strictly *after* a submission and safe to
reveal at that point.

Every question is addressed by its position (0-based) in `exam.json`'s
own `questions` list, not by a client-invented identifier -- so grading
one lecture's submission can only ever score questions that belong to
that lecture's own exam, read fresh from disk by the trusted `lecture_id`
path parameter. Nothing about which question maps to which lecture is
ever taken from the request body.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from html import escape
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from lecture_study.domain.models import MockExam, MockQuestion, SafeIdentifier

MOCK_EXAMS_DIR = Path(
    os.environ.get("LECTURE_STUDY_SITE_MOCK_EXAMS", "workspace/data/14_mock_exams")
)

# The same primitive every other reader of a per-lecture disk artifact
# validates a path component against before splicing it into a Path
# (e.g. `quality/delivery.py`'s `_LECTURE_ID_ADAPTER`) -- kept here as this
# module's one seam into that convention rather than a second, hand-rolled
# regex that could drift from it.
_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)

_ANSWER_LETTERS = ("A", "B", "C", "D")


class ExamNotFoundError(RuntimeError):
    """Raised when no compiled mock exam exists for the requested lecture,
    or the requested lecture id is not a safe path component."""


@dataclass(frozen=True, slots=True)
class PublicQuestion:
    """What a quiz-taker may see before grading: no `correct_answer`, no
    `explanation`."""

    index: int
    question_text: str
    options: dict[str, str]


@dataclass(frozen=True, slots=True)
class GradedQuestion:
    """One question's Gabarito comentado, safe to reveal only once grading
    has already happened."""

    index: int
    question_text: str
    options: dict[str, str]
    submitted_answer: str | None
    correct_answer: str
    is_correct: bool
    explanation: str


@dataclass(frozen=True, slots=True)
class GradedExam:
    """A finished grading pass over one lecture's mock exam."""

    lecture_id: str
    score: int
    total: int
    questions: tuple[GradedQuestion, ...]

    @property
    def percentage_correct(self) -> int:
        """Rounded percentage of correct answers; 0 for a degenerate
        zero-question exam rather than dividing by zero."""
        if self.total == 0:
            return 0
        return round(100 * self.score / self.total)

    @property
    def errors(self) -> int:
        return self.total - self.score

    @property
    def grade_out_of_ten(self) -> float:
        """Score on the Brazilian 0-10 school scale, one decimal place;
        0.0 for a degenerate zero-question exam rather than dividing by
        zero."""
        if self.total == 0:
            return 0.0
        return round(10 * self.score / self.total, 1)


def read_exam(lecture_id: str, exams_directory: Path = MOCK_EXAMS_DIR) -> MockExam:
    """Load one lecture's compiled `MockExam`. Raises `ExamNotFoundError`
    when the id is not a safe path component, no exam was compiled for it,
    or the file on disk no longer parses as a `MockExam` -- the same
    fail-closed shape as `LectureNotFoundError` elsewhere in this codebase,
    never a silently empty exam and never an unhandled 500 surfaced straight
    from a schema mismatch."""
    try:
        _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    except ValidationError as error:
        raise ExamNotFoundError(f"id de aula inválido: {lecture_id!r}") from error
    exam_path = exams_directory / lecture_id / "exam.json"
    if not exam_path.is_file():
        raise ExamNotFoundError(f"nenhum simulado compilado para {lecture_id!r}")
    try:
        return MockExam.model_validate_json(exam_path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise ExamNotFoundError(
            f"simulado compilado para {lecture_id!r} não pôde ser lido"
        ) from error


def public_view(exam: MockExam) -> tuple[PublicQuestion, ...]:
    """The question set a quiz-taker may see before answering: options
    only, in no case `correct_answer` or `explanation`."""
    return tuple(
        PublicQuestion(
            index=index,
            question_text=question.question_text,
            options=dict(question.options),
        )
        for index, question in enumerate(exam.questions)
    )


def grade(
    lecture_id: str, exam: MockExam, submitted_answers: dict[int, str]
) -> GradedExam:
    """Score a submission against `exam`, read fresh from disk by the
    caller for the one lecture this submission claims -- so a submitted
    answer can only ever be graded against the question at the same
    position in that same lecture's own exam, never one implied by the
    request body. `lecture_id` is the caller's already-validated path
    parameter, not `exam.lecture_id`: the two are expected to agree, but
    the result must report the id that was actually trusted to locate the
    file, not a second copy of it read back out of the file's own
    content."""
    graded_questions = tuple(
        _grade_one(index, question, submitted_answers.get(index))
        for index, question in enumerate(exam.questions)
    )
    score = sum(1 for question in graded_questions if question.is_correct)
    return GradedExam(
        lecture_id=lecture_id,
        score=score,
        total=len(graded_questions),
        questions=graded_questions,
    )


def _grade_one(
    index: int, question: MockQuestion, submitted_answer: str | None
) -> GradedQuestion:
    is_correct = submitted_answer == question.correct_answer
    return GradedQuestion(
        index=index,
        question_text=question.question_text,
        options=dict(question.options),
        submitted_answer=submitted_answer,
        correct_answer=question.correct_answer,
        is_correct=is_correct,
        explanation=question.explanation,
    )


def parse_submitted_answers(fields: dict[str, str]) -> dict[int, str]:
    """Read `q{index}` form fields into `{index: letter}`, ignoring
    anything that is not a small non-negative index or not one of the
    four declared answer letters -- a submission can only ever assert an
    answer to a question, never inject an out-of-shape value into
    grading."""
    answers: dict[int, str] = {}
    for name, value in fields.items():
        index = _parsed_question_index(name)
        if index is None:
            continue
        if value not in _ANSWER_LETTERS:
            continue
        answers[index] = value
    return answers


def _parsed_question_index(field_name: str) -> int | None:
    if not field_name.startswith("q"):
        return None
    digits = field_name[1:]
    if not digits.isdigit():
        return None
    return int(digits)


QUIZ_STYLE = """
  body { margin: 0; font-family: system-ui, sans-serif; background: #10151c; color: #e6e9ee; }
  main { max-width: 40rem; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
  h1 { font-size: 1.4rem; margin: 0 0 1.5rem; }
  fieldset { border: 1px solid #2c3745; border-radius: 10px; padding: 1rem 1.1rem; margin: 0 0 1.1rem; }
  legend { padding: 0 0.4rem; color: #e6e9ee; }
  label.option { display: block; padding: 0.35rem 0; color: #c7cdd6; }
  button { margin-top: 0.5rem; padding: 0.75rem 1.5rem; border: 0; border-radius: 8px; background: #2563eb; color: #fff; font-size: 1rem; font-weight: 600; }
  .score { font-size: 1.2rem; margin-bottom: 1.5rem; }
  .correct { color: #4ade80; }
  .wrong { color: #f87171; }
  .unanswered { color: #aab3bf; }
  .explanation { color: #aab3bf; margin-top: 0.4rem; }
"""


def render_quiz_form(lecture_id: str, questions: tuple[PublicQuestion, ...]) -> str:
    """The GET page: options only, never an answer -- grep-testable, since
    nothing here ever interpolates a `MockQuestion.correct_answer` or
    `.explanation`."""
    body = "".join(_render_question_fieldset(question) for question in questions)
    return (
        "<!DOCTYPE html>"
        '<html lang="pt-BR">'
        '<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Simulado — Pós em Veterinária</title>"
        f"<style>{QUIZ_STYLE}</style>"
        "</head><body><main>"
        "<h1>Simulado</h1>"
        f'<form method="post" action="/simulado/{escape(lecture_id)}">'
        f"{body}"
        '<button type="submit">Corrigir</button>'
        "</form></main></body></html>"
    )


def _render_question_fieldset(question: PublicQuestion) -> str:
    options = "".join(
        _render_option(question.index, letter, text)
        for letter, text in sorted(question.options.items())
    )
    return (
        "<fieldset>"
        f"<legend>{escape(question.question_text)}</legend>"
        f"{options}"
        "</fieldset>"
    )


def _render_option(question_index: int, letter: str, text: str) -> str:
    field_name = f"q{question_index}"
    input_id = f"{field_name}-{letter}"
    return (
        f'<label class="option" for="{input_id}">'
        f'<input type="radio" id="{input_id}" name="{escape(field_name)}" '
        f'value="{escape(letter)}"> {escape(letter)}) {escape(text)}'
        "</label>"
    )


def _format_grade_out_of_ten(grade: float) -> str:
    """Brazilian decimal convention: a comma, never a dot ("7,5" not
    "7.5")."""
    return f"{grade:.1f}".replace(".", ",")


def render_quiz_results(graded: GradedExam) -> str:
    """The POST response: the Gabarito comentado, safe to reveal only now
    that grading has already happened."""
    body = "".join(_render_graded_question(question) for question in graded.questions)
    return (
        "<!DOCTYPE html>"
        '<html lang="pt-BR">'
        '<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Resultado do simulado — Pós em Veterinária</title>"
        f"<style>{QUIZ_STYLE}</style>"
        "</head><body><main>"
        "<h1>Resultado</h1>"
        f'<p class="score">Nota: {graded.score} de {graded.total} '
        f"({graded.percentage_correct}% de acerto, {graded.errors} "
        f"erro{'s' if graded.errors != 1 else ''}) — "
        f"{_format_grade_out_of_ten(graded.grade_out_of_ten)} de 10</p>"
        f"{body}"
        "</main></body></html>"
    )


def _render_graded_question(question: GradedQuestion) -> str:
    verdict_class, verdict_text = _verdict(question)
    chosen_text = (
        question.options.get(question.submitted_answer, "")
        if question.submitted_answer
        else None
    )
    chosen_line = (
        f"<p>Resposta marcada: {escape(question.submitted_answer)}) "
        f"{escape(chosen_text or '')}</p>"
        if question.submitted_answer
        else "<p>Não respondida.</p>"
    )
    correct_text = question.options.get(question.correct_answer, "")
    return (
        "<fieldset>"
        f"<legend>{escape(question.question_text)}</legend>"
        f'<p class="{verdict_class}">{verdict_text}</p>'
        f"{chosen_line}"
        f"<p>Resposta correta: {escape(question.correct_answer)}) "
        f"{escape(correct_text)}</p>"
        f'<p class="explanation">{escape(question.explanation)}</p>'
        "</fieldset>"
    )


def _verdict(question: GradedQuestion) -> tuple[str, str]:
    if question.submitted_answer is None:
        return "unanswered", "Não respondida"
    if question.is_correct:
        return "correct", "Correta"
    return "wrong", "Incorreta"
