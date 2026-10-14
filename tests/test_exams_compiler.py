"""ExamCompiler tests: the deterministic simulado renderer/serializer
(extracted from tests/test_exams.py when the local llama.cpp exam
generator was removed under ADR 0038)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.models import MockExam, MockQuestion
from lecture_study.exams import compiler as compiler_module
from lecture_study.exams.compiler import ExamCompiler
from lecture_study.quality.derivative import DerivativeQualityError


class _AllowingExamQualityVerifier:
    def verify_exam(self, _exam: MockExam) -> None:
        return None


class _RejectingExamQualityVerifier:
    def verify_exam(self, _exam: MockExam) -> None:
        raise DerivativeQualityError("exam certificate rejected")


def _exam_compiler() -> ExamCompiler:
    return ExamCompiler(quality_certificate_verifier=_AllowingExamQualityVerifier())


def _question_payload(
    question_text: str = "Cão de 8 anos apresenta FC 40bpm. Qual a conduta?",
    options: dict[str, str] | None = None,
    correct_answer: str = "B",
    explanation: str = "Bradicardia sinusal exige monitoramento contínuo do paciente.",
) -> dict[str, object]:
    return {
        "question_text": question_text,
        "options": options
        if options is not None
        else {
            "A": "Nenhuma conduta.",
            "B": "Monitorar e investigar causa.",
            "C": "Eutanasia imediata.",
            "D": "Aumentar a dose de anestesico.",
        },
        "correct_answer": correct_answer,
        "explanation": explanation,
    }


def _make_exam(lecture_id: str = "aula-001") -> MockExam:
    return MockExam(
        lecture_id=lecture_id,
        questions=[
            MockQuestion(**_question_payload()),
            MockQuestion(
                **_question_payload(
                    question_text="Qual a dosagem correta de dipirona?",
                    correct_answer="A",
                )
            ),
        ],
    )


class TestMockQuestionContract:
    def test_rejects_options_with_fewer_than_four_keys(self):
        with pytest.raises(ValidationError, match="A.*B.*C.*D"):
            MockQuestion(
                **_question_payload(options={"A": "a", "B": "b"}, correct_answer="A")
            )

    def test_rejects_options_not_keyed_a_through_d(self):
        with pytest.raises(ValidationError, match="A.*B.*C.*D"):
            MockQuestion(
                **_question_payload(
                    options={"opt1": "a", "opt2": "b", "opt3": "c", "opt4": "d"},
                    correct_answer="opt1",
                )
            )

    def test_accepts_exactly_four_options_keyed_a_through_d(self):
        question = MockQuestion(**_question_payload())

        assert set(question.options) == {"A", "B", "C", "D"}


class TestExamCompiler:
    def test_compile_rejects_before_creating_output_directory(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        output_root = tmp_path / "exams"
        monkeypatch.setattr(compiler_module, "EXAMS_OUTPUT_DIR", output_root)
        compiler = ExamCompiler(
            quality_certificate_verifier=_RejectingExamQualityVerifier()
        )

        with pytest.raises(DerivativeQualityError, match="certificate rejected"):
            compiler.compile(_make_exam())

        assert not output_root.exists()

    def test_compile_persists_raw_json_round_trippable_to_the_same_exam(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "EXAMS_OUTPUT_DIR", tmp_path)
        exam = _make_exam()

        output_dir = _exam_compiler().compile(exam)

        assert output_dir == tmp_path / "aula-001"
        persisted = MockExam.model_validate_json((output_dir / "exam.json").read_text())
        assert persisted == exam

    def test_compile_renders_questions_options_in_letter_order_and_divider(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "EXAMS_OUTPUT_DIR", tmp_path)
        exam = MockExam(
            lecture_id="aula-001",
            questions=[
                MockQuestion(
                    question_text="Qual o diagnostico?",
                    options={
                        "C": "Opcao C",
                        "A": "Opcao A",
                        "D": "Opcao D",
                        "B": "Opcao B",
                    },
                    correct_answer="A",
                    explanation="Justificativa clinica.",
                )
            ],
        )

        output_dir = _exam_compiler().compile(exam)

        markdown = (output_dir / "simulado.md").read_text()
        assert markdown.index("A) Opcao A") < markdown.index("B) Opcao B")
        assert markdown.index("B) Opcao B") < markdown.index("C) Opcao C")
        assert markdown.index("C) Opcao C") < markdown.index("D) Opcao D")
        assert markdown.index("Opcao D") < markdown.index("---")
        assert markdown.index("---") < markdown.index(
            "## Gabarito e Justificativas Clínicas"
        )
        assert "Resposta correta -- A" in markdown
        assert "Justificativa clinica." in markdown

    def test_compile_escapes_markdown_special_characters_in_free_text_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # question_text/options/explanation originate in Phase 13's LLM
        # pass, not markdown this compiler authors -- a stray "#"/blank
        # line must not spoof a heading or fracture simulado.md's
        # structure, matching Phase 10-11's markdown_compiler.py
        # precedent.
        monkeypatch.setattr(compiler_module, "EXAMS_OUTPUT_DIR", tmp_path)
        exam = MockExam(
            lecture_id="aula-001",
            questions=[
                MockQuestion(
                    question_text="Caso\n\n## Fake Injected Heading",
                    options={"A": "*itálico falso*", "B": "b", "C": "c", "D": "d"},
                    correct_answer="A",
                    explanation="Alerta\n\n# Fake Injected Heading 2",
                )
            ],
        )

        output_dir = _exam_compiler().compile(exam)

        markdown = (output_dir / "simulado.md").read_text()
        assert "\n## Fake Injected Heading" not in markdown
        assert "\n# Fake Injected Heading 2" not in markdown
        assert "\\#\\# Fake Injected Heading" in markdown
        assert "\\*itálico falso\\*" in markdown

    def test_compile_collapses_embedded_newlines_in_free_text_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "EXAMS_OUTPUT_DIR", tmp_path)
        exam = MockExam(
            lecture_id="aula-001",
            questions=[
                MockQuestion(
                    question_text="Caso clinico\n\n1. Item de lista falso",
                    options={"A": "a", "B": "b", "C": "c", "D": "d"},
                    correct_answer="A",
                    explanation="ok",
                )
            ],
        )

        output_dir = _exam_compiler().compile(exam)

        markdown = (output_dir / "simulado.md").read_text()
        assert "\n\n1." not in markdown

    def test_compile_creates_lecture_output_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "EXAMS_OUTPUT_DIR", tmp_path)
        exam = _make_exam(lecture_id="aula-999")

        output_dir = _exam_compiler().compile(exam)

        assert output_dir.is_dir()
        assert output_dir == tmp_path / "aula-999"

    def test_compile_writes_atomically_leaving_no_tmp_files_behind(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "EXAMS_OUTPUT_DIR", tmp_path)
        exam = _make_exam()

        output_dir = _exam_compiler().compile(exam)

        remaining = sorted(path.name for path in output_dir.iterdir())
        assert remaining == ["exam.json", "simulado.md"]

    def test_compile_recovers_after_a_partial_previous_write(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "EXAMS_OUTPUT_DIR", tmp_path)
        lecture_dir = tmp_path / "aula-001"
        lecture_dir.mkdir(parents=True)
        (lecture_dir / "exam.json").write_text("stale content")
        exam = _make_exam()

        output_dir = _exam_compiler().compile(exam)

        assert "stale content" not in (output_dir / "exam.json").read_text()
