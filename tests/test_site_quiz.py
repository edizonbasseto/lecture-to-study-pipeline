"""Site quiz tests (SITE-09): server-side grading of a lecture's compiled
mock exam, never exposing an answer before grading happens."""

import json
from pathlib import Path

import pytest

from lecture_study.domain.models import MockExam, MockQuestion
from lecture_study.site import quiz


def _question(
    question_text: str = "Cão de 8 anos com FC 40bpm. Qual a conduta?",
    correct_answer: str = "B",
) -> MockQuestion:
    return MockQuestion(
        question_text=question_text,
        options={
            "A": "Nenhuma conduta.",
            "B": "Monitorar e investigar causa.",
            "C": "Eutanasia imediata.",
            "D": "Aumentar a dose de anestesico.",
        },
        correct_answer=correct_answer,
        explanation="Bradicardia sinusal exige monitoramento contínuo.",
    )


def _write_exam(directory: Path, lecture_id: str, exam: MockExam) -> None:
    exam_dir = directory / lecture_id
    exam_dir.mkdir(parents=True)
    (exam_dir / "exam.json").write_text(
        json.dumps(exam.model_dump(mode="json")), encoding="utf-8"
    )


class TestReadExam:
    def test_reads_a_compiled_exam(self, tmp_path: Path) -> None:
        exam = MockExam(lecture_id="01aula", questions=[_question()])
        _write_exam(tmp_path, "01aula", exam)

        loaded = quiz.read_exam("01aula", tmp_path)

        assert loaded.lecture_id == "01aula"
        assert len(loaded.questions) == 1

    def test_missing_exam_raises(self, tmp_path: Path) -> None:
        with pytest.raises(quiz.ExamNotFoundError):
            quiz.read_exam("naoexiste", tmp_path)

    @pytest.mark.parametrize(
        "unsafe_id", ["../../etc/passwd", "01/../02", "01 aula", ""]
    )
    def test_unsafe_lecture_id_raises_without_touching_disk(
        self, tmp_path: Path, unsafe_id: str
    ) -> None:
        with pytest.raises(quiz.ExamNotFoundError):
            quiz.read_exam(unsafe_id, tmp_path)


class TestPublicView:
    def test_strips_every_answer_bearing_field(self) -> None:
        exam = MockExam(lecture_id="01aula", questions=[_question()])

        public = quiz.public_view(exam)

        rendered = repr(public)
        assert "correct_answer" not in rendered
        assert "Bradicardia" not in rendered  # the explanation text
        assert "B" == exam.questions[0].correct_answer  # sanity: it does exist
        assert public[0].question_text == exam.questions[0].question_text
        assert public[0].options == exam.questions[0].options


class TestGrade:
    def test_scores_correct_and_incorrect_answers(self) -> None:
        exam = MockExam(
            lecture_id="01aula",
            questions=[
                _question(correct_answer="B"),
                _question(correct_answer="D"),
            ],
        )

        graded = quiz.grade("01aula", exam, {0: "B", 1: "A"})

        assert graded.score == 1
        assert graded.total == 2
        assert graded.questions[0].is_correct is True
        assert graded.questions[1].is_correct is False
        assert graded.questions[1].correct_answer == "D"

    def test_unanswered_question_is_graded_as_wrong_not_missing(self) -> None:
        exam = MockExam(lecture_id="01aula", questions=[_question()])

        graded = quiz.grade("01aula", exam, {})

        assert graded.total == 1
        assert graded.score == 0
        assert graded.questions[0].submitted_answer is None
        assert graded.questions[0].is_correct is False

    def test_an_index_naming_no_real_question_is_simply_ignored(self) -> None:
        """A submission cannot inject a question that does not exist in
        this exam -- grading only ever iterates the exam's own questions,
        never the submitted indices."""
        exam = MockExam(lecture_id="01aula", questions=[_question()])

        graded = quiz.grade("01aula", exam, {0: "B", 99: "A"})

        assert graded.total == 1
        assert graded.questions[0].index == 0

    def test_grading_one_lecture_never_reaches_into_another(
        self, tmp_path: Path
    ) -> None:
        """Cross-lecture answer injection: a submission naming lecture A's
        question shape cannot be graded against lecture B's answer key,
        because grading always re-reads the *claimed* lecture's own exam
        from disk and scores by position within it alone."""
        exam_a = MockExam(lecture_id="aulaa", questions=[_question(correct_answer="A")])
        exam_b = MockExam(lecture_id="aulab", questions=[_question(correct_answer="C")])
        _write_exam(tmp_path, "aulaa", exam_a)
        _write_exam(tmp_path, "aulab", exam_b)

        loaded_b = quiz.read_exam("aulab", tmp_path)
        graded = quiz.grade("aulab", loaded_b, {0: "A"})  # A's correct answer, not B's

        assert graded.questions[0].correct_answer == "C"
        assert graded.questions[0].is_correct is False

    def test_percentage_and_error_count_are_derived_from_score_and_total(
        self,
    ) -> None:
        exam = MockExam(
            lecture_id="01aula",
            questions=[
                _question(correct_answer="B"),
                _question(correct_answer="B"),
                _question(correct_answer="B"),
                _question(correct_answer="B"),
            ],
        )

        graded = quiz.grade("01aula", exam, {0: "B", 1: "B", 2: "B", 3: "A"})

        assert graded.percentage_correct == 75
        assert graded.errors == 1


class TestParseSubmittedAnswers:
    def test_reads_well_formed_fields(self) -> None:
        answers = quiz.parse_submitted_answers({"q0": "A", "q1": "C"})

        assert answers == {0: "A", 1: "C"}

    def test_ignores_fields_that_are_not_a_question_index(self) -> None:
        answers = quiz.parse_submitted_answers({"nonsense": "A", "q": "B"})

        assert answers == {}

    def test_ignores_a_value_that_is_not_a_declared_letter(self) -> None:
        answers = quiz.parse_submitted_answers({"q0": "Z", "q1": "correct_answer"})

        assert answers == {}


class TestRenderQuizForm:
    def test_never_renders_the_correct_answer_or_explanation(self) -> None:
        exam = MockExam(lecture_id="01aula", questions=[_question()])

        html = quiz.render_quiz_form("01aula", quiz.public_view(exam))

        assert "correct_answer" not in html
        assert "Bradicardia" not in html  # the explanation text
        assert exam.questions[0].question_text in html

    def test_renders_one_radio_input_per_option(self) -> None:
        exam = MockExam(lecture_id="01aula", questions=[_question()])

        html = quiz.render_quiz_form("01aula", quiz.public_view(exam))

        assert html.count('type="radio"') == 4


class TestRenderQuizResults:
    def test_reveals_the_answer_key_only_after_grading(self) -> None:
        exam = MockExam(lecture_id="01aula", questions=[_question(correct_answer="B")])
        graded = quiz.grade("01aula", exam, {0: "B"})

        html = quiz.render_quiz_results(graded)

        assert "Monitorar e investigar causa." in html
        assert "Bradicardia" in html
        assert "Nota: 1 de 1" in html
        assert "100% de acerto" in html
        assert "0 erros)" in html

    def test_a_single_error_is_singular_not_plural(self) -> None:
        exam = MockExam(
            lecture_id="01aula",
            questions=[
                _question(correct_answer="B"),
                _question(correct_answer="B"),
            ],
        )
        graded = quiz.grade("01aula", exam, {0: "B", 1: "A"})

        html = quiz.render_quiz_results(graded)

        assert "1 erro)" in html
        assert "1 erros)" not in html


class TestGradedExamWithNoQuestions:
    def test_percentage_and_render_never_divide_by_zero(self) -> None:
        exam = MockExam(lecture_id="01aula", questions=[])

        graded = quiz.grade("01aula", exam, {})

        assert graded.percentage_correct == 0
        assert graded.errors == 0
        assert graded.grade_out_of_ten == 0.0
        html = quiz.render_quiz_results(graded)
        assert "Nota: 0 de 0" in html
        assert "0% de acerto" in html
        assert "0,0 de 10" in html


class TestGradeOutOfTen:
    def test_a_rounding_boundary_case_matches_the_computed_expectation(self) -> None:
        # 1/8 correct -> 1.25 on the 0-10 scale, a round-half tie; Python's
        # round() is round-half-to-even, so this pins the actual behavior
        # (1.2) rather than a convenient exact value with no rounding.
        exam = MockExam(
            lecture_id="01aula",
            questions=[_question(correct_answer="B") for _ in range(8)],
        )

        graded = quiz.grade("01aula", exam, {0: "B"})

        assert graded.grade_out_of_ten == round(10 * 1 / 8, 1)
        assert graded.grade_out_of_ten == 1.2

    def test_grade_uses_the_brazilian_0_to_10_scale_with_a_comma(self) -> None:
        exam = MockExam(
            lecture_id="01aula",
            questions=[
                _question(correct_answer="B"),
                _question(correct_answer="B"),
                _question(correct_answer="B"),
                _question(correct_answer="B"),
            ],
        )

        graded = quiz.grade("01aula", exam, {0: "B", 1: "B", 2: "B", 3: "A"})

        assert graded.grade_out_of_ten == 7.5
        html = quiz.render_quiz_results(graded)
        assert "7,5 de 10" in html
        assert "7.5" not in html


class TestEscaping:
    def test_a_hostile_question_text_is_rendered_inert_in_the_form(self) -> None:
        """Mutation-testing gap closed: proves `escape()` is load-bearing,
        not incidental -- deleting it would fail this test."""
        exam = MockExam(
            lecture_id="01aula",
            questions=[_question(question_text="<script>alert(1)</script>")],
        )

        html = quiz.render_quiz_form("01aula", quiz.public_view(exam))

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_a_hostile_explanation_is_rendered_inert_in_the_results(self) -> None:
        exam = MockExam(
            lecture_id="01aula",
            questions=[
                MockQuestion(
                    question_text="Q",
                    options={"A": "a", "B": "b", "C": "c", "D": "d"},
                    correct_answer="A",
                    explanation="<img src=x onerror=alert(1)>",
                )
            ],
        )
        graded = quiz.grade("01aula", exam, {0: "A"})

        html = quiz.render_quiz_results(graded)

        assert "<img src=x onerror=alert(1)>" not in html
        assert "&lt;img" in html
