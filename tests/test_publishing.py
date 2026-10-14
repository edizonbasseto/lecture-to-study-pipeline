"""Publishing Pipeline (Phases 10-11) tests: Markdown compilation of a
lecture's CanonicalLectureDocument/LectureGlobalIndex, and the
orchestrator that compiles then renders that Markdown to HTML. No
LLM/subprocess involved -- pure data transformation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.errors import LectureNotFoundError
from lecture_study.domain.models import (
    CanonicalLectureDocument,
    CriticReview,
    LearningUnitDraft,
    LectureGlobalIndex,
    SemanticFact,
    ValidatedLearningUnit,
)
from lecture_study.publishing import pipeline as pipeline_module
from lecture_study.publishing.markdown_compiler import MarkdownCompiler
from lecture_study.publishing.pipeline import PublishingOrchestrator
from lecture_study.quality.delivery import (
    DeliveryArtifactKind,
    DeliveryVerificationError,
)


pytestmark = pytest.mark.usefixtures("legacy_certification_bypass")


def _make_unit(
    lecture_id: str = "aula-001", pack_sequence: int = 1, title: str = "Bradicardia"
) -> ValidatedLearningUnit:
    return ValidatedLearningUnit(
        lecture_id=lecture_id,
        pack_sequence=pack_sequence,
        content=LearningUnitDraft(
            lecture_id=lecture_id,
            pack_sequence=pack_sequence,
            title=title,
            body_markdown=f"Conteudo de {title}.",
            clinical_warnings=[],
        ),
        review=CriticReview(passed=True, hallucination_score=0.0, feedback="ok"),
    )


def _make_document(
    lecture_id: str = "aula-001", units: list[ValidatedLearningUnit] | None = None
) -> CanonicalLectureDocument:
    resolved_units = units if units is not None else [_make_unit(lecture_id)]
    return CanonicalLectureDocument(
        lecture_id=lecture_id,
        total_units=len(resolved_units),
        units=resolved_units,
        assembled_at=1700000000.0,
    )


def _make_fact(**overrides: object) -> SemanticFact:
    defaults: dict[str, object] = dict(
        entity_name="Aspirina",
        category="dosagem",
        clinical_context="100mg via oral a cada 8 horas.",
        timestamp_ref=12.5,
    )
    defaults.update(overrides)
    return SemanticFact(**defaults)


class TestMarkdownCompiler:
    def test_compile_renders_header_lecture_id_and_date(self):
        document = _make_document()
        index = LectureGlobalIndex(
            lecture_id="aula-001", glossary_terms=[], dosages=[], red_flags=[]
        )

        markdown = MarkdownCompiler().compile(document, index)

        assert "# aula-001" in markdown
        assert "**Data:** 2023-11-14" in markdown

    def test_compile_renders_units_in_order_with_titles_and_body(self):
        document = _make_document(
            units=[
                _make_unit(pack_sequence=1, title="Primeira Unidade"),
                _make_unit(pack_sequence=2, title="Segunda Unidade"),
            ]
        )
        index = LectureGlobalIndex(
            lecture_id="aula-001", glossary_terms=[], dosages=[], red_flags=[]
        )

        markdown = MarkdownCompiler().compile(document, index)

        first_index = markdown.index("## Primeira Unidade")
        second_index = markdown.index("## Segunda Unidade")
        assert first_index < second_index
        assert "Conteudo de Primeira Unidade." in markdown
        assert "Conteudo de Segunda Unidade." in markdown

    def test_compile_renders_placeholder_when_no_units(self):
        document = _make_document(units=[])
        index = LectureGlobalIndex(
            lecture_id="aula-001", glossary_terms=[], dosages=[], red_flags=[]
        )

        markdown = MarkdownCompiler().compile(document, index)

        assert "Nenhuma unidade de aprendizagem disponível" in markdown

    def test_compile_renders_glossary_dosages_and_red_flags(self):
        document = _make_document()
        dosage_fact = _make_fact(entity_name="Aspirina", clinical_context="100mg VO.")
        red_flag_fact = _make_fact(
            entity_name="Gravidez", clinical_context="Evitar uso na gravidez."
        )
        index = LectureGlobalIndex(
            lecture_id="aula-001",
            glossary_terms=[_make_fact(entity_name="Bradicardia")],
            dosages=[dosage_fact],
            red_flags=[red_flag_fact],
        )

        markdown = MarkdownCompiler().compile(document, index)

        assert "## Apêndice Clínico" in markdown
        assert "### Glossário" in markdown
        assert "- Bradicardia" in markdown
        assert "### Dosagens" in markdown
        assert "**Aspirina**: 100mg VO." in markdown
        assert "### Alertas Vermelhos" in markdown
        assert "**Gravidez**: Evitar uso na gravidez." in markdown

    def test_compile_renders_empty_appendix_placeholders(self):
        document = _make_document()
        index = LectureGlobalIndex(
            lecture_id="aula-001", glossary_terms=[], dosages=[], red_flags=[]
        )

        markdown = MarkdownCompiler().compile(document, index)

        assert "Nenhum termo catalogado." in markdown
        assert markdown.count("Nenhum registro catalogado.") == 2

    def test_compile_escapes_markdown_special_characters_in_free_text_fields(self):
        document = _make_document(units=[_make_unit(title="# Injected Heading *bold*")])
        adversarial_fact = _make_fact(
            entity_name="Droga # 1",
            clinical_context="Use com cautela [ver nota] (grave).",
        )
        index = LectureGlobalIndex(
            lecture_id="aula-001",
            glossary_terms=[_make_fact(entity_name="Termo * com _ especiais_")],
            dosages=[adversarial_fact],
            red_flags=[],
        )

        markdown = MarkdownCompiler().compile(document, index)

        assert "## \\# Injected Heading \\*bold\\*" in markdown
        assert "- Termo \\* com \\_ especiais\\_" in markdown
        assert "**Droga \\# 1**" in markdown
        assert "Use com cautela \\[ver nota\\] \\(grave\\)." in markdown
        # body_markdown itself is Phase 6's own generated markdown and
        # must be left unescaped.
        assert "Conteudo de # Injected Heading *bold*." in markdown

    def test_compile_collapses_embedded_newlines_in_free_text_fields(self):
        # A blank line (two consecutive newlines) inside clinical_context
        # starts a genuine new markdown block regardless of punctuation
        # escaping -- e.g. a numbered-list marker ("1. ...") carries no
        # punctuation _MARKDOWN_SPECIAL_CHARS escapes. Collapsing embedded
        # newlines to a single space keeps the fact on its own bullet
        # line instead of fracturing into an orphaned second block.
        document = _make_document()
        adversarial_fact = _make_fact(
            entity_name="Droga X",
            clinical_context=(
                "Contexto normal.\n\n1. Item forjado injetado no documento"
            ),
        )
        index = LectureGlobalIndex(
            lecture_id="aula-001",
            glossary_terms=[],
            dosages=[adversarial_fact],
            red_flags=[],
        )

        markdown = MarkdownCompiler().compile(document, index)

        assert (
            "- **Droga X**: Contexto normal. "
            "1. Item forjado injetado no documento" in markdown
        )
        assert "\n\n1." not in markdown


def _setup_pipeline_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "CANONICAL_DOCUMENTS_INPUT_DIR",
        tmp_path / "10_canonical_documents",
    )
    monkeypatch.setattr(pipeline_module, "METADATA_INPUT_DIR", tmp_path / "11_metadata")
    monkeypatch.setattr(
        pipeline_module, "PUBLISHED_OUTPUT_DIR", tmp_path / "12_published"
    )


def _write_canonical_document(
    tmp_path: Path, document: CanonicalLectureDocument
) -> None:
    lecture_dir = tmp_path / "10_canonical_documents" / document.lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / "canonical_document.json").write_text(document.model_dump_json())


def _write_global_index(tmp_path: Path, index: LectureGlobalIndex) -> None:
    lecture_dir = tmp_path / "11_metadata" / index.lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / "global_index.json").write_text(index.model_dump_json())


class TestPublishingOrchestrator:
    def test_publish_lecture_writes_markdown_and_html(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        _setup_pipeline_dirs(monkeypatch, tmp_path)
        _write_canonical_document(tmp_path, _make_document())
        _write_global_index(
            tmp_path,
            LectureGlobalIndex(
                lecture_id="aula-001",
                glossary_terms=[],
                dosages=[_make_fact()],
                red_flags=[],
            ),
        )

        output_dir = PublishingOrchestrator().publish_lecture("aula-001")

        assert output_dir == tmp_path / "12_published" / "aula-001"
        markdown_path = output_dir / "study_guide.md"
        html_path = output_dir / "study_guide.html"
        assert markdown_path.exists()
        assert html_path.exists()
        markdown_text = markdown_path.read_text()
        assert "# aula-001" in markdown_text
        assert "Aspirina" in markdown_text
        html_text = html_path.read_text()
        assert "<!DOCTYPE html>" in html_text
        assert "aula-001" in html_text

    def test_issuer_preflight_failure_preserves_previous_published_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _setup_pipeline_dirs(monkeypatch, tmp_path)
        _write_canonical_document(tmp_path, _make_document())
        _write_global_index(
            tmp_path,
            LectureGlobalIndex(
                lecture_id="aula-001",
                glossary_terms=[],
                dosages=[_make_fact()],
                red_flags=[],
            ),
        )
        output_dir = tmp_path / "12_published" / "aula-001"
        output_dir.mkdir(parents=True)
        previous_output_bytes = {
            "study_guide.md": b"# Previously certified\n",
            "study_guide.html": b"<main>Previously certified</main>",
        }
        for relative_path, payload in previous_output_bytes.items():
            (output_dir / relative_path).write_bytes(payload)

        def reject_preflight(
            _lecture_id: str,
            _artifact_kind: DeliveryArtifactKind,
            *,
            expected_output_bytes: dict[str, bytes],
        ) -> None:
            assert expected_output_bytes != previous_output_bytes
            raise DeliveryVerificationError("injected issuer preflight failure")

        monkeypatch.setattr(
            pipeline_module,
            "issue_deterministic_delivery",
            reject_preflight,
        )

        with pytest.raises(
            DeliveryVerificationError,
            match="injected issuer preflight failure",
        ):
            PublishingOrchestrator().publish_lecture("aula-001")

        assert {
            path.name: path.read_bytes() for path in output_dir.iterdir()
        } == previous_output_bytes

    def test_publish_lecture_escapes_html_and_markdown_in_clinical_facts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # entity_name/clinical_context are LLM-extracted free text from
        # OCR'd slides/transcripts, not text this pipeline authors -- a
        # stray "<script>", a "#"/blank-line combination, or an embedded
        # blank line before a numbered-list marker must not execute as
        # script or fracture the document's markdown structure once
        # study_guide.html is opened in a browser.
        _setup_pipeline_dirs(monkeypatch, tmp_path)
        _write_canonical_document(tmp_path, _make_document())
        adversarial_fact = _make_fact(
            entity_name="Droga\n\n## Fake Injected Heading",
            clinical_context=(
                "<script>alert(1)</script>\n\n1. Fake injected list item"
            ),
        )
        _write_global_index(
            tmp_path,
            LectureGlobalIndex(
                lecture_id="aula-001",
                glossary_terms=[],
                dosages=[adversarial_fact],
                red_flags=[],
            ),
        )

        output_dir = PublishingOrchestrator().publish_lecture("aula-001")

        html_text = (output_dir / "study_guide.html").read_text()
        assert "<h2>Fake Injected Heading</h2>" not in html_text
        assert "<script>alert(1)</script>" not in html_text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text
        assert "<ol>" not in html_text
        assert "Fake injected list item" in html_text

    def test_publish_lecture_recovers_after_a_partial_previous_write(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # Simulates a crash between the .md and .html writes: re-running
        # publish_lecture must overwrite the stale/partial output rather
        # than fail or leave it inconsistent.
        _setup_pipeline_dirs(monkeypatch, tmp_path)
        _write_canonical_document(tmp_path, _make_document())
        _write_global_index(
            tmp_path,
            LectureGlobalIndex(
                lecture_id="aula-001", glossary_terms=[], dosages=[], red_flags=[]
            ),
        )
        lecture_dir = tmp_path / "12_published" / "aula-001"
        lecture_dir.mkdir(parents=True)
        (lecture_dir / "study_guide.md").write_text("stale content")

        output_dir = PublishingOrchestrator().publish_lecture("aula-001")

        assert "stale content" not in (output_dir / "study_guide.md").read_text()
        assert (output_dir / "study_guide.html").exists()

    def test_publish_lecture_raises_lecture_not_found_when_canonical_document_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        _setup_pipeline_dirs(monkeypatch, tmp_path)

        with pytest.raises(LectureNotFoundError, match="aula-001"):
            PublishingOrchestrator().publish_lecture("aula-001")

        assert not (tmp_path / "12_published").exists()

    def test_publish_lecture_raises_lecture_not_found_when_global_index_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        _setup_pipeline_dirs(monkeypatch, tmp_path)
        _write_canonical_document(tmp_path, _make_document())

        with pytest.raises(LectureNotFoundError, match="aula-001"):
            PublishingOrchestrator().publish_lecture("aula-001")

        assert not (tmp_path / "12_published").exists()

    def test_publish_lecture_rejects_malformed_lecture_id_before_touching_disk(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        _setup_pipeline_dirs(monkeypatch, tmp_path)

        with pytest.raises(ValidationError):
            PublishingOrchestrator().publish_lecture("../../etc")

        assert list(tmp_path.iterdir()) == []
