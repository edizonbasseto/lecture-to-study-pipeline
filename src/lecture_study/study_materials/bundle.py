"""Build and present a complete personal study bundle in pt-BR."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, replace
from heapq import nlargest
from io import StringIO
from itertools import batched, chain
from typing import Final, Iterable, Mapping, Sequence

from lecture_study.domain.models import (
    MockExam,
    MockQuestion,
    PersonalStudyFlashcardDeck,
    PodcastScript,
)
from lecture_study.exams.compiler import _EMBEDDED_NEWLINE_RUN
from lecture_study.study_materials.academic_typst import (
    academic_document_start,
    render_academic_figure,
    render_academic_list_item,
    render_typst_prose,
    typst_string,
)
from lecture_study.study_materials.compiler import (
    SelectedFigure,
    StudyChapter,
    StudyEdition,
    StudyEditionKind,
    StudySection,
    has_informative_study_content,
    study_assertion_identity,
    study_fact_assertion_identity,
    study_fact_subject,
    study_fact_without_category,
)

_MAXIMUM_QUESTIONS: Final = 20
_MAXIMUM_QUESTION_CANDIDATES: Final = 80
_MAXIMUM_QUESTION_ANSWER_WORDS: Final = 200
_MAXIMUM_DISTINCTIVE_CUE_WORDS: Final = 24
_MINIMUM_FLASHCARDS: Final = 18
_MINIMUM_QUESTIONS: Final = 8
_FLASHCARDS_PER_SHEET: Final = 6
_FORMULA_TRIGGER_CHARACTERS: Final = ("=", "+", "-", "@", "\t")
_STUDY_NOISE_MARKERS: Final = (
    "a gente",
    "a aula teórica",
    "a evidência apresenta",
    "antigamente",
    "adriana enfatiza",
    "adriana teixeira",
    "camille",
    "conteúdo aborda",
    "desse jeito",
    "débora",
    "especialização",
    "falou que",
    "graduada em",
    "eu ",
    "hoje vamos",
    "hoje, vamos",
    "linkedin",
    "mestre em",
    "na hora que eu",
    "não é que não é legal",
    "não tem contaminação até",
    "o pessoal",
    "pós graduação",
    "por isso,",
    "perguntou",
    "residência em",
    "responderam",
    "sócia-fundadora",
    "diretora científica",
    "grupo de ensino",
    "coordenadora",
    "tá bom",
    " tá ",
    " né",
    "então,",
    "tô ",
    "uma outra coisa",
    "vamos discutir",
    "vamos falar",
    "você ",
    "vocês ",
    ", ó",
)
_STUDY_FRAGMENT_PREFIXES: Final = (
    "aí ",
    "coisa que ",
    "da ",
    "do ",
    "e ",
    "então ",
    "mas ",
    "porque ",
    "só que ",
)
_CLINICAL_VALUE_MARKERS: Final = (
    "alerta",
    "avalia",
    "córnea",
    "contraind",
    "diagnóst",
    "eletroquimioterapia",
    "exame",
    "fluoresceína",
    "indica",
    "mastectomia",
    "paciente",
    "procedimento",
    "pupila",
    "radioterapia",
    "recomenda",
    "retina",
    "risco",
    "sangramento",
    "teste",
    "tratamento",
    "tumor",
    "úlcera",
    "visão",
)
_WARNING_CATEGORY_LABEL = re.compile(
    r"\((?:"
    r"alerta clínico|alterações neuro-oftálmicas|anatomia|complicação|"
    r"complicações|condição|estresse|fator de risco|medicação tópica|"
    r"procedimento|tratamento|urgência"
    r")\)\s*:",
    flags=re.IGNORECASE,
)
_FLASHCARD_PREDICATE = re.compile(
    r"^(.{3,90}?)\s+(?:não\s+)?(?:"
    r"é|são|foi|foram|tem|têm|deve|devem|pode|podem|permite|permitem|"
    r"identifica|identificam|"
    r"avalia|avaliam|evidencia|evidenciam|indica|indicam|causa|causam|"
    r"apresenta|apresentam|apresentava|apresentavam|inclui|incluem|incluiu|"
    r"incluíram|recomenda|recomendam|auxilia|auxiliam|comprova|comprovam|"
    r"reduz|aumenta|consiste|diferencia|diferenciam|demonstra|demonstram|"
    r"demonstrou|demonstraram|descreve|descrevem|descreveu|descreveram|"
    r"sustenta|sustentam|exige|exigem|substitui|substituem"
    r")\b",
    flags=re.IGNORECASE,
)
_FLASHCARD_CASE_TOPIC = re.compile(
    r"^Em caso de (.{3,90}?)(?:,|\s+(?:deve|devem|pode|podem)\b)",
    flags=re.IGNORECASE,
)
_FLASHCARD_COMPARISON_TOPIC = re.compile(
    r"^Não houve diferença entre (.{3,90})$",
    flags=re.IGNORECASE,
)
_FLASHCARD_CONSIDERATION_TOPIC = re.compile(
    r"^Considerar (?:a possibilidade de )?(.{3,90}?)(?:\s+como|\s+para|\s+em)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class StudySourceEvidence:
    """One source excerpt and the learning units from which it originated."""

    excerpt: str
    source_sequences: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class StudyFlashcard:
    """One source-grounded personal study flashcard."""

    front: str
    back: str
    tags: tuple[str, ...]
    evidence: tuple[StudySourceEvidence, ...]

    @property
    def source_excerpt(self) -> str:
        """Return the first source excerpt for compatibility with existing callers."""
        return self.evidence[0].excerpt

    @property
    def source_excerpts(self) -> tuple[str, ...]:
        """Return distinct source excerpts in their first-seen order."""
        return tuple(dict.fromkeys(item.excerpt for item in self.evidence))

    @property
    def source_sequences(self) -> tuple[int, ...]:
        """Return the ordered union of source sequences represented by the evidence."""
        return tuple(
            sorted(
                {
                    sequence
                    for item in self.evidence
                    for sequence in item.source_sequences
                }
            )
        )

    def with_content(self, *, front: str, back: str) -> StudyFlashcard:
        return replace(self, front=front, back=back)


@dataclass(frozen=True, slots=True)
class StudyQuestion:
    """One open question with a source-grounded commented answer."""

    prompt: str
    answer: str
    commentary: str
    source_sequences: tuple[int, ...]
    source_excerpt: str


@dataclass(frozen=True, slots=True)
class PodcastTurn:
    """One turn in the personal pt-BR revision podcast."""

    speaker: str
    text: str


@dataclass(frozen=True, slots=True)
class StudyDocumentSection:
    """One presentational section shared by Markdown and Typst outputs."""

    title: str
    items: tuple[str, ...]
    numbered: bool = False
    page_break_before: bool = False
    # One optional figure per item, aligned by index (2026-09-15: an
    # inference exam question can now illustrate with the same lecture
    # frame already selected for that question's learning unit -- see
    # inference_mock_exam_document). None for every section whose items
    # carry no figures at all -- every section except the inference
    # exam's "Questões" list; an item's own slot may itself be None when
    # that question's unit had no usable candidate frame.
    item_figures: tuple[SelectedFigure | None, ...] | None = None

    def __post_init__(self) -> None:
        if self.item_figures is not None and len(self.item_figures) != len(self.items):
            raise ValueError(
                "item_figures must have exactly one entry per item "
                f"({len(self.items)} items, {len(self.item_figures)} figures)"
            )


@dataclass(frozen=True, slots=True)
class StudyDocument:
    """One structured personal-study document."""

    title: str
    subtitle: str
    sections: tuple[StudyDocumentSection, ...]

    def with_title(self, title: str) -> StudyDocument:
        return replace(self, title=title)


@dataclass(frozen=True, slots=True)
class PersonalStudyBundle:
    """All confirmed personal study content for one lecture."""

    complete: StudyEdition
    summary: StudyEdition
    flashcards: tuple[StudyFlashcard, ...]
    questions: tuple[StudyQuestion, ...]
    podcast_turns: tuple[PodcastTurn, ...]

    def with_flashcards(
        self,
        flashcards: tuple[StudyFlashcard, ...],
    ) -> PersonalStudyBundle:
        return replace(self, flashcards=flashcards)


def build_personal_study_bundle(
    *,
    complete: StudyEdition,
    summary: StudyEdition,
) -> PersonalStudyBundle:
    """Build every confirmed personal-study derivative from two editions."""

    _validate_editions(complete, summary)
    return PersonalStudyBundle(
        complete=complete,
        summary=summary,
        flashcards=_build_flashcards(complete=complete, summary=summary),
        questions=_build_questions(complete),
        podcast_turns=_build_podcast_turns(complete),
    )


def critical_review_document(
    bundle: PersonalStudyBundle,
    *,
    inference_exam: MockExam | None = None,
) -> StudyDocument:
    """Create the unified critical-review document: essential points,
    clinical warnings, and grounded review questions, divided by chapter
    and by content type within each chapter.

    2026-09-15: replaces the former separate `resumo_critico` and
    `apostila_resumida` documents. Both derived from the exact same
    `bundle.summary.chapters` -- `resumo_critico` just re-prefixed each
    point with "Ponto essencial:"/"Alerta clínico:" -- so keeping two
    documents was pure duplication (operator decision). When an inference
    exam is supplied and its questions carry `pack_sequence`, each
    chapter's own review-question sub-block is grounded in that chapter's
    units specifically.
    """

    questions_by_pack_sequence = _questions_by_pack_sequence(inference_exam)
    sections: list[StudyDocumentSection] = []
    for chapter in bundle.summary.chapters:
        # No "Ponto essencial:"/"Alerta clínico:" prefix on each item
        # (2026-09-15 document review, round 3): the section title right
        # above already says "Pontos essenciais"/"Alertas clínicos" --
        # repeating it on every single line was pure noise adding visual
        # density with no information, on top of an already dense list.
        sections.append(
            StudyDocumentSection(
                title=f"{chapter.title} — Pontos essenciais",
                items=_chapter_points(chapter),
            )
        )
        warnings = _chapter_warnings(chapter)
        if warnings:
            sections.append(
                StudyDocumentSection(
                    title=f"{chapter.title} — Alertas clínicos",
                    items=warnings,
                )
            )
        review_questions = _review_questions_for_chapter(
            chapter, questions_by_pack_sequence
        )
        if review_questions:
            sections.append(
                StudyDocumentSection(
                    title=f"{chapter.title} — Perguntas de revisão",
                    items=review_questions,
                    numbered=True,
                )
            )
    critical_note = StudyDocumentSection(
        title="Orientação para leitura crítica",
        items=(
            "Diferenciar conceitos, condutas e alertas antes da memorização.",
            "Revisar números, contraindicações e condições de aplicação no contexto "
            "em que foram apresentados na aula.",
            "Usar o material como apoio pessoal de estudo, sem atribuir certificação "
            "clínica ao conteúdo.",
        ),
    )
    return StudyDocument(
        title="Resumo crítico",
        subtitle=bundle.complete.lecture_title,
        sections=(*sections, critical_note),
    )


def _questions_by_pack_sequence(
    exam: MockExam | None,
) -> Mapping[int, tuple[MockQuestion, ...]]:
    if exam is None:
        return {}
    grouped: dict[int, list[MockQuestion]] = {}
    for question in exam.questions:
        if question.pack_sequence is None:
            continue
        grouped.setdefault(question.pack_sequence, []).append(question)
    return {sequence: tuple(items) for sequence, items in grouped.items()}


def _review_questions_for_chapter(
    chapter: StudyChapter,
    questions_by_pack_sequence: Mapping[int, tuple[MockQuestion, ...]],
) -> tuple[str, ...]:
    chapter_pack_sequences = sorted(
        {
            sequence
            for section in chapter.sections
            for sequence in section.source_sequences
        }
    )
    return tuple(
        _format_question_with_answer(question)
        for sequence in chapter_pack_sequences
        for question in questions_by_pack_sequence.get(sequence, ())
    )


# A CommonMark hard line break (two trailing spaces before "\n") -- chosen
# over a bare "\n" so the same formatted string reads correctly in both
# renderers this shared StudyDocumentSection.items string crosses: Typst's
# own string-escape handling (typst_string()) turns a bare "\n" into a
# real line break on its own, but the site's Markdown renderer
# (MarkdownIt("commonmark", {"html": False})) would otherwise soft-wrap a
# bare "\n" into a run-on space (2026-09-15 document review: question stem,
# four options, and the answer/explanation were all rendering as one dense
# paragraph, unreadable as a multiple-choice question).
_LINE_BREAK: Final = "  \n"
# A blank line -- a real paragraph break in both renderers (CommonMark's
# own paragraph rule; verified empirically in Typst too, where "\n\n" in a
# string produces genuine extra vertical space, unlike a single "\n"'s
# plain line break) -- used only between an item's distinct sections
# (options block -> answer -> explanation), never between one option and
# the next (2026-09-15 document review, round 4: "Resposta: ..." and the
# explanation right after it still read as running together with no
# pause)."""
_PARAGRAPH_BREAK: Final = "\n\n"


def _format_question_with_options(question: MockQuestion) -> str:
    # render_academic_list_item() splits its item string on "\n" and
    # treats every line after the first as an option's own wrapped
    # continuation (hanging-indented, tight paragraph spacing) -- an
    # embedded newline inside question_text itself (the model splitting
    # the stem into two sentences) is otherwise misread as that, landing
    # the stem's second sentence hanging-indented like an option instead
    # of flowing as one continuous question (2026-09-16 operator report).
    # Same collapse exams/compiler.py's own _escape_markdown already
    # applies to the Markdown rendering of this same MockQuestion.
    stem = _EMBEDDED_NEWLINE_RUN.sub(" ", question.question_text)
    options_lines = _LINE_BREAK.join(
        f"{letter}) {_EMBEDDED_NEWLINE_RUN.sub(' ', text)}"
        for letter, text in sorted(question.options.items())
    )
    return f"{stem}{_LINE_BREAK}{options_lines}"


def _format_question_with_answer(question: MockQuestion) -> str:
    # rstrip(".") before appending "." -- an option's own text often
    # already ends in a period, which produced a visible ".." artifact in
    # the rendered document (2026-09-15 document review).
    correct_option_text = question.options[question.correct_answer].rstrip(".")
    return (
        f"{_format_question_with_options(question)}{_PARAGRAPH_BREAK}"
        f"Resposta: {question.correct_answer}) {correct_option_text}."
        f"{_PARAGRAPH_BREAK}"
        f"{question.explanation}"
    )


def mock_exam_document(bundle: PersonalStudyBundle) -> StudyDocument:
    """Create an open-question mock exam with commented answer key."""

    question_items = tuple(question.prompt for question in bundle.questions)
    answer_items = tuple(
        (
            f"Questão {index} - Resposta: {question.answer} "
            f"Comentário: {question.commentary}"
        )
        for index, question in enumerate(bundle.questions, start=1)
    )
    return StudyDocument(
        title="Simulado com gabarito comentado",
        subtitle=bundle.complete.lecture_title,
        sections=(
            StudyDocumentSection(
                title="Questões",
                items=question_items,
                numbered=True,
            ),
            StudyDocumentSection(
                title="Gabarito comentado",
                items=answer_items,
                page_break_before=True,
            ),
        ),
    )


_INFERENCE_EXAM_DISCLAIMER: Final = (
    "Material de estudo pessoal gerado por inferência, sem certificação "
    "clínica ou autoridade de publicação."
)


def inference_mock_exam_document(
    exam: MockExam,
    *,
    figures_by_pack_sequence: Mapping[int, tuple[SelectedFigure, ...]] | None = None,
) -> StudyDocument:
    """Create a mock exam document from an inference-generated MockExam.

    Sourced from ADR 0024's uncertified personal-study payload (Phase 13's
    real LLM-backed generation) instead of the deterministic template that
    `mock_exam_document` builds from `bundle.questions` -- that function
    stays in place and reachable per D-10; this is a distinct document
    builder for the opt-in inference route, not a replacement.

    2026-09-15: a question whose `pack_sequence` has a selected figure in
    `figures_by_pack_sequence` (the same lookup `build_personal_study_editions`
    already uses to place one figure per learning unit in the complete
    edition) illustrates with that same figure -- no separate selection
    or storage on MockQuestion itself; the figure is resolved purely at
    render time from data both call sites already have.

    Only the FIRST question for a given `pack_sequence` gets the figure,
    not every one: a learning unit produces several questions (5, for
    lecture 10003's own exam), all sharing the same `pack_sequence` and
    therefore the same candidate figure -- attaching it to every one of
    them printed the identical slide five times in a row before this
    guard, confirmed against the compiled simulado.pdf.
    """

    question_items = tuple(
        _format_question_with_options(question) for question in exam.questions
    )
    question_figures = (
        _figures_for_questions_once_per_unit(exam.questions, figures_by_pack_sequence)
        if figures_by_pack_sequence
        else None
    )
    answer_items = tuple(
        (
            f"Questão {index} - Resposta: {question.correct_answer} "
            f"Comentário: {question.explanation}"
        )
        for index, question in enumerate(exam.questions, start=1)
    )
    return StudyDocument(
        title="Simulado com gabarito comentado (gerado por inferência)",
        subtitle=_INFERENCE_EXAM_DISCLAIMER,
        sections=(
            StudyDocumentSection(
                title="Questões",
                # The disclaimer already renders as the document's own
                # subtitle (every page) -- it does not belong in this
                # numbered list too (2026-09-15 document review: it was
                # being rendered as a fake "Questão 1", shifting every
                # real question's number by one).
                items=question_items,
                numbered=True,
                item_figures=question_figures,
            ),
            StudyDocumentSection(
                title="Gabarito comentado",
                items=answer_items,
                page_break_before=True,
            ),
        ),
    )


def _figures_for_questions_once_per_unit(
    questions: Sequence[MockQuestion],
    figures_by_pack_sequence: Mapping[int, tuple[SelectedFigure, ...]],
) -> tuple[SelectedFigure | None, ...]:
    illustrated_pack_sequences: set[int] = set()
    figures: list[SelectedFigure | None] = []
    for question in questions:
        pack_sequence = question.pack_sequence
        already_illustrated = (
            pack_sequence is None or pack_sequence in illustrated_pack_sequences
        )
        figure = (
            None
            if already_illustrated
            else _figure_for_question(question, figures_by_pack_sequence)
        )
        if figure is not None and pack_sequence is not None:
            illustrated_pack_sequences.add(pack_sequence)
        figures.append(figure)
    return tuple(figures)


def _figure_for_question(
    question: MockQuestion,
    figures_by_pack_sequence: Mapping[int, tuple[SelectedFigure, ...]],
) -> SelectedFigure | None:
    if question.pack_sequence is None:
        return None
    candidates = figures_by_pack_sequence.get(question.pack_sequence, ())
    return candidates[0] if candidates else None


def review_plan_document(bundle: PersonalStudyBundle) -> StudyDocument:
    """Create a practical revision sequence for the lecture."""

    chapter_names = tuple(chapter.title for chapter in bundle.complete.chapters)
    return StudyDocument(
        title="Roteiro de revisão",
        subtitle=bundle.complete.lecture_title,
        sections=(
            StudyDocumentSection(
                title="Etapa 1 - Visão geral",
                items=(
                    "Ler a apostila resumida sem interrupções.",
                    f"Identificar os {len(chapter_names)} blocos temáticos da aula.",
                ),
            ),
            StudyDocumentSection(
                title="Etapa 2 - Estudo aprofundado",
                items=tuple(
                    f"Revisar na apostila completa: {chapter_name}."
                    for chapter_name in chapter_names
                ),
            ),
            StudyDocumentSection(
                title="Etapa 3 - Recuperação ativa",
                items=(
                    f"Responder aos {len(bundle.flashcards)} flashcards.",
                    f"Realizar as {len(bundle.questions)} questões sem consultar "
                    "o gabarito.",
                ),
            ),
            StudyDocumentSection(
                title="Etapa 4 - Consolidação",
                items=(
                    "Ouvir o podcast de revisão.",
                    "Registrar dúvidas e erros recorrentes.",
                    "Repetir apenas os flashcards e questões com resposta insegura.",
                ),
            ),
        ),
    )


def podcast_script_document(bundle: PersonalStudyBundle) -> StudyDocument:
    """Create the readable two-role podcast script."""

    return StudyDocument(
        title="Podcast de revisão",
        subtitle=bundle.complete.lecture_title,
        sections=(
            StudyDocumentSection(
                title="Roteiro",
                items=tuple(
                    f"{turn.speaker}: {turn.text}" for turn in bundle.podcast_turns
                ),
            ),
        ),
    )


_INFERENCE_PODCAST_DISCLAIMER: Final = (
    "Material de estudo pessoal gerado por inferência, sem certificação "
    "clínica ou autoridade de publicação."
)


def inference_podcast_script_document(script: PodcastScript) -> StudyDocument:
    """Create the readable podcast script document from an inference-
    generated PodcastScript.

    Sourced from ADR 0024's uncertified personal-study payload (Phase 14's
    real LLM-backed generation) instead of the deterministic template that
    `podcast_script_document` builds from `bundle.podcast_turns` -- that
    function stays in place and reachable per D-10; this is a distinct
    document builder for the opt-in inference route, not a replacement.
    """

    return StudyDocument(
        title="Podcast de revisão (gerado por inferência)",
        subtitle=_INFERENCE_PODCAST_DISCLAIMER,
        sections=(
            StudyDocumentSection(
                title="Roteiro",
                items=(
                    _INFERENCE_PODCAST_DISCLAIMER,
                    *(f"{line.speaker}: {line.text}" for line in script.dialogue),
                ),
            ),
        ),
    )


def render_study_document_markdown(document: StudyDocument) -> str:
    """Render one structured study document as pt-BR Markdown."""

    lines = [f"# {document.title}", "", f"## {document.subtitle}"]
    for section in document.sections:
        if section.page_break_before:
            lines.extend(("", '<div style="page-break-before: always;"></div>'))
        lines.extend(("", f"## {section.title}", ""))
        for index, item in enumerate(section.items, start=1):
            marker = f"{index}." if section.numbered else "-"
            lines.append(f"{marker} {item}")
            figure = _item_figure(section, index)
            if figure is not None:
                lines.extend(("", f"![{figure.caption}]({figure.relative_path})", ""))
    return "\n".join(lines).rstrip() + "\n"


def _item_figure(
    section: StudyDocumentSection, item_number: int
) -> SelectedFigure | None:
    if section.item_figures is None:
        return None
    return section.item_figures[item_number - 1]


def render_study_document_typst(document: StudyDocument) -> str:
    """Render one structured study document as a self-contained Typst file."""

    parts = academic_document_start(
        title=document.title,
        subtitle=document.subtitle,
    )
    for section in document.sections:
        if section.page_break_before:
            parts.append("#pagebreak()")
        parts.append(f"#heading(level: 1)[#text({typst_string(section.title)})]")
        for index, item in enumerate(section.items, start=1):
            parts.append(
                render_academic_list_item(
                    item,
                    marker=f"{index}." if section.numbered else "-",
                )
            )
            figure = _item_figure(section, index)
            if figure is not None:
                parts.append(
                    render_academic_figure(figure.relative_path, figure.caption)
                )
    return "\n".join(parts) + "\n"


def render_flashcards_markdown(bundle: PersonalStudyBundle) -> str:
    """Render flashcards as a human-readable pt-BR study document: every
    question first (self-quiz read), then every answer (check afterward)
    -- not interleaved, so reading the questions in sequence does not
    also reveal the next answer."""

    lines = [
        "# Flashcards",
        "",
        f"## {bundle.complete.lecture_title}",
        "",
    ]
    lines.extend(_flashcard_question_and_answer_sections(bundle.flashcards))
    return "\n".join(lines).rstrip() + "\n"


def _flashcard_question_and_answer_sections(
    cards: Sequence[StudyFlashcard],
) -> list[str]:
    lines = ["## Perguntas", ""]
    for index, card in enumerate(cards, start=1):
        lines.extend((f"{index}. {card.front}", ""))
    lines.extend(("## Respostas", ""))
    for index, card in enumerate(cards, start=1):
        lines.extend((f"{index}. {card.back}", ""))
    return lines


def render_flashcards_csv(bundle: PersonalStudyBundle) -> str:
    """Render an Anki-importable CSV with spreadsheet-safe fields."""

    output = StringIO(newline="")
    writer = csv.writer(output)
    for card in bundle.flashcards:
        writer.writerow(
            (
                _sanitize_csv_field(card.front),
                _sanitize_csv_field(card.back),
                " ".join(card.tags),
            )
        )
    return output.getvalue()


def render_printable_flashcards_typst(bundle: PersonalStudyBundle) -> str:
    """Render duplex-ready flashcards with six cards on each A4 sheet."""

    return _render_printable_flashcard_sheets(bundle.flashcards)


_INFERENCE_FLASHCARDS_DISCLAIMER: Final = (
    "Material de estudo pessoal gerado por inferência, sem certificação "
    "clínica ou autoridade de publicação."
)


def render_generated_flashcards_markdown(deck: PersonalStudyFlashcardDeck) -> str:
    """Render a generated flashcard deck (ADR 0024's personal-study route)
    as a human-readable pt-BR study document, matching
    render_flashcards_markdown's own heading/section shape exactly --
    only the source of the front/back text differs."""

    lines = [
        "# Flashcards (gerados por inferência)",
        "",
        f"## {_INFERENCE_FLASHCARDS_DISCLAIMER}",
        "",
        "## Perguntas",
        "",
    ]
    for index, card in enumerate(deck.cards, start=1):
        lines.extend((f"{index}. {card.prompt}", ""))
    lines.extend(("## Respostas", ""))
    for index, card in enumerate(deck.cards, start=1):
        lines.extend((f"{index}. {card.answer}", ""))
    return "\n".join(lines).rstrip() + "\n"


def render_generated_flashcards_csv(deck: PersonalStudyFlashcardDeck) -> str:
    """Render an Anki-importable CSV from a generated flashcard deck
    (ADR 0024's personal-study route), reusing render_flashcards_csv's
    own field-escaping helper."""

    output = StringIO(newline="")
    writer = csv.writer(output)
    for card in deck.cards:
        writer.writerow(
            (
                _sanitize_csv_field(card.prompt),
                _sanitize_csv_field(card.answer),
                "",
            )
        )
    return output.getvalue()


def render_printable_generated_flashcards_typst(
    deck: PersonalStudyFlashcardDeck,
) -> str:
    """Render duplex-ready generated flashcards (ADR 0024's personal-study
    route) with six cards per A4 sheet, reusing the same layout, card-
    cell, and back-mirroring helpers as render_printable_flashcards_typst
    -- these are the same physical printable sheet shape, only the source
    of the front/back text differs."""

    study_cards = tuple(
        StudyFlashcard(front=card.prompt, back=card.answer, tags=(), evidence=())
        for card in deck.cards
    )
    return _render_printable_flashcard_sheets(study_cards)


def _render_printable_flashcard_sheets(cards: tuple[StudyFlashcard, ...]) -> str:
    parts = [
        '#set page(paper: "a4", margin: 10mm)',
        '#set text(font: "Arial", lang: "pt", size: 10pt, hyphenate: false)',
        "#set par(justify: false, first-line-indent: 0pt)",
        "#let study-card(label, body) = box(",
        "  width: 100%, height: 100%,",
        "  stroke: 0.7pt + black, inset: 7mm,",
        ")[",
        "  #set par(first-line-indent: 0pt, justify: false)",
        '  #text(size: 8pt, weight: "bold")[#label]',
        "  #v(8mm)",
        "  #align(center + horizon)[#text(size: 11pt)[#body]]",
        "]",
    ]
    indexed_cards = tuple(enumerate(cards, start=1))
    for sheet_index, sheet in enumerate(batched(indexed_cards, _FLASHCARDS_PER_SHEET)):
        if sheet_index:
            parts.append("#pagebreak()")
        parts.append(_render_flashcard_sheet(sheet, front=True))
        parts.append("#pagebreak()")
        parts.append(_render_flashcard_sheet(_mirror_card_backs(sheet), front=False))
    parts.append("#pagebreak(weak: true)")
    return "\n".join(parts) + "\n"


def _validate_editions(
    complete: StudyEdition,
    summary: StudyEdition,
) -> None:
    if complete.kind is not StudyEditionKind.COMPLETE:
        raise ValueError("complete edition has the wrong kind")
    if summary.kind is not StudyEditionKind.SUMMARY:
        raise ValueError("summary edition has the wrong kind")
    if complete.lecture_id != summary.lecture_id:
        raise ValueError("study editions belong to different lectures")
    if complete.source_unit_count != summary.source_unit_count:
        raise ValueError("study editions have different source-unit counts")


def _build_flashcards(
    *,
    complete: StudyEdition,
    summary: StudyEdition,
) -> tuple[StudyFlashcard, ...]:
    summary_cards = _deduplicate_flashcards(_edition_flashcards(summary))
    complete_cards = _edition_flashcards(complete)
    contextual_summary_cards = _prefer_contextual_flashcards(
        summary_cards=summary_cards,
        complete_cards=complete_cards,
    )
    all_cards = _deduplicate_flashcards((*contextual_summary_cards, *complete_cards))
    target_count = max(_MINIMUM_FLASHCARDS, len(summary_cards))
    selected_cards = all_cards[:target_count]
    return _disambiguate_flashcard_fronts(selected_cards)


def _prefer_contextual_flashcards(
    *,
    summary_cards: tuple[StudyFlashcard, ...],
    complete_cards: tuple[StudyFlashcard, ...],
) -> tuple[StudyFlashcard, ...]:
    complete_by_content: dict[str, list[StudyFlashcard]] = {}
    complete_by_subject: dict[str, list[StudyFlashcard]] = {}
    for card in complete_cards:
        complete_by_content.setdefault(study_assertion_identity(card.back), []).append(
            card
        )
        subject = study_fact_subject(card.back)
        if subject is not None:
            complete_by_subject.setdefault(subject.casefold(), []).append(card)
    selected_cards: list[StudyFlashcard] = []
    for card in summary_cards:
        if not _flashcard_has_generic_context(card):
            selected_cards.append(card)
            continue
        exact_match = _select_source_aligned_flashcard(
            card,
            complete_by_content.get(study_assertion_identity(card.back), ()),
        )
        subject = study_fact_subject(card.back)
        subject_match = (
            _select_source_aligned_flashcard(
                card,
                complete_by_subject.get(subject.casefold(), ()),
            )
            if subject is not None
            else None
        )
        contextual_match = exact_match or subject_match
        selected_cards.append(
            card.with_content(front=contextual_match.front, back=card.back)
            if contextual_match is not None
            else card
        )
    return tuple(selected_cards)


def _select_source_aligned_flashcard(
    summary_card: StudyFlashcard,
    candidates: Sequence[StudyFlashcard],
) -> StudyFlashcard | None:
    if not summary_card.source_sequences:
        return None
    exact_source_matches = [
        candidate
        for candidate in candidates
        if candidate.source_sequences == summary_card.source_sequences
    ]
    consolidated_exact_matches = _deduplicate_flashcards(tuple(exact_source_matches))
    if len(consolidated_exact_matches) == 1:
        return consolidated_exact_matches[0]
    overlapping_matches = [
        candidate
        for candidate in candidates
        if set(candidate.source_sequences).intersection(summary_card.source_sequences)
    ]
    consolidated_overlapping_matches = _deduplicate_flashcards(
        tuple(overlapping_matches)
    )
    return (
        consolidated_overlapping_matches[0]
        if len(consolidated_overlapping_matches) == 1
        else None
    )


def _flashcard_has_generic_context(card: StudyFlashcard) -> bool:
    return bool(
        re.match(
            r"^Em (?:Pontos essenciais|Parte \d+),",
            card.front,
            flags=re.IGNORECASE,
        )
    )


def _edition_flashcards(edition: StudyEdition) -> tuple[StudyFlashcard, ...]:
    cards: list[StudyFlashcard] = []
    for chapter in edition.chapters:
        for section in chapter.sections:
            points = _deduplicate(
                point
                for point in _bullet_points(section.body)
                if _is_study_worthy(point)
            )
            warnings = tuple(
                warning
                for warning in section.warnings
                if _warning_is_study_worthy(warning)
            )
            for point in points:
                cards.append(
                    _build_flashcard(
                        context_title=section.title,
                        content=point,
                        back=point,
                        tags=("ponto_essencial",),
                        source_sequences=section.source_sequences,
                        source_excerpt=point,
                    )
                )
            for warning in warnings:
                cleaned_warning = _clean_warning(warning)
                cards.append(
                    _build_flashcard(
                        context_title=section.title,
                        content=cleaned_warning,
                        back=cleaned_warning,
                        tags=("alerta_clinico",),
                        source_sequences=section.source_sequences,
                        source_excerpt=warning,
                        is_warning=True,
                    )
                )
    return tuple(cards)


def _build_flashcard(
    *,
    context_title: str,
    content: str,
    back: str,
    tags: tuple[str, ...],
    source_sequences: tuple[int, ...],
    source_excerpt: str,
    is_warning: bool = False,
) -> StudyFlashcard:
    front = _flashcard_front(
        context_title,
        content,
        is_warning=is_warning,
    )
    return StudyFlashcard(
        front=front,
        back=back,
        tags=tags,
        evidence=(
            StudySourceEvidence(
                excerpt=source_excerpt,
                source_sequences=source_sequences,
            ),
        ),
    )


def _disambiguate_flashcard_fronts(
    cards: tuple[StudyFlashcard, ...],
) -> tuple[StudyFlashcard, ...]:
    backs_by_front: dict[str, set[str]] = {}
    for card in cards:
        backs_by_front.setdefault(card.front, set()).add(card.back)
    ambiguous_fronts = {
        front for front, backs in backs_by_front.items() if len(backs) > 1
    }
    disambiguated = tuple(
        card.with_content(
            front=_contextualize_cloze_front(
                original_front=card.front,
                cloze_front=_cloze_flashcard_front(
                    card.back,
                    is_warning="alerta_clinico" in card.tags,
                    include_discriminator=True,
                ),
            ),
            back=card.back,
        )
        if card.front in ambiguous_fronts
        else card
        for card in cards
    )
    source_qualified = _qualify_residual_flashcard_fronts(disambiguated)
    variant_qualified = _qualify_same_source_variants(source_qualified)
    _validate_flashcard_fronts(variant_qualified)
    return variant_qualified


def _contextualize_cloze_front(*, original_front: str, cloze_front: str) -> str:
    context, separator, _ = original_front.partition(", ")
    if not separator or not context.startswith("Em "):
        return cloze_front
    return f"{context}, {cloze_front[:1].lower()}{cloze_front[1:]}"


def _qualify_residual_flashcard_fronts(
    cards: tuple[StudyFlashcard, ...],
) -> tuple[StudyFlashcard, ...]:
    sequences_by_front: dict[str, list[tuple[int, ...]]] = {}
    for card in cards:
        sequences_by_front.setdefault(card.front, []).append(card.source_sequences)
    qualifiable_fronts = {
        front
        for front, sequences in sequences_by_front.items()
        if len(sequences) > 1
        and all(sequences)
        and len(set(sequences)) == len(sequences)
    }
    return tuple(
        card.with_content(
            front=f"{_source_sequence_label(card.source_sequences)} — {card.front}",
            back=card.back,
        )
        if card.front in qualifiable_fronts
        else card
        for card in cards
    )


def _source_sequence_label(source_sequences: tuple[int, ...]) -> str:
    if len(source_sequences) == 1:
        return f"Unidade {source_sequences[0]}"
    joined_sequences = ", ".join(str(sequence) for sequence in source_sequences)
    return f"Unidades {joined_sequences}"


def _qualify_same_source_variants(
    cards: tuple[StudyFlashcard, ...],
) -> tuple[StudyFlashcard, ...]:
    identities_by_front: dict[str, set[str]] = {}
    for card in cards:
        identities_by_front.setdefault(card.front, set()).add(
            study_assertion_identity(card.back)
        )
    variant_numbers = {
        front: {
            identity: index
            for index, identity in enumerate(sorted(identities), start=1)
        }
        for front, identities in identities_by_front.items()
        if len(identities) > 1
    }
    return tuple(
        card.with_content(
            front=(
                "Variante "
                f"{variant_numbers[card.front][study_assertion_identity(card.back)]}"
                f" — {card.front}"
            ),
            back=card.back,
        )
        if card.front in variant_numbers
        else card
        for card in cards
    )


def _deduplicate_flashcards(
    cards: tuple[StudyFlashcard, ...],
) -> tuple[StudyFlashcard, ...]:
    grouped_cards: list[StudyFlashcard] = []
    first_positions: list[int] = []
    index_by_identity: dict[str, int] = {}
    for position, card in enumerate(cards):
        content_identity = study_assertion_identity(card.back)
        existing_index = index_by_identity.get(content_identity)
        if existing_index is None:
            existing_index = len(grouped_cards)
            grouped_cards.append(card)
            first_positions.append(position)
            index_by_identity[content_identity] = existing_index
            continue
        existing = grouped_cards[existing_index]
        grouped_cards[existing_index] = StudyFlashcard(
            front=existing.front,
            back=existing.back,
            tags=tuple(dict.fromkeys((*existing.tags, *card.tags))),
            evidence=tuple(dict.fromkeys((*existing.evidence, *card.evidence))),
        )
    merge_targets = _source_aligned_plain_merge_targets(
        contents=tuple(card.back for card in grouped_cards),
        source_sequences=tuple(card.source_sequences for card in grouped_cards),
    )
    absorbed_indices: set[int] = set()
    for plain_index, categorized_index in merge_targets.items():
        ordered_indices = sorted(
            (plain_index, categorized_index),
            key=first_positions.__getitem__,
        )
        first_card, second_card = (grouped_cards[index] for index in ordered_indices)
        grouped_cards[categorized_index] = StudyFlashcard(
            front=first_card.front,
            back=first_card.back,
            tags=tuple(dict.fromkeys((*first_card.tags, *second_card.tags))),
            evidence=tuple(
                dict.fromkeys((*first_card.evidence, *second_card.evidence))
            ),
        )
        first_positions[categorized_index] = first_positions[ordered_indices[0]]
        absorbed_indices.add(plain_index)
    return tuple(
        grouped_cards[index]
        for index in sorted(
            (
                index
                for index in range(len(grouped_cards))
                if index not in absorbed_indices
            ),
            key=first_positions.__getitem__,
        )
    )


def _study_subject_identity(content: str) -> str | None:
    subject = study_fact_subject(content)
    return study_assertion_identity(subject) if subject is not None else None


def _source_aligned_plain_merge_targets(
    *,
    contents: Sequence[str],
    source_sequences: Sequence[tuple[int, ...]],
) -> dict[int, int]:
    categorized_by_assertion_and_sequence: dict[tuple[str, int], set[int]] = {}
    plain_indices: list[int] = []
    for index, (content, sequences) in enumerate(
        zip(contents, source_sequences, strict=True)
    ):
        subject_identity = _study_subject_identity(content)
        if subject_identity is None:
            plain_indices.append(index)
            continue
        assertion_identity = study_fact_assertion_identity(content)
        for sequence in sequences:
            categorized_by_assertion_and_sequence.setdefault(
                (assertion_identity, sequence),
                set(),
            ).add(index)
    merge_targets: dict[int, int] = {}
    for plain_index in plain_indices:
        assertion_identity = study_fact_assertion_identity(contents[plain_index])
        compatible_indices: set[int] = set()
        for sequence in source_sequences[plain_index]:
            compatible_indices.update(
                categorized_by_assertion_and_sequence.get(
                    (assertion_identity, sequence),
                    (),
                )
            )
        if len(compatible_indices) == 1:
            merge_targets[plain_index] = compatible_indices.pop()
    return merge_targets


def _validate_flashcard_fronts(cards: tuple[StudyFlashcard, ...]) -> None:
    seen_fronts: set[str] = set()
    for card in cards:
        if card.front in seen_fronts:
            raise ValueError(
                f"duplicate flashcard front remained after consolidation: {card.front!r}"
            )
        seen_fronts.add(card.front)


def _build_questions(complete: StudyEdition) -> tuple[StudyQuestion, ...]:
    primary_questions, primary_candidates_used = _collect_unique_questions(
        (
            question
            for chapter in complete.chapters
            for question in _chapter_questions(chapter)
        ),
        candidate_limit=_MAXIMUM_QUESTION_CANDIDATES,
    )
    eligible_primary = _disambiguate_question_prompts(primary_questions)
    if len(eligible_primary) >= _MINIMUM_QUESTIONS:
        return eligible_primary[:_MAXIMUM_QUESTIONS]
    remaining_budget = _MAXIMUM_QUESTION_CANDIDATES - primary_candidates_used
    supplemental_questions, _ = _collect_unique_questions(
        _supplemental_questions(complete, limit=remaining_budget),
        candidate_limit=remaining_budget,
        excluded_answer_identities={
            study_assertion_identity(question.answer) for question in eligible_primary
        },
    )
    combined = _deduplicate_questions((*eligible_primary, *supplemental_questions))
    disambiguated = _disambiguate_question_prompts(combined)
    return disambiguated[:_MINIMUM_QUESTIONS]


def _supplemental_questions(
    complete: StudyEdition,
    *,
    limit: int,
) -> Iterable[StudyQuestion]:
    remaining = limit
    for chapter in complete.chapters:
        for section in chapter.sections:
            if remaining <= 0:
                return
            section_questions = _supplemental_section_questions(
                section,
                limit=remaining,
            )
            for question in section_questions:
                yield question
                remaining -= 1
                if remaining <= 0:
                    return


def _supplemental_section_questions(
    section: StudySection,
    *,
    limit: int,
) -> tuple[StudyQuestion, ...]:
    candidates = nlargest(
        limit,
        chain(
            (
                (point, False, point)
                for point in _bullet_points(section.body)
                if _is_study_worthy(point)
            ),
            (
                (cleaned_warning, True, warning)
                for warning in section.warnings
                if _warning_is_study_worthy(warning)
                for cleaned_warning in (_clean_warning(warning),)
            ),
        ),
        key=lambda candidate: _study_sentence_score(candidate[0]),
    )
    return tuple(
        StudyQuestion(
            prompt=_flashcard_front(
                section.title,
                content,
                is_warning=is_warning,
            ),
            answer=(
                _clean_warning(content) if is_warning else _clean_study_text(content)
            ),
            commentary=(
                "A resposta recupera um alerta complementar com sua condição "
                f'de aplicação na seção "{section.title}".'
                if is_warning
                else "A resposta recupera uma informação complementar da seção "
                f'"{section.title}".'
            ),
            source_sequences=section.source_sequences,
            source_excerpt=source_excerpt,
        )
        for content, is_warning, source_excerpt in candidates
    )


def _deduplicate_questions(
    questions: Iterable[StudyQuestion],
) -> tuple[StudyQuestion, ...]:
    grouped_questions: list[StudyQuestion] = []
    first_positions: list[int] = []
    index_by_identity: dict[str, int] = {}
    for position, question in enumerate(questions):
        content_identity = study_assertion_identity(question.answer)
        existing_index = index_by_identity.get(content_identity)
        if existing_index is None:
            existing_index = len(grouped_questions)
            grouped_questions.append(question)
            first_positions.append(position)
            index_by_identity[content_identity] = existing_index
    merge_targets = _source_aligned_plain_merge_targets(
        contents=tuple(question.answer for question in grouped_questions),
        source_sequences=tuple(
            question.source_sequences for question in grouped_questions
        ),
    )
    for plain_index, categorized_index in merge_targets.items():
        if first_positions[plain_index] < first_positions[categorized_index]:
            grouped_questions[categorized_index] = grouped_questions[plain_index]
        first_positions[categorized_index] = min(
            first_positions[plain_index],
            first_positions[categorized_index],
        )
    absorbed_indices = set(merge_targets)
    return tuple(
        grouped_questions[index]
        for index in sorted(
            (
                index
                for index in range(len(grouped_questions))
                if index not in absorbed_indices
            ),
            key=first_positions.__getitem__,
        )
    )


def _collect_unique_questions(
    questions: Iterable[StudyQuestion],
    *,
    candidate_limit: int,
    excluded_answer_identities: set[str] | None = None,
) -> tuple[tuple[StudyQuestion, ...], int]:
    selected: dict[str, StudyQuestion] = {}
    excluded = excluded_answer_identities or set()
    candidates_used = 0
    for question in questions:
        if candidates_used >= candidate_limit:
            break
        candidates_used += 1
        answer_identity = study_assertion_identity(question.answer)
        if answer_identity in excluded:
            continue
        selected.setdefault(answer_identity, question)
    return _deduplicate_questions(tuple(selected.values())), candidates_used


def _disambiguate_question_prompts(
    questions: tuple[StudyQuestion, ...],
) -> tuple[StudyQuestion, ...]:
    questions_by_prompt: dict[str, list[StudyQuestion]] = {}
    for question in questions:
        questions_by_prompt.setdefault(question.prompt, []).append(question)
    disambiguated: list[StudyQuestion] = []
    for prompt_group in questions_by_prompt.values():
        if len(prompt_group) == 1:
            disambiguated.extend(prompt_group)
            continue
        group_words = tuple(_bounded_answer_words(item.answer) for item in prompt_group)
        distinctive_questions: list[StudyQuestion] = []
        for question, answer_words in zip(prompt_group, group_words, strict=True):
            if answer_words is None:
                continue
            distinctive_front = _distinctive_question_front(
                answer_words=answer_words,
                group_words=group_words,
            )
            if distinctive_front is None:
                continue
            distinctive_questions.append(
                replace(
                    question,
                    prompt=_contextualize_cloze_front(
                        original_front=question.prompt,
                        cloze_front=distinctive_front,
                    ),
                )
            )
        disambiguated.extend(distinctive_questions or prompt_group[:1])
    unique_questions = _deduplicate_question_prompts(disambiguated)
    _validate_question_prompts(unique_questions)
    return unique_questions


def _distinctive_question_front(
    *,
    answer_words: tuple[str, ...],
    group_words: Sequence[tuple[str, ...] | None],
) -> str | None:
    longest_shared_prefix = 0
    for other_words in group_words:
        if other_words is None or other_words == answer_words:
            continue
        shared_prefix = 0
        for answer_word, other_word in zip(answer_words, other_words):
            if answer_word != other_word:
                break
            shared_prefix += 1
        longest_shared_prefix = max(longest_shared_prefix, shared_prefix)
    prefix_length = longest_shared_prefix + 1
    if (
        prefix_length >= len(answer_words)
        or prefix_length > _MAXIMUM_DISTINCTIVE_CUE_WORDS
    ):
        return None
    cue = " ".join(answer_words[:prefix_length])
    return f"Complete a informação clínica: {cue} ____."


def _bounded_answer_words(answer: str) -> tuple[str, ...] | None:
    words = tuple(_cloze_content(answer).split())
    if not words or len(words) > _MAXIMUM_QUESTION_ANSWER_WORDS:
        return None
    return words


def _deduplicate_question_prompts(
    questions: Iterable[StudyQuestion],
) -> tuple[StudyQuestion, ...]:
    selected: dict[str, StudyQuestion] = {}
    for question in questions:
        selected.setdefault(question.prompt, question)
    return tuple(selected.values())


def _validate_question_prompts(questions: tuple[StudyQuestion, ...]) -> None:
    seen_prompts: set[str] = set()
    for question in questions:
        if question.prompt in seen_prompts:
            raise ValueError(
                "duplicate question prompt remained after consolidation: "
                f"{question.prompt!r}"
            )
        seen_prompts.add(question.prompt)


def _chapter_questions(chapter: StudyChapter) -> tuple[StudyQuestion, ...]:
    questions = tuple(
        question
        for section in _representative_sections(chapter.sections)
        for question in [_section_question(section)]
        if question is not None
    )
    warning_candidate = _best_warning_with_section(chapter.sections)
    if warning_candidate is not None:
        warning_section, warning = warning_candidate
        return (
            *questions,
            StudyQuestion(
                prompt=(f"Qual alerta clínico é relevante em {warning_section.title}?"),
                answer=_clean_warning(warning),
                commentary=(
                    "O alerta deve ser recuperado com sua condição de aplicação, "
                    "sem generalização para outros contextos."
                ),
                source_sequences=warning_section.source_sequences,
                source_excerpt=warning,
            ),
        )
    return questions


def _section_question(section: StudySection) -> StudyQuestion | None:
    source_excerpt = _first_study_sentence(section.body)
    is_warning = False
    if not source_excerpt:
        source_excerpt = _best_warning((section,)) or ""
        is_warning = bool(source_excerpt)
    if not source_excerpt:
        return None
    answer = (
        _clean_warning(source_excerpt)
        if is_warning
        else _clean_study_text(source_excerpt)
    )
    return StudyQuestion(
        prompt=_flashcard_front(
            section.title,
            answer,
            is_warning=is_warning,
        ),
        answer=answer,
        commentary=(
            f'A resposta corresponde ao ponto central da seção "{section.title}".'
        ),
        source_sequences=section.source_sequences,
        source_excerpt=source_excerpt,
    )


def _representative_sections(
    sections: Sequence[StudySection],
) -> tuple[StudySection, ...]:
    if len(sections) <= 3:
        return tuple(sections)
    indexes = (0, len(sections) // 2, len(sections) - 1)
    return tuple(sections[index] for index in indexes)


def _build_podcast_turns(summary: StudyEdition) -> tuple[PodcastTurn, ...]:
    turns = [
        PodcastTurn(
            speaker="Apresentação",
            text=(
                f"Este episódio revisa a aula {summary.lecture_title}. "
                "O foco será recuperar os pontos essenciais e os alertas clínicos."
            ),
        )
    ]
    for chapter in summary.chapters:
        turns.extend(_chapter_podcast_turns(chapter))
    turns.append(
        PodcastTurn(
            speaker="Apresentação",
            text=(
                "A revisão termina aqui. O próximo passo é responder aos "
                "flashcards e realizar o simulado sem consultar o gabarito."
            ),
        )
    )
    return tuple(turns)


def _chapter_podcast_turns(chapter: StudyChapter) -> tuple[PodcastTurn, ...]:
    points = _chapter_points(chapter)
    warnings = _chapter_warnings(chapter)
    turns = [
        PodcastTurn(
            speaker="Apresentação",
            text=f"O próximo bloco é {chapter.title}.",
        ),
        PodcastTurn(
            speaker="Explicação",
            text=" ".join(points),
        ),
    ]
    if warnings:
        turns.extend(
            (
                PodcastTurn(
                    speaker="Apresentação",
                    text="Quais alertas merecem atenção neste bloco?",
                ),
                PodcastTurn(
                    speaker="Explicação",
                    text=" ".join(warnings),
                ),
            )
        )
    return tuple(turn for turn in turns if turn.text.strip())


def _chapter_points(chapter: StudyChapter) -> tuple[str, ...]:
    points = _deduplicate(
        point for section in chapter.sections for point in _bullet_points(section.body)
    )
    return tuple(point for point in points if _is_study_worthy(point))


def _chapter_warnings(chapter: StudyChapter) -> tuple[str, ...]:
    return _deduplicate(
        _clean_warning(warning)
        for warning in _chapter_raw_warnings(chapter)
        if _warning_is_study_worthy(warning)
    )


def _chapter_raw_warnings(chapter: StudyChapter) -> tuple[str, ...]:
    return tuple(
        warning for section in chapter.sections for warning in section.warnings
    )


def _bullet_points(body: str) -> tuple[str, ...]:
    return tuple(
        line.removeprefix("- ").strip()
        for line in body.splitlines()
        if line.removeprefix("- ").strip()
    )


def _first_study_sentence(body: str) -> str:
    plain = re.sub(r"(?m)^\s*#{1,6}\s*", "", body)
    plain = re.sub(r"(?m)^\s*[-*]\s+", "", plain)
    sentences = tuple(
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", plain)
        if _is_study_worthy(sentence.strip())
    )
    return max(sentences, key=_study_sentence_score, default="")


def _best_warning(sections: Sequence[StudySection]) -> str | None:
    candidate = _best_warning_with_section(sections)
    return candidate[1] if candidate is not None else None


def _best_warning_with_section(
    sections: Sequence[StudySection],
) -> tuple[StudySection, str] | None:
    candidates = (
        (section, warning)
        for section in sections
        for warning in section.warnings
        if _warning_is_study_worthy(warning)
    )
    return max(
        candidates,
        key=lambda item: _study_sentence_score(_clean_warning(item[1])),
        default=None,
    )


def _deduplicate(values: Iterable[str]) -> tuple[str, ...]:
    selected: dict[str, str] = {}
    for value in values:
        selected.setdefault(study_assertion_identity(value), value)
    return tuple(selected.values())


def _is_study_worthy(value: str) -> bool:
    normalized = value.casefold()
    return (
        len(value) >= 30
        and not value.endswith("?")
        and has_informative_study_content(value)
        and _study_sentence_score(value)[0] > 0
        and not normalized.startswith(_STUDY_FRAGMENT_PREFIXES)
        and not any(marker in normalized for marker in _STUDY_NOISE_MARKERS)
        and "###" not in value
    )


def _study_sentence_score(value: str) -> tuple[int, int]:
    normalized = value.casefold()
    clinical_score = sum(
        1 for marker in _CLINICAL_VALUE_MARKERS if marker in normalized
    )
    return clinical_score, min(len(value), 300)


def _clean_warning(value: str) -> str:
    category = _WARNING_CATEGORY_LABEL.search(value)
    if category is None:
        return _capitalize_first(value)
    without_category = f"{value[: category.start()].rstrip()}:{value[category.end() :]}"
    return _capitalize_first(without_category)


def _warning_is_study_worthy(value: str) -> bool:
    cleaned = _clean_warning(value)
    if len(cleaned) < 30:
        return False
    if not has_informative_study_content(cleaned):
        return False
    if ":" not in cleaned:
        return True
    subject, context = cleaned.split(":", maxsplit=1)
    subject_identity = re.sub(r"\W+", "", subject.casefold())
    context_identity = re.sub(r"\W+", "", context.casefold())
    return subject_identity != context_identity


def _clean_study_text(value: str) -> str:
    without_markdown = re.sub(r"(?m)^\s*#{1,6}\s*", "", value)
    without_bullets = without_markdown.lstrip(" -•\t")
    cleaned = without_bullets.replace("**", "").replace("__", "").strip()
    return _capitalize_first(cleaned)


def _capitalize_first(value: str) -> str:
    return f"{value[:1].upper()}{value[1:]}" if value else value


def _sanitize_csv_field(value: str) -> str:
    starts_with_trigger = value.startswith(
        _FORMULA_TRIGGER_CHARACTERS
    ) or value.lstrip().startswith(_FORMULA_TRIGGER_CHARACTERS)
    single_line = (
        value.replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")
    )
    return f"'{single_line}" if starts_with_trigger else single_line


def _flashcard_front(
    context_title: str,
    content: str,
    *,
    is_warning: bool = False,
) -> str:
    topic = _flashcard_topic(content)
    if topic is None:
        cloze_front = _cloze_flashcard_front(content, is_warning=is_warning)
        return f"Em {context_title}, {cloze_front[:1].lower()}{cloze_front[1:]}"
    if is_warning:
        return (
            f"Em {context_title}, qual alerta clínico deve ser lembrado sobre "
            f"{_lowercase_initial_article(topic)}?"
        )
    return (
        f"Em {context_title}, o que deve ser recordado sobre "
        f"{_lowercase_initial_article(topic)}?"
    )


def _cloze_flashcard_front(
    content: str,
    *,
    is_warning: bool = False,
    include_discriminator: bool = False,
) -> str:
    cleaned = _cloze_content(content)
    conditional_cue = _conditional_cloze_cue(cleaned) if include_discriminator else None
    if conditional_cue is not None:
        prefix = (
            "Complete o alerta clínico"
            if is_warning
            else "Complete a informação clínica"
        )
        return f"{prefix}: {conditional_cue}."
    words = cleaned.split()
    initial_articles = {"a", "as", "o", "os", "um", "uma", "uns", "umas"}
    leading_word = words[0].casefold() if words else ""
    if leading_word == "não":
        prefix_count = 3
    else:
        prefix_count = 2 if leading_word in initial_articles else 1
    if include_discriminator:
        prefix_count += 2
    prefix_count = min(prefix_count, max(1, len(words) - 1))
    prefix_words = " ".join(words[:prefix_count])
    cue = f"{prefix_words} ____"
    prefix = (
        "Complete o alerta clínico" if is_warning else "Complete a informação clínica"
    )
    return f"{prefix}: {cue}."


def _cloze_content(content: str) -> str:
    return study_fact_without_category(_clean_study_text(content))


def _conditional_cloze_cue(content: str) -> str | None:
    condition_match = re.search(r" quando ", content, flags=re.IGNORECASE)
    if condition_match is None:
        return None
    condition_start = condition_match.start()
    assertion = content[:condition_start]
    condition = content[condition_start:]
    for predicate in (" deve estar", " devem estar", " pode estar", " podem estar"):
        predicate_match = re.search(
            re.escape(predicate), assertion, flags=re.IGNORECASE
        )
        if predicate_match is None or predicate_match.start() <= 0:
            continue
        predicate_end = predicate_match.end()
        if predicate_end > 100 or not assertion[predicate_end:].strip():
            return None
        return f"{assertion[:predicate_end]} ____{condition}"
    return None


def _lowercase_initial_article(topic: str) -> str:
    if topic.startswith(("A ", "As ", "O ", "Os ")):
        return topic[0].lower() + topic[1:]
    return topic


def _flashcard_topic(content: str) -> str | None:
    cleaned = _clean_study_text(content).rstrip(".;:")
    fact_subject = study_fact_subject(cleaned)
    if fact_subject is not None:
        return fact_subject
    subject, separator, _ = cleaned.partition(":")
    if separator and 3 <= len(subject) <= 90:
        return subject
    for topic_pattern in (
        _FLASHCARD_CASE_TOPIC,
        _FLASHCARD_COMPARISON_TOPIC,
        _FLASHCARD_CONSIDERATION_TOPIC,
    ):
        topic_match = topic_pattern.match(cleaned)
        if topic_match:
            return topic_match.group(1).rstrip(" ,;:")
    predicate_match = _FLASHCARD_PREDICATE.match(cleaned)
    if predicate_match:
        return predicate_match.group(1).rstrip(" ,;:")
    return None


def _render_flashcard_sheet(
    cards: Sequence[tuple[int, StudyFlashcard] | None],
    *,
    front: bool,
) -> str:
    cells: list[str] = []
    for slot in cards:
        if slot is None:
            cells.append(_render_blank_flashcard_cell())
            continue
        index, card = slot
        cells.append(_render_flashcard_cell(index, card, front=front))
    cells.extend(
        _render_blank_flashcard_cell()
        for _ in range(_FLASHCARDS_PER_SHEET - len(cells))
    )
    return (
        "#block(width: 100%, height: 100%, breakable: false)[\n"
        "#layout(size => grid(columns: (1fr, 1fr), "
        "rows: (size.height / 3, size.height / 3, size.height / 3), "
        "gutter: 0pt,\n" + ",\n".join(cells) + "\n))\n]"
    )


def _render_blank_flashcard_cell() -> str:
    return '[#study-card("", [])]'


def _render_flashcard_cell(
    index: int,
    card: StudyFlashcard,
    *,
    front: bool,
) -> str:
    side = "pergunta" if front else "resposta"
    content = card.front if front else _clean_study_text(card.back)
    return (
        f"[#study-card({typst_string(f'Cartão {index} - {side}')}, "
        f"[{render_typst_prose(content)}])]"
    )


def _mirror_card_backs(
    cards: Sequence[tuple[int, StudyFlashcard]],
) -> tuple[tuple[int, StudyFlashcard] | None, ...]:
    padded: list[tuple[int, StudyFlashcard] | None] = list(cards)
    padded.extend(None for _ in range(_FLASHCARDS_PER_SHEET - len(padded)))
    return tuple(padded[index] for index in (1, 0, 3, 2, 5, 4))
