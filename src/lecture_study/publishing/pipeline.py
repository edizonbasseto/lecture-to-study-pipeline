"""Publishing Pipeline (Phase 11): orchestrates the terminal handoff --
compiling a lecture's Phase 8 CanonicalLectureDocument and Phase 9
LectureGlobalIndex into a Markdown study guide, then rendering that
Markdown to a static HTML page.

No llama.cpp, no model inference: pure deterministic file I/O and string
templating, matching Phases 8-10's "no LLM" precedent.
"""

from pathlib import Path

from markdown_it import MarkdownIt
from pydantic import TypeAdapter

from lecture_study.domain.errors import LectureNotFoundError
from lecture_study.domain.models import (
    CanonicalLectureDocument,
    LectureGlobalIndex,
    SafeIdentifier,
)
from lecture_study.publishing.markdown_compiler import MarkdownCompiler
from lecture_study.quality import (
    DeliveryArtifactKind,
    issue_deterministic_delivery,
    require_publication_ready,
)

CANONICAL_DOCUMENTS_INPUT_DIR = Path("workspace/data/10_canonical_documents")
METADATA_INPUT_DIR = Path("workspace/data/11_metadata")
PUBLISHED_OUTPUT_DIR = Path("workspace/data/12_published")

_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)
# "commonmark", html=False: raw HTML in source Markdown (e.g. an
# OCR/LLM-derived SemanticFact.clinical_context that happens to contain
# "<script>...") is escaped to literal text in the rendered output
# instead of passed through -- CommonMark's default html=True would
# otherwise let it execute when study_guide.html is opened in a browser.
_HTML_RENDERER = MarkdownIt("commonmark", {"html": False})


class PublishingOrchestrator:
    """Publishes one lecture's study guide: Markdown compilation followed
    by HTML rendering, both written to
    workspace/data/12_published/{lecture_id}/."""

    def publish_lecture(self, lecture_id: str) -> Path:
        lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
        verified_inputs = require_publication_ready(str(lecture_id))
        document = verified_inputs.document
        index = verified_inputs.global_index

        study_guide_markdown = MarkdownCompiler().compile(document, index)
        output_bytes = {
            "study_guide.md": study_guide_markdown.encode("utf-8"),
            "study_guide.html": self._render_html(study_guide_markdown).encode("utf-8"),
        }
        issue_deterministic_delivery(
            str(lecture_id),
            DeliveryArtifactKind.PUBLISHING,
            expected_output_bytes=output_bytes,
        )
        return PUBLISHED_OUTPUT_DIR / lecture_id

    @staticmethod
    def _load_canonical_document(lecture_id: str) -> CanonicalLectureDocument:
        document_path = (
            CANONICAL_DOCUMENTS_INPUT_DIR / lecture_id / "canonical_document.json"
        )
        if not document_path.exists():
            raise LectureNotFoundError(
                f"no {document_path} file for lecture {lecture_id!r}: "
                "Phase 8 has not produced a canonical document for this lecture"
            )
        return CanonicalLectureDocument.model_validate_json(document_path.read_text())

    @staticmethod
    def _load_global_index(lecture_id: str) -> LectureGlobalIndex:
        # Reads Phase 9's persisted artifact directly, rather than calling
        # MetadataAggregator.aggregate_lecture, so this phase stays
        # self-contained (imports only domain.models, per ADR 0006) and
        # depends only on the one file Phase 9 actually promises to
        # produce -- matching every other phase's "read the prior phase's
        # persisted artifact, don't recompute it" convention.
        index_path = METADATA_INPUT_DIR / lecture_id / "global_index.json"
        if not index_path.exists():
            raise LectureNotFoundError(
                f"no {index_path} file for lecture {lecture_id!r}: "
                "Phase 9 has not produced a global index for this lecture"
            )
        return LectureGlobalIndex.model_validate_json(index_path.read_text())

    @staticmethod
    def _render_html(study_guide_markdown: str) -> str:
        body = _HTML_RENDERER.render(study_guide_markdown)
        return (
            "<!DOCTYPE html>\n"
            '<html lang="pt-BR">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            "<title>Study Guide</title>\n"
            "</head>\n"
            f"<body>\n{body}</body>\n"
            "</html>\n"
        )
