"""Global Metadata Aggregation (Phase 9): deterministic, no-LLM aggregation
of a lecture's Phase 5 SemanticLedgers into a single LectureGlobalIndex --
a deduplicated glossary plus dosage/treatment and contraindication/alert
fact subsets.

No llama.cpp, no model inference: pure text-normalization deduplication,
so this phase spends no VRAM. See
docs/decisions/0009-deterministic-metadata-deduplication.md for why that
trade-off (surface-level dedup only, deep semantic paraphrases are not
merged) is accepted for this appendix-grade artifact.
"""

import json
import logging
import string
from collections.abc import Iterable
from pathlib import Path
from re import sub as _collapse_whitespace

from pydantic import TypeAdapter

from lecture_study.domain.errors import (
    LectureNotFoundError,
    SemanticLedgersNotFoundError,
)
from lecture_study.domain.models import (
    LectureGlobalIndex,
    SafeIdentifier,
    SemanticFact,
    SemanticLedger,
)

_logger = logging.getLogger(__name__)

SEMANTIC_LEDGERS_INPUT_DIR = Path("workspace/data/07_semantic_ledgers")
CANONICAL_DOCUMENTS_INPUT_DIR = Path("workspace/data/10_canonical_documents")
METADATA_OUTPUT_DIR = Path("workspace/data/11_metadata")

_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)

# category is a free-text field an LLM populates (domain/models.py's
# SemanticFact has no enum, and the retired semantic/llm_worker.py's extraction
# prompt asks for "medical facts, dosages, and red flags" only in prose,
# with no closed vocabulary). These keyword tuples are this phase's own
# best-effort heuristic over that free text, not a vocabulary Phase 5 is
# actually constrained to produce -- a category matching neither list is
# not an error (see _log_uncategorized_facts): the fact still lands in
# glossary_terms, just not in dosages/red_flags.
_DOSAGE_CATEGORY_KEYWORDS = (
    "dosagem",
    "dosage",
    "dose",
    "posologia",
    "tratamento",
    "treatment",
)
_RED_FLAG_CATEGORY_KEYWORDS = (
    "contraindic",
    "red_flag",
    "red flag",
    "alerta",
    "warning",
    "risco",
    "risk",
)


class MetadataAggregator:
    """Aggregates one lecture's SemanticLedgers into a LectureGlobalIndex."""

    def aggregate_lecture(self, lecture_id: str) -> LectureGlobalIndex:
        lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
        self._require_canonical_document(lecture_id)
        facts = self._load_facts(lecture_id)
        _log_uncategorized_facts(lecture_id, facts)
        index = LectureGlobalIndex(
            lecture_id=lecture_id,
            glossary_terms=_collect_glossary_terms(facts),
            dosages=_deduplicate_facts(
                _facts_in_category(facts, _DOSAGE_CATEGORY_KEYWORDS)
            ),
            red_flags=_deduplicate_facts(
                _facts_in_category(facts, _RED_FLAG_CATEGORY_KEYWORDS)
            ),
        )
        self._save_index(index)
        return index

    @staticmethod
    def _require_canonical_document(lecture_id: str) -> None:
        # Phase 8's canonical_document.json is the authoritative record of
        # which lectures completed the pipeline; a lecture absent from it
        # must not be silently indexed off of leftover Phase 5 ledgers.
        canonical_document_path = (
            CANONICAL_DOCUMENTS_INPUT_DIR / lecture_id / "canonical_document.json"
        )
        if not canonical_document_path.exists():
            raise LectureNotFoundError(
                f"no {canonical_document_path} file for lecture {lecture_id!r}: "
                "Phase 8 has not produced a canonical document for this lecture"
            )

    @staticmethod
    def _load_facts(lecture_id: str) -> list[SemanticFact]:
        lecture_dir = SEMANTIC_LEDGERS_INPUT_DIR / lecture_id
        if not lecture_dir.exists():
            # Phase 5 writes one ledger file per evidence pack
            # unconditionally, so a canonically-complete lecture always
            # had this directory at some point; its absence now is data
            # loss, not "a lecture with no clinical content" (that case
            # still has ledger files, just with empty `facts` lists).
            raise SemanticLedgersNotFoundError(
                f"no {lecture_dir} directory for lecture {lecture_id!r}: Phase 8 "
                "produced a canonical document for this lecture but its Phase 5 "
                "SemanticLedgers directory is missing"
            )
        ledgers = [
            SemanticLedger.model_validate_json(ledger_path.read_text())
            for ledger_path in sorted(lecture_dir.glob("ledger_*.json"))
        ]
        ledgers.sort(key=lambda ledger: ledger.pack_sequence)
        return [fact for ledger in ledgers for fact in ledger.facts]

    @staticmethod
    def _save_index(index: LectureGlobalIndex) -> None:
        lecture_dir = METADATA_OUTPUT_DIR / index.lecture_id
        lecture_dir.mkdir(parents=True, exist_ok=True)
        output_path = lecture_dir / "global_index.json"
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(index.model_dump(mode="json"), indent=2, ensure_ascii=False)
        )
        tmp_path.replace(output_path)


def _facts_in_category(
    facts: Iterable[SemanticFact], keywords: tuple[str, ...]
) -> Iterable[SemanticFact]:
    return (fact for fact in facts if _category_matches(fact.category, keywords))


def _category_matches(category: str, keywords: tuple[str, ...]) -> bool:
    normalized_category = category.lower()
    return any(keyword in normalized_category for keyword in keywords)


def _log_uncategorized_facts(lecture_id: str, facts: Iterable[SemanticFact]) -> None:
    for fact in facts:
        matches_dosage = _category_matches(fact.category, _DOSAGE_CATEGORY_KEYWORDS)
        matches_red_flag = _category_matches(fact.category, _RED_FLAG_CATEGORY_KEYWORDS)
        if not matches_dosage and not matches_red_flag:
            _logger.debug(
                "lecture %r: fact %r (category=%r) matched neither dosage nor "
                "red-flag keywords; indexed in glossary_terms only",
                lecture_id,
                _truncate_for_log(fact.entity_name),
                _truncate_for_log(fact.category),
            )


def _truncate_for_log(text: str, max_length: int = 200) -> str:
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}...(truncated, {len(text)} chars total)"


def _collect_glossary_terms(facts: Iterable[SemanticFact]) -> list[SemanticFact]:
    seen_terms: set[str] = set()
    glossary_terms: list[SemanticFact] = []
    for fact in facts:
        signature = _normalize_text(fact.entity_name)
        if signature in seen_terms:
            continue
        seen_terms.add(signature)
        glossary_terms.append(fact)
    return glossary_terms


def _deduplicate_facts(facts: Iterable[SemanticFact]) -> list[SemanticFact]:
    seen_signatures: set[tuple[str, str]] = set()
    deduplicated_facts: list[SemanticFact] = []
    for fact in facts:
        signature = (
            _normalize_text(fact.entity_name),
            _normalize_text(fact.clinical_context),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduplicated_facts.append(fact)
    return deduplicated_facts


def _normalize_text(text: str) -> str:
    """Collapse case, surrounding whitespace, and edge punctuation
    differences that would otherwise make one clinical fact look like two
    (e.g. "Aspirina 100mg " and "aspirina 100mg")."""
    collapsed = _collapse_whitespace(r"\s+", " ", text.strip().lower())
    return collapsed.strip(string.punctuation)
