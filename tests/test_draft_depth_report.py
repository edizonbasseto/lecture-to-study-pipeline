"""Tests for the offline draft depth diagnostic: word count, clinical
category coverage, and clinical_warnings entries that merely restate the
body. This module is a diagnostic, not a gate -- it never raises for a
valid LearningUnitDraft and asserts no filesystem access."""

from lecture_study.domain.models import LearningUnitDraft
from lecture_study.drafting.depth_report import (
    DraftDepthReport,
    render_draft_depth_report_markdown,
    summarize_draft_depth,
)
from lecture_study.ops.llm_instructions import (
    DRAFT_COVERAGE_CATEGORIES as _DRAFT_COVERAGE_CATEGORIES,
)


def _make_draft(**overrides: object) -> LearningUnitDraft:
    defaults: dict[str, object] = dict(
        lecture_id="aula-001",
        pack_sequence=1,
        title="Mastectomia Invertida",
        body_markdown=(
            "A mastectomia invertida consiste na ligadura previa da "
            "epigastrica superficial caudal antes da retirada da glandula "
            "mamaria. O objetivo e reduzir o sangramento ao ligar o vaso "
            "mais calibroso. No entanto, a tecnica e considerada mais "
            "dificil de executar. A ligadura do vaso e feita com tres "
            "pincas para seguranca."
        ),
        clinical_warnings=[
            "A ligadura do vaso e feita com tres pincas para seguranca."
        ],
    )
    defaults.update(overrides)
    return LearningUnitDraft(**defaults)


def test_summarize_draft_depth_reports_word_count() -> None:
    draft = _make_draft(body_markdown="uma duas tres quatro cinco")

    report = summarize_draft_depth(draft)

    assert report.word_count == 5


def test_summarize_draft_depth_reports_covered_and_missing_categories() -> None:
    draft = _make_draft(
        body_markdown=(
            "A tecnica cirurgica utilizada exige ligadura previa do vaso "
            "antes da retirada da glandula."
        )
    )

    report = summarize_draft_depth(draft)

    assert "técnica" in report.covered_categories
    assert "técnica" not in report.missing_categories
    for category in _DRAFT_COVERAGE_CATEGORIES:
        if category != "técnica":
            assert category in report.missing_categories


def test_summarize_draft_depth_category_match_is_accent_and_case_insensitive() -> None:
    draft = _make_draft(body_markdown="A INDICACAO cirurgica e clara neste caso.")

    report = summarize_draft_depth(draft)

    assert "indicação" in report.covered_categories


def test_summarize_draft_depth_thin_ninety_word_draft_locks_the_known_failure() -> None:
    """Mirrors the shape of the real ~90-word draft the research traced for
    lecture 10010: one category present, four missing, below the word
    floor -- and the function still returns normally rather than raising."""
    thin_body = (
        "A mastectomia invertida consiste na ligadura previa da "
        "epigastrica superficial caudal antes da retirada da glandula "
        "mamaria. O objetivo e reduzir o sangramento ao ligar o vaso "
        "mais calibroso. No entanto, a tecnica e considerada mais "
        "dificil de executar em algumas pacientes de maior porte. A "
        "ligadura do vaso e feita com tres pincas para seguranca durante "
        "o procedimento cirurgico veterinario padrao realizado em "
        "cadelas adultas de diferentes portes corporais em ambiente "
        "hospitalar veterinario especializado."
    )
    draft = _make_draft(body_markdown=thin_body, clinical_warnings=[])

    report = summarize_draft_depth(draft)

    assert 70 <= report.word_count <= 95
    assert len(report.covered_categories) == 1
    assert len(report.missing_categories) == 4


def test_summarize_draft_depth_flags_warning_that_restates_body() -> None:
    restated = "A ligadura do vaso e feita com tres pincas para seguranca."
    draft = _make_draft(
        body_markdown=f"Texto introdutorio. {restated} Texto final.",
        clinical_warnings=[restated, "Um aviso distinto nao repetido no corpo."],
    )

    report = summarize_draft_depth(draft)

    assert report.warnings_restating_body == (0,)


def test_summarize_draft_depth_never_raises_on_empty_warnings_and_short_body() -> None:
    draft = _make_draft(body_markdown="Corpo curto.", clinical_warnings=[])

    report = summarize_draft_depth(draft)

    assert report.warnings_restating_body == ()
    assert isinstance(report, DraftDepthReport)


def test_summarize_draft_depth_ignores_blank_warning_as_trivial_substring() -> None:
    draft = _make_draft(clinical_warnings=["   "])

    report = summarize_draft_depth(draft)

    assert report.warnings_restating_body == ()


def test_render_draft_depth_report_markdown_orders_by_pack_sequence() -> None:
    second = summarize_draft_depth(_make_draft(pack_sequence=2))
    first = summarize_draft_depth(_make_draft(pack_sequence=1))

    rendered = render_draft_depth_report_markdown([second, first])

    assert rendered.index("| 1 |") < rendered.index("| 2 |")


def test_render_draft_depth_report_markdown_is_a_table_with_no_verdict_wording() -> (
    None
):
    report = summarize_draft_depth(_make_draft())

    rendered = render_draft_depth_report_markdown([report])

    assert rendered.startswith("|")
    for banned in ("aprovado", "reprovado", "pass", "fail"):
        assert banned not in rendered.lower()
