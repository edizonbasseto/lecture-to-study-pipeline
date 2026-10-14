"""Offline mock-exam section-title-echo diagnostic.

Scans a generated MockExam for the one known mechanical defect the
deterministic bundle template produced: a question whose text is
substantially just one of the lecture's unit titles wrapped in an
interrogative phrase, adding no genuine clinical scenario of its own.
This is a finding, never a gate -- deciding whether an exam is
acceptable stays a human judgement (the manual reading of every question
and its answer key, per 01-VALIDATION.md's LOCAL-EXAM-01 row); this
module only surfaces evidence for that reading and for the tests here.
No LLM inference, no filesystem access. Do not import this module from
exams/generator.py, exams/compiler.py, or any CLI orchestrator entry
point -- wiring it into generation would turn a reportable failure into
a silent retry loop, exactly the quiet accommodation this phase avoids.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from lecture_study.domain.models import CanonicalLectureDocument, MockExam, MockQuestion

# Generic Portuguese question-forming scaffolding: articles, prepositions,
# and the interrogative/recall verbs the deterministic template's own
# question shape used ("Em X, o que deve ser recordado sobre Y?"). This is
# not a semantic vocabulary filter -- it exists only to strip the
# grammatical wrapper around a title, never to judge clinical content.
_INTERROGATIVE_WRAPPER_WORDS = frozenset(
    {
        "a",
        "as",
        "da",
        "das",
        "de",
        "deve",
        "devem",
        "deveria",
        "do",
        "dos",
        "e",
        "em",
        "lembrado",
        "lembrar",
        "na",
        "nas",
        "no",
        "nos",
        "o",
        "os",
        "para",
        "qual",
        "quais",
        "que",
        "recordado",
        "recordar",
        "relevante",
        "sao",
        "se",
        "ser",
        "sobre",
    }
)

_NON_WORD_CHARACTERS = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RUN = re.compile(r"\s+")

# How much content, beyond the matched title's own words, a question may
# still carry and be considered "substantially the title": the
# deterministic template's own shape ("Em X, o que deve ser recordado
# sobre Y?") leaves a short topic phrase after wrapper-stripping in
# addition to the repeated title; a real clinical-scenario question
# (naming a technique, presenting two courses of action) leaves far more.
_MAX_ECHO_EXTRA_CONTENT_WORDS = 2


@dataclass(frozen=True, slots=True)
class SectionTitleEcho:
    """One question flagged as substantially echoing a unit title.

    Carries no verdict beyond the fact of the match: whether the echo
    makes the exam unacceptable stays the reader's judgement.
    """

    question_index: int
    question_text: str
    matched_title: str


def section_title_echoes(
    exam: MockExam,
    document: CanonicalLectureDocument,
) -> list[SectionTitleEcho]:
    """Report every question that is substantially one of the document's
    unit titles wrapped in an interrogative phrase.

    A question is reported when, after stripping generic question-forming
    words, its remaining content contiguously contains a unit title and
    adds at most ``_MAX_ECHO_EXTRA_CONTENT_WORDS`` words beyond it -- the
    signature of a question that restates a title instead of testing
    reasoning about it. A question that merely shares one word with a
    title while asking a real clinical-scenario question (naming a
    technique, presenting choices) keeps far more extra content than that
    bound allows, and is not reported.
    """
    titles = tuple(unit.content.title for unit in document.units)
    echoes: list[SectionTitleEcho] = []
    for question_index, question in enumerate(exam.questions):
        matched_title = _matched_title(question, titles)
        if matched_title is not None:
            echoes.append(
                SectionTitleEcho(
                    question_index=question_index,
                    question_text=question.question_text,
                    matched_title=matched_title,
                )
            )
    return echoes


def render_echo_scan_markdown(echoes: Sequence[SectionTitleEcho]) -> str:
    """Render detected echoes as a pt-BR Markdown table.

    An empty ``echoes`` sequence still renders the header -- absence of
    the defect is reported, not silently omitted.
    """
    header = "| Questão | Texto da questão | Título ecoado |"
    separator = "|---|---|---|"
    rows = [_render_echo_row(echo) for echo in _ordered_by_question(echoes)]
    return "\n".join([header, separator, *rows]) + "\n"


def _ordered_by_question(echoes: Sequence[SectionTitleEcho]) -> list[SectionTitleEcho]:
    return sorted(echoes, key=lambda echo: echo.question_index)


def _render_echo_row(echo: SectionTitleEcho) -> str:
    return (
        f"| {echo.question_index + 1} | {echo.question_text} | {echo.matched_title} |"
    )


def _matched_title(question: MockQuestion, titles: Sequence[str]) -> str | None:
    content_words = _content_words(question.question_text)
    for title in titles:
        title_words = tuple(_normalize(title).split())
        if not title_words:
            continue
        if not _contains_contiguous_subsequence(content_words, title_words):
            continue
        extra_word_count = len(content_words) - len(title_words)
        if extra_word_count <= _MAX_ECHO_EXTRA_CONTENT_WORDS:
            return title
    return None


def _content_words(text: str) -> tuple[str, ...]:
    return tuple(
        word
        for word in _normalize(text).split()
        if word not in _INTERROGATIVE_WRAPPER_WORDS
    )


def _contains_contiguous_subsequence(
    haystack: tuple[str, ...], needle: tuple[str, ...]
) -> bool:
    if not needle:
        return False
    needle_length = len(needle)
    return any(
        haystack[start : start + needle_length] == needle
        for start in range(len(haystack) - needle_length + 1)
    )


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    folded = without_accents.casefold()
    without_punctuation = _NON_WORD_CHARACTERS.sub(" ", folded)
    return _WHITESPACE_RUN.sub(" ", without_punctuation).strip()
