"""Offline draft depth diagnostic.

Scores a LearningUnitDraft for word count and clinical category coverage
without any LLM inference and without touching the filesystem. This is a
finding about the source lecture, never a gate: a thin draft is reported,
not repaired or rejected. Do not import this module from a Pydantic
validator or any orchestrator phase runner -- its only consumers are
tests and the Codex drafting script's depth-report step.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from lecture_study.domain.models import LearningUnitDraft
from lecture_study.ops.llm_instructions import (
    DRAFT_COVERAGE_CATEGORIES as _DRAFT_COVERAGE_CATEGORIES,
)

_CATEGORY_VARIANTS: dict[str, tuple[str, ...]] = {
    "indicação": ("indicação", "indicado", "indicada", "indicações"),
    "contraindicação": (
        "contraindicação",
        "contra-indicação",
        "contraindicado",
        "contraindicada",
        "contraindicações",
        "contra-indicações",
    ),
    "técnica": ("técnica", "técnicas"),
    "complicações": ("complicação", "complicações"),
    "tomada de decisão cirúrgica": (
        "tomada de decisão",
        "decisão cirúrgica",
        "conduta cirúrgica",
    ),
}


@dataclass(frozen=True, slots=True)
class DraftDepthReport:
    """A no-inference measurement of one draft's coverage and length.

    Carries no verdict field: it states what is present and absent, and
    the judgement of whether that is adequate stays with the reader.
    """

    lecture_id: str
    pack_sequence: int
    word_count: int
    covered_categories: tuple[str, ...]
    missing_categories: tuple[str, ...]
    warnings_restating_body: tuple[int, ...]


def summarize_draft_depth(draft: LearningUnitDraft) -> DraftDepthReport:
    """Score draft for length and clinical category coverage.

    Category detection is a case-insensitive, accent-insensitive substring
    match over a small accepted-variant table per category -- presence of
    vocabulary, not semantic understanding. Never raises for a valid
    LearningUnitDraft.
    """
    normalized_body = _normalize(draft.body_markdown)
    covered_categories, missing_categories = _split_categories_by_coverage(
        normalized_body
    )
    return DraftDepthReport(
        lecture_id=draft.lecture_id,
        pack_sequence=draft.pack_sequence,
        word_count=len(draft.body_markdown.split()),
        covered_categories=covered_categories,
        missing_categories=missing_categories,
        warnings_restating_body=_warnings_restating_body(
            draft.clinical_warnings, normalized_body
        ),
    )


def render_draft_depth_report_markdown(
    reports: Sequence[DraftDepthReport],
) -> str:
    """Render reports as a pt-BR Markdown table, ordered by pack_sequence."""
    header = (
        "| Pacote | Palavras | Categorias cobertas | Categorias ausentes | "
        "Avisos que repetem o corpo |"
    )
    separator = "|---|---|---|---|---|"
    rows = [_render_report_row(report) for report in _ordered_by_pack(reports)]
    return "\n".join([header, separator, *rows]) + "\n"


def _ordered_by_pack(
    reports: Sequence[DraftDepthReport],
) -> list[DraftDepthReport]:
    return sorted(reports, key=lambda report: report.pack_sequence)


def _render_report_row(report: DraftDepthReport) -> str:
    covered = ", ".join(report.covered_categories) or "nenhuma"
    missing = ", ".join(report.missing_categories) or "nenhuma"
    restated_indices = ", ".join(str(index) for index in report.warnings_restating_body)
    return (
        f"| {report.pack_sequence} | {report.word_count} | {covered} | "
        f"{missing} | {restated_indices or 'nenhum'} |"
    )


def _split_categories_by_coverage(
    normalized_body: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    covered = [
        category
        for category in _DRAFT_COVERAGE_CATEGORIES
        if _category_is_covered(category, normalized_body)
    ]
    missing = [
        category for category in _DRAFT_COVERAGE_CATEGORIES if category not in covered
    ]
    return tuple(covered), tuple(missing)


def _category_is_covered(category: str, normalized_body: str) -> bool:
    variants = _CATEGORY_VARIANTS.get(category, (category,))
    return any(_normalize(variant) in normalized_body for variant in variants)


def _warnings_restating_body(
    clinical_warnings: Sequence[str], normalized_body: str
) -> tuple[int, ...]:
    return tuple(
        index
        for index, warning in enumerate(clinical_warnings)
        if warning.strip() and _normalize(warning) in normalized_body
    )


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return without_accents.casefold()
