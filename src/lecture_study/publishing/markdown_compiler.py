"""Markdown Compilation (Phase 10): deterministic rendering of one
lecture's Phase 8 CanonicalLectureDocument and Phase 9 LectureGlobalIndex
into a single well-formatted Markdown study guide.

No llama.cpp, no model inference -- pure string templating over content
already vetted by the Critic Repair Loop (Phase 7) and deduplicated by
Phase 9, matching Phases 8-9's "no LLM" precedent one step later.
"""

import re
from datetime import UTC, datetime

from lecture_study.domain.models import (
    CanonicalLectureDocument,
    LectureGlobalIndex,
    SemanticFact,
)

# entity_name/clinical_context (SemanticFact) and a unit's title are free
# text an LLM extracted from OCR'd slides/transcripts (Phases 5-6) -- not
# markdown this compiler generates itself, unlike body_markdown, which
# Phase 6 writes as markdown by design and is deliberately left
# unescaped. Backslash-escaping CommonMark's ASCII punctuation in these
# fields (per the spec's own escaping rules) neutralizes a stray "#" or
# embedded blank line from turning into a spurious heading, or an
# unbalanced "*"/"`" from corrupting the rest of the document's
# formatting -- a structural-integrity concern independent of
# publishing/pipeline.py's html=False HTML-escaping, which only stops
# raw HTML/script passthrough, not markdown-syntax corruption.
_MARKDOWN_SPECIAL_CHARS = re.compile(r"([\\`*_#+\-\[\]()<>|])")
# Collapses any run of whitespace containing a newline into a single
# space. Backslash-escaping punctuation alone (above) does not stop an
# embedded blank line (two consecutive newlines) in free text from
# starting a genuine new block -- e.g. a numbered-list marker ("1. ...")
# has no punctuation this compiler escapes. Every call site interpolates
# these fields as a single line within a template prefix ("- **{...}**:
# ", "## {...}"), so collapsing internal newlines guarantees the field's
# own content can never itself begin a fresh line, closing that vector
# entirely rather than chasing each block-starting marker individually.
_EMBEDDED_NEWLINE_RUN = re.compile(r"\s*\n\s*")


class MarkdownCompiler:
    """Compiles a lecture's CanonicalLectureDocument and LectureGlobalIndex
    into one Markdown study guide: a header, the lecture body in
    pack_sequence order, and a clinical appendix (glossary, dosages, red
    flags)."""

    def compile(
        self, document: CanonicalLectureDocument, index: LectureGlobalIndex
    ) -> str:
        sections = [
            self._render_header(document),
            self._render_body(document),
            self._render_appendix(index),
        ]
        return "\n\n".join(sections) + "\n"

    @staticmethod
    def _render_header(document: CanonicalLectureDocument) -> str:
        assembled_date = datetime.fromtimestamp(document.assembled_at, tz=UTC).strftime(
            "%Y-%m-%d"
        )
        return f"# {document.lecture_id}\n\n**Data:** {assembled_date}"

    @classmethod
    def _render_body(cls, document: CanonicalLectureDocument) -> str:
        if not document.units:
            return "_Nenhuma unidade de aprendizagem disponível para esta aula._"
        return "\n\n".join(
            f"## {cls._escape_markdown(unit.content.title)}"
            f"\n\n{unit.content.body_markdown}"
            for unit in document.units
        )

    @classmethod
    def _render_appendix(cls, index: LectureGlobalIndex) -> str:
        return "\n\n".join(
            [
                "## Apêndice Clínico",
                cls._render_glossary(index.glossary_terms),
                cls._render_facts("Dosagens", index.dosages),
                cls._render_facts("Alertas Vermelhos", index.red_flags),
            ]
        )

    @classmethod
    def _render_glossary(cls, terms: list[SemanticFact]) -> str:
        if not terms:
            return "### Glossário\n\nNenhum termo catalogado."
        bullet_lines = "\n".join(
            f"- {cls._escape_markdown(fact.entity_name)}" for fact in terms
        )
        return f"### Glossário\n\n{bullet_lines}"

    @classmethod
    def _render_facts(cls, title: str, facts: list[SemanticFact]) -> str:
        if not facts:
            return f"### {title}\n\nNenhum registro catalogado."
        bullet_lines = "\n".join(
            f"- **{cls._escape_markdown(fact.entity_name)}**: "
            f"{cls._escape_markdown(fact.clinical_context)}"
            for fact in facts
        )
        return f"### {title}\n\n{bullet_lines}"

    @staticmethod
    def _escape_markdown(text: str) -> str:
        single_line = _EMBEDDED_NEWLINE_RUN.sub(" ", text)
        return _MARKDOWN_SPECIAL_CHARS.sub(r"\\\1", single_line)
