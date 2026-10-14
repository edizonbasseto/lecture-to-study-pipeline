"""Offline mock-exam section-title-echo diagnostic tests.

Covers `section_title_echoes` and `render_echo_scan_markdown` against
already-loaded CanonicalLectureDocument/MockExam models. No LLM
inference and no subprocess mocking happens in this suite -- unlike
tests/test_exams.py, this module has nothing to mock: `section_title_echoes`
is a pure function over in-memory Pydantic models.

The literal template-defect fixture text lives only here, never in
src/lecture_study/exams/echo_scan.py itself, so the production module
carries no example of the text it exists to detect.
"""

import pytest

from lecture_study.domain.models import (
    CanonicalLectureDocument,
    CriticReview,
    LearningUnitDraft,
    MockExam,
    MockQuestion,
    ValidatedLearningUnit,
)
from lecture_study.exams.echo_scan import (
    SectionTitleEcho,
    render_echo_scan_markdown,
    section_title_echoes,
)

# The exact shape src/lecture_study/study_materials/bundle.py's
# deterministic _section_question/_flashcard_front produced for the
# defect the student named: the question opens with the section title
# verbatim, then only asks a generic "what should be remembered" --
# no clinical scenario of its own.
_TEMPLATE_DEFECT_QUESTION_TEXT = (
    "Em Bradicardia, o que deve ser recordado sobre a bradicardia sinusal?"
)

_DEFAULT_OPTIONS = {
    "A": "Nenhuma conduta.",
    "B": "Monitorar e investigar causa.",
    "C": "Eutanasia imediata.",
    "D": "Aumentar a dose de anestesico.",
}


def _unit(
    lecture_id: str = "aula-001",
    pack_sequence: int = 1,
    title: str = "Bradicardia",
    body_markdown: str = "Conteudo sobre bradicardia sinusal.",
) -> ValidatedLearningUnit:
    return ValidatedLearningUnit(
        lecture_id=lecture_id,
        pack_sequence=pack_sequence,
        content=LearningUnitDraft(
            lecture_id=lecture_id,
            pack_sequence=pack_sequence,
            title=title,
            body_markdown=body_markdown,
            clinical_warnings=[],
        ),
        review=CriticReview(passed=True, hallucination_score=0.0, feedback="ok"),
    )


def _document(
    lecture_id: str = "aula-001",
    units: list[ValidatedLearningUnit] | None = None,
) -> CanonicalLectureDocument:
    resolved_units = units if units is not None else [_unit(lecture_id)]
    return CanonicalLectureDocument(
        lecture_id=lecture_id,
        total_units=len(resolved_units),
        units=resolved_units,
        assembled_at=1700000000.0,
    )


def _question(question_text: str) -> MockQuestion:
    return MockQuestion(
        question_text=question_text,
        options=_DEFAULT_OPTIONS,
        correct_answer="B",
        explanation="Bradicardia sinusal exige monitoramento contínuo do paciente.",
    )


def _exam(lecture_id: str, *questions: MockQuestion) -> MockExam:
    return MockExam(lecture_id=lecture_id, questions=list(questions))


class TestSectionTitleEchoes:
    def test_reports_the_deterministic_template_defect_shape(self) -> None:
        document = _document(units=[_unit(title="Bradicardia")])
        exam = _exam("aula-001", _question(_TEMPLATE_DEFECT_QUESTION_TEXT))

        echoes = section_title_echoes(exam, document)

        assert echoes == [
            SectionTitleEcho(
                question_index=0,
                question_text=_TEMPLATE_DEFECT_QUESTION_TEXT,
                matched_title="Bradicardia",
            )
        ]

    def test_is_case_and_accent_insensitive(self) -> None:
        document = _document(units=[_unit(title="Excisão")])
        exam = _exam("aula-001", _question("O QUE DEVE SER RECORDADO SOBRE EXCISAO?"))

        echoes = section_title_echoes(exam, document)

        assert len(echoes) == 1
        assert echoes[0].matched_title == "Excisão"

    def test_does_not_report_a_genuine_clinical_reasoning_question(self) -> None:
        # Shares the word "bradicardia" with the title, but is a real
        # scenario asking the reader to choose between two clinical
        # courses of action -- exactly what a title-echo scan must not
        # flag as an echo.
        document = _document(units=[_unit(title="Bradicardia")])
        exam = _exam(
            "aula-001",
            _question(
                "Um cao de 8 anos com sopro cardiaco apresenta bradicardia "
                "acentuada; qual a conduta mais adequada: monitorar sinais "
                "vitais ou iniciar atropina imediatamente?"
            ),
        )

        echoes = section_title_echoes(exam, document)

        assert echoes == []

    def test_reports_only_the_echoing_question_and_keeps_its_index(self) -> None:
        document = _document(units=[_unit(title="Bradicardia")])
        exam = _exam(
            "aula-001",
            _question(
                "Um cao de 8 anos com sopro cardiaco apresenta bradicardia "
                "acentuada; qual a conduta mais adequada: monitorar sinais "
                "vitais ou iniciar atropina imediatamente?"
            ),
            _question("Em Bradicardia, o que deve ser recordado?"),
        )

        echoes = section_title_echoes(exam, document)

        assert [echo.question_index for echo in echoes] == [1]

    def test_reports_nothing_when_no_title_appears_in_any_question(self) -> None:
        document = _document(units=[_unit(title="Excisão")])
        exam = _exam(
            "aula-001",
            _question(
                "Cao de 8 anos apresenta FC 40bpm. Qual a conduta mais indicada?"
            ),
        )

        echoes = section_title_echoes(exam, document)

        assert echoes == []

    def test_performs_no_filesystem_access(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_on_open(*args: object, **kwargs: object) -> None:
            raise AssertionError("section_title_echoes must not touch the filesystem")

        monkeypatch.setattr("builtins.open", _raise_on_open)
        document = _document(units=[_unit(title="Bradicardia")])
        exam = _exam("aula-001", _question(_TEMPLATE_DEFECT_QUESTION_TEXT))

        echoes = section_title_echoes(exam, document)

        assert len(echoes) == 1


class TestRenderEchoScanMarkdown:
    def test_renders_a_row_per_echo_with_one_based_question_numbers(self) -> None:
        echoes = [
            SectionTitleEcho(
                question_index=2,
                question_text=_TEMPLATE_DEFECT_QUESTION_TEXT,
                matched_title="Bradicardia",
            )
        ]

        markdown = render_echo_scan_markdown(echoes)

        assert f"| 3 | {_TEMPLATE_DEFECT_QUESTION_TEXT} | Bradicardia |" in markdown

    def test_renders_header_only_when_no_echoes_detected(self) -> None:
        markdown = render_echo_scan_markdown([])

        assert "Título ecoado" in markdown
        assert markdown.count("|") == 8  # header row + separator row only

    def test_orders_rows_by_question_index_regardless_of_input_order(self) -> None:
        echoes = [
            SectionTitleEcho(question_index=1, question_text="B", matched_title="T"),
            SectionTitleEcho(question_index=0, question_text="A", matched_title="T"),
        ]

        markdown = render_echo_scan_markdown(echoes)

        assert markdown.index("| 1 | A | T |") < markdown.index("| 2 | B | T |")
