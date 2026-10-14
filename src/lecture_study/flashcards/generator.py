"""Anki Flashcard Generation (Phase 12): deterministic, no-LLM conversion
of a lecture's Phase 9 LectureGlobalIndex into an Anki-importable
flashcard deck (CSV).

No llama.cpp, no model inference: pure string templating over facts
already vetted by the Critic Repair Loop (Phase 7) and deduplicated by
Phase 9, matching Phases 9-11's "no LLM" precedent one step later. See
docs/decisions/0012-deterministic-flashcard-generation.md.
"""

import csv
from io import StringIO
from pathlib import Path

from pydantic import TypeAdapter

from lecture_study.domain.errors import GlobalIndexNotFoundError
from lecture_study.domain.models import (
    Flashcard,
    LectureGlobalIndex,
    SafeIdentifier,
    SemanticFact,
)
from lecture_study.quality import (
    DeliveryArtifactKind,
    issue_deterministic_delivery,
    require_publication_ready,
)

METADATA_INPUT_DIR = Path("workspace/data/11_metadata")
FLASHCARDS_OUTPUT_DIR = Path("workspace/data/13_flashcards")

_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)

# A field opened by a spreadsheet application (Excel, Numbers, LibreOffice)
# that starts with one of these characters (OWASP's CSV/formula-injection
# trigger set, CWE-1236) is interpreted as a formula, not literal text.
# front/back originate in Phase 5's LLM-extracted lecture free text, so a
# malicious slide/transcript could plant a leading "=" -- or a leading
# space/tab before one, which some parsers still treat as a formula start
# -- today; prefixing with a single quote neutralizes formula evaluation
# while remaining valid, human-readable text in Anki itself.
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t")
_LEADING_WHITESPACE = " \t"


class AnkiDeckGenerator:
    """Converts one lecture's LectureGlobalIndex into an Anki-importable
    flashcard deck: one row per glossary term, dosage, and red flag."""

    def generate_deck(self, lecture_id: str) -> Path:
        lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
        index = require_publication_ready(str(lecture_id)).global_index
        flashcards = self._build_flashcards(index)
        output_bytes = {"anki_deck.csv": self._render_deck_bytes(flashcards)}
        issue_deterministic_delivery(
            str(lecture_id),
            DeliveryArtifactKind.FLASHCARDS,
            expected_output_bytes=output_bytes,
        )
        return FLASHCARDS_OUTPUT_DIR / lecture_id / "anki_deck.csv"

    @staticmethod
    def _load_global_index(lecture_id: str) -> LectureGlobalIndex:
        # Reads Phase 9's persisted artifact directly, matching
        # publishing/pipeline.py's own "read the prior phase's persisted
        # artifact, don't recompute it" convention.
        index_path = METADATA_INPUT_DIR / lecture_id / "global_index.json"
        if not index_path.exists():
            raise GlobalIndexNotFoundError(
                f"no {index_path} file for lecture {lecture_id!r}: Phase 9 "
                "has not produced a global index for this lecture"
            )
        return LectureGlobalIndex.model_validate_json(index_path.read_text())

    @classmethod
    def _build_flashcards(cls, index: LectureGlobalIndex) -> list[Flashcard]:
        return [
            *(cls._glossary_flashcard(fact) for fact in index.glossary_terms),
            *(cls._dosage_flashcard(fact) for fact in index.dosages),
            *(cls._red_flag_flashcard(fact) for fact in index.red_flags),
        ]

    @staticmethod
    def _glossary_flashcard(fact: SemanticFact) -> Flashcard:
        return Flashcard(
            front=f"O que é {fact.entity_name}?",
            back=fact.clinical_context,
            tags=["glossario"],
        )

    @staticmethod
    def _dosage_flashcard(fact: SemanticFact) -> Flashcard:
        return Flashcard(
            front=f"Qual a diretriz/dosagem para {fact.entity_name}?",
            back=fact.clinical_context,
            tags=["dosagem"],
        )

    @staticmethod
    def _red_flag_flashcard(fact: SemanticFact) -> Flashcard:
        return Flashcard(
            front=(
                "🚨 ALERTA CLÍNICO: Quais as contraindicações/alertas para "
                f"{fact.entity_name}?"
            ),
            back=fact.clinical_context,
            tags=["red_flag"],
        )

    @classmethod
    def _render_deck_bytes(cls, flashcards: list[Flashcard]) -> bytes:
        deck_buffer = StringIO(newline="")
        writer = csv.writer(deck_buffer)
        for card in flashcards:
            writer.writerow(
                [
                    cls._sanitize_field(card.front),
                    cls._sanitize_field(card.back),
                    " ".join(card.tags),
                ]
            )
        return deck_buffer.getvalue().encode("utf-8")

    @staticmethod
    def _sanitize_field(text: str) -> str:
        single_line = (
            text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
        )
        # Checked both raw (catches a leading "\t" itself) and left-
        # stripped of plain spaces/tabs (catches " =SOMA(...)" or
        # "\t=SOMA(...)", which a bare startswith() would miss since the
        # trigger character is not literally first).
        starts_with_trigger = single_line.startswith(
            _FORMULA_TRIGGER_CHARS
        ) or single_line.lstrip(_LEADING_WHITESPACE).startswith(_FORMULA_TRIGGER_CHARS)
        if starts_with_trigger:
            return f"'{single_line}"
        return single_line
