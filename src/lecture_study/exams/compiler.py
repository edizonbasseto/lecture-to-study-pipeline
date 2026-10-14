"""Mock Exam Compilation (Phase 13): persists one lecture's generated
MockExam as both raw JSON (exam.json) and a human-readable Markdown mock
exam (simulado.md) -- a strict view-vs-data split, matching Phase 10-11's
Markdown-vs-JSON separation precedent (see
docs/decisions/0011-markdown-compilation-and-publishing-pipeline.md).
"""

import json
import re
from pathlib import Path

from lecture_study.domain.models import ArtifactExecutionMode, MockExam, MockQuestion
from lecture_study.quality.delivery import (
    DeliveryArtifactKind,
    write_delivery_outputs,
)
from lecture_study.quality.derivative import DerivativeQualityCertificateVerifier

EXAMS_OUTPUT_DIR = Path("workspace/data/14_mock_exams")

# question_text/options/explanation are free text produced by Phase 13's
# LLM pass (exams/generator.py), not markdown this compiler generates
# itself -- the same class of risk publishing/markdown_compiler.py already
# closed a review-board finding on (HTML-injection and embedded-newline-
# block-injection, Phases 10-11): a stray "#"/"*"/blank line in that text
# could otherwise spoof a heading or corrupt this document's structure
# once interpolated into a Markdown template.
_MARKDOWN_SPECIAL_CHARS = re.compile(r"([\\`*_#+\-\[\]()<>|])")
_EMBEDDED_NEWLINE_RUN = re.compile(r"\s*\n\s*")


class ExamCompiler:
    """Compiles one lecture's MockExam into
    workspace/data/14_mock_exams/{lecture_id}/: exam.json (raw data) and
    simulado.md (a questions-and-alternatives section, a divider, then an
    answer key with clinical justifications)."""

    def __init__(
        self,
        *,
        quality_certificate_verifier: (
            DerivativeQualityCertificateVerifier | None
        ) = None,
    ) -> None:
        self._quality_certificate_verifier = (
            quality_certificate_verifier
            if quality_certificate_verifier is not None
            else DerivativeQualityCertificateVerifier()
        )

    def compile(self, exam: MockExam) -> Path:
        # ADR 0024: the uncertified personal-study route bypasses derivative
        # certification entirely rather than attempt to satisfy it -- every
        # other execution mode keeps requiring a real derivative certificate.
        if exam.execution_mode is not ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY:
            self._quality_certificate_verifier.verify_exam(exam)
        output_bytes = {
            "exam.json": json.dumps(
                exam.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
            ).encode("utf-8"),
            "simulado.md": self._render_markdown(exam).encode("utf-8"),
        }
        write_delivery_outputs(
            EXAMS_OUTPUT_DIR,
            str(exam.lecture_id),
            DeliveryArtifactKind.MOCK_EXAM,
            output_bytes,
        )
        return EXAMS_OUTPUT_DIR / exam.lecture_id

    @classmethod
    def _render_markdown(cls, exam: MockExam) -> str:
        sections = [
            f"# Simulado Clínico -- {exam.lecture_id}",
            cls._render_questions_section(exam.questions),
            "---",
            cls._render_answer_key_section(exam.questions),
        ]
        return "\n\n".join(sections) + "\n"

    @classmethod
    def _render_questions_section(cls, questions: list[MockQuestion]) -> str:
        return "\n\n".join(
            cls._render_question(index, question)
            for index, question in enumerate(questions, start=1)
        )

    @classmethod
    def _render_question(cls, index: int, question: MockQuestion) -> str:
        option_lines = "\n".join(
            f"{letter}) {cls._escape_markdown(text)}"
            for letter, text in sorted(question.options.items())
        )
        return (
            f"**Questão {index}.** {cls._escape_markdown(question.question_text)}"
            f"\n\n{option_lines}"
        )

    @classmethod
    def _render_answer_key_section(cls, questions: list[MockQuestion]) -> str:
        entries = "\n\n".join(
            cls._render_answer(index, question)
            for index, question in enumerate(questions, start=1)
        )
        return f"## Gabarito e Justificativas Clínicas\n\n{entries}"

    @classmethod
    def _render_answer(cls, index: int, question: MockQuestion) -> str:
        return (
            f"**Questão {index}:** Resposta correta -- "
            f"{cls._escape_markdown(question.correct_answer)}"
            f"\n\n{cls._escape_markdown(question.explanation)}"
        )

    @staticmethod
    def _escape_markdown(text: str) -> str:
        single_line = _EMBEDDED_NEWLINE_RUN.sub(" ", text)
        return _MARKDOWN_SPECIAL_CHARS.sub(r"\\\1", single_line)
