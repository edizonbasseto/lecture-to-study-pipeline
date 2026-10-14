"""Podcast Script Compilation (Phase 14): persists one lecture's
generated PodcastScript as both raw JSON (script.json, reserved for a
future Phase 15 TTS pass) and a human-readable Host/Expert transcript
(script_legivel.md) -- a strict view-vs-data split, matching Phase
10-11/13's Markdown-vs-JSON separation precedent.
"""

import json
import re
from pathlib import Path

from lecture_study.domain.models import (
    ArtifactExecutionMode,
    PodcastLine,
    PodcastScript,
)
from lecture_study.quality.delivery import (
    DeliveryArtifactKind,
    write_delivery_outputs,
)
from lecture_study.quality.derivative import DerivativeQualityCertificateVerifier

PODCAST_SCRIPTS_OUTPUT_DIR = Path("workspace/data/15_podcast_scripts")

# title/text are free text produced by Phase 14's LLM pass
# (podcast/generator.py), not markdown this compiler generates itself --
# the same class of risk publishing/markdown_compiler.py and
# exams/compiler.py already close a review-board finding on (HTML- and
# embedded-newline-block-injection, Phases 10-11/13): a stray
# "#"/"*"/blank line in a generated line could otherwise spoof a heading
# or corrupt this document's structure once interpolated into a Markdown
# template. source_unit_id is generator-validated against a fixed
# "unit-NNN" shape (see podcast/generator.py's _validate_traceability),
# so it never actually needs escaping, but every LLM-sourced field is
# escaped uniformly here rather than trusting that upstream constraint to
# hold forever. The char class also includes '"' -- a CommonMark-legal
# escape target like every other char here -- unlike exams/compiler.py's
# sibling regex: _render_line wraps `text` in a literal "..." pair, so an
# embedded quote in the LLM's own output would otherwise visually close
# that wrapper early in the rendered transcript.
_MARKDOWN_SPECIAL_CHARS = re.compile(r'([\\`*_#+\-\[\]()<>|"])')
_EMBEDDED_NEWLINE_RUN = re.compile(r"\s*\n\s*")


class PodcastScriptCompiler:
    """Compiles one lecture's PodcastScript into
    workspace/data/15_podcast_scripts/{lecture_id}/: script.json (raw
    data) and script_legivel.md (a readable Host/Expert transcript with
    an inline unit-traceability annotation on every line)."""

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

    def compile(self, script: PodcastScript) -> Path:
        # ADR 0024: the uncertified personal-study route bypasses derivative
        # certification entirely rather than attempt to satisfy it -- every
        # other execution mode keeps requiring a real derivative certificate.
        if (
            script.execution_mode
            is not ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY
        ):
            self._quality_certificate_verifier.verify_podcast_script(script)
        output_bytes = {
            "script.json": json.dumps(
                script.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
            ).encode("utf-8"),
            "script_legivel.md": self._render_markdown(script).encode("utf-8"),
        }
        write_delivery_outputs(
            PODCAST_SCRIPTS_OUTPUT_DIR,
            str(script.lecture_id),
            DeliveryArtifactKind.PODCAST_SCRIPT,
            output_bytes,
        )
        return PODCAST_SCRIPTS_OUTPUT_DIR / script.lecture_id

    @classmethod
    def _render_markdown(cls, script: PodcastScript) -> str:
        header = f"# {cls._escape_markdown(script.title)}"
        dialogue = "\n\n".join(cls._render_line(line) for line in script.dialogue)
        return f"{header}\n\n{dialogue}\n"

    @classmethod
    def _render_line(cls, line: PodcastLine) -> str:
        # speaker is a Literal["Host", "Expert"], so it needs no escaping
        # -- only the two LLM-sourced free-text fields (text,
        # source_unit_id) do.
        return (
            f'**{line.speaker}:** "{cls._escape_markdown(line.text)}" '
            f"*(Fonte: {cls._escape_markdown(line.source_unit_id)})*"
        )

    @staticmethod
    def _escape_markdown(text: str) -> str:
        single_line = _EMBEDDED_NEWLINE_RUN.sub(" ", text)
        return _MARKDOWN_SPECIAL_CHARS.sub(r"\\\1", single_line)
