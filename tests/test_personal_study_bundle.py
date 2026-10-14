"""Personal study bundle generation and presentation tests."""

import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import pytest

from lecture_study.domain.models import (
    LearningUnitDraft,
    MockExam,
    MockQuestion,
    SemanticFact,
    SemanticLedger,
)
from lecture_study.study_materials.academic_typst import (
    _STEM_TO_FIRST_OPTION_GAP,
    render_academic_body,
    render_academic_list_item,
    render_academic_warning_block,
    render_typst_prose,
    typst_string,
)
from lecture_study.study_materials.bundle import (
    StudyDocument,
    StudyDocumentSection,
    StudyFlashcard,
    StudyQuestion,
    StudySourceEvidence,
    _clean_warning,
    _collect_unique_questions,
    _format_question_with_answer,
    _format_question_with_options,
    _conditional_cloze_cue,
    _deduplicate_flashcards,
    _disambiguate_question_prompts,
    _flashcard_front,
    _flashcard_topic,
    _sanitize_csv_field,
    _supplemental_section_questions,
    _warning_is_study_worthy,
    build_personal_study_bundle,
    critical_review_document,
    inference_mock_exam_document,
    mock_exam_document,
    podcast_script_document,
    render_flashcards_csv,
    render_flashcards_markdown,
    render_printable_flashcards_typst,
    render_study_document_markdown,
    render_study_document_typst,
    review_plan_document,
)
from lecture_study.study_materials.content_identity import (
    content_identity as _content_identity,
)
from lecture_study.study_materials.compiler import (
    SelectedFigure,
    StudyChapter,
    StudyEdition,
    StudyEditionKind,
    StudySection,
    build_ledger_grounded_drafts,
    build_personal_study_editions,
    has_informative_study_content,
    render_study_edition_markdown,
    render_study_edition_typst,
    study_assertion_identity,
    study_fact_assertion_identity,
)


def _draft(
    sequence: int,
    title: str,
    body: str,
    *,
    warnings: list[str] | None = None,
) -> LearningUnitDraft:
    return LearningUnitDraft(
        lecture_id="lecture-001",
        pack_sequence=sequence,
        title=title,
        body_markdown=body,
        clinical_warnings=warnings or [],
    )


def _bundle():
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=(
            _draft(
                1,
                "Avaliação da visão",
                "O reflexo pupilar fotomotor não comprova visão. "
                "O teste de ameaça auxilia na avaliação da visão.",
            ),
            _draft(
                2,
                "Úlcera de córnea",
                "A fluoresceína evidencia defeitos epiteliais da córnea.",
                warnings=["Corticoide tópico é contraindicado na úlcera de córnea."],
            ),
        ),
    )
    return build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=editions[StudyEditionKind.SUMMARY],
    )


def test_ledger_grounded_drafts_remove_free_form_claims() -> None:
    grounded = build_ledger_grounded_drafts(
        lecture_id="lecture-001",
        drafts=(
            _draft(
                1,
                "Anatomia auricular",
                "Afirmação livre que não deve permanecer.",
            ),
        ),
        ledgers=(
            SemanticLedger(
                lecture_id="lecture-001",
                pack_sequence=1,
                facts=[
                    SemanticFact(
                        entity_name="Nervo facial",
                        category="anatomy",
                        clinical_context="O nervo facial acompanha o ouvido médio.",
                        timestamp_ref=12.0,
                    ),
                    SemanticFact(
                        entity_name="Carótida",
                        category="Risco cirúrgico",
                        clinical_context="Evitar lesão da carótida durante a abordagem.",
                        timestamp_ref=18.0,
                    ),
                ],
            ),
        ),
    )

    assert grounded[0].title == "Anatomia auricular"
    assert "Afirmação livre" not in grounded[0].body_markdown
    assert grounded[0].body_markdown.splitlines() == [
        "- **Nervo facial (anatomia).** O nervo facial acompanha o ouvido médio.",
        "- **Carótida (Risco cirúrgico).** Evitar lesão da carótida durante a abordagem.",
    ]
    assert grounded[0].clinical_warnings == [
        "Evitar lesão da carótida durante a abordagem."
    ]


def test_ledger_grounded_drafts_translate_internal_taxonomy_for_readers() -> None:
    internal_facts = (
        ("Contexto", "clinical_context", "disease stable."),
        ("Local", "anatomical_site", "Átrio direito."),
        ("Frequência", "statistic", "Incidência de 3%."),
        ("Condição", "disease", "Linfoma nodal."),
        ("Instituição", "institution", "Hospital veterinário."),
        ("Profissional", "person", "Patologista responsável."),
    )
    grounded = build_ledger_grounded_drafts(
        lecture_id="lecture-001",
        drafts=(_draft(1, "Taxonomia clínica", "Texto livre."),),
        ledgers=(
            SemanticLedger(
                lecture_id="lecture-001",
                pack_sequence=1,
                facts=[
                    SemanticFact(
                        entity_name=entity_name,
                        category=category,
                        clinical_context=clinical_context,
                        timestamp_ref=float(index),
                    )
                    for index, (entity_name, category, clinical_context) in enumerate(
                        internal_facts,
                        start=1,
                    )
                ],
            ),
        ),
    )

    assert grounded[0].body_markdown.splitlines() == [
        "- **Contexto (contexto clínico).** doença estável.",
        "- **Local (sítio anatômico).** Átrio direito.",
        "- **Frequência (estatística).** Incidência de 3%.",
        "- **Condição (doença).** Linfoma nodal.",
        "- **Instituição (instituição).** Hospital veterinário.",
        "- **Profissional (pessoa).** Patologista responsável.",
    ]


def test_ledger_grounded_drafts_remove_repeated_rendered_facts() -> None:
    repeated_fact = {
        "entity_name": "Margem lateral",
        "category": "Métrica",
        "clinical_context": "Margem lateral.",
    }
    grounded = build_ledger_grounded_drafts(
        lecture_id="lecture-001",
        drafts=(_draft(1, "Margens cirúrgicas", "Texto livre."),),
        ledgers=(
            SemanticLedger(
                lecture_id="lecture-001",
                pack_sequence=1,
                facts=[
                    SemanticFact(**repeated_fact, timestamp_ref=12.0),
                    SemanticFact(**repeated_fact, timestamp_ref=18.0),
                ],
            ),
        ),
    )

    assert grounded[0].body_markdown.splitlines() == [
        "- **Margem lateral (Métrica).** Margem lateral."
    ]


def test_ledger_grounded_drafts_preserve_clinically_distinct_operators() -> None:
    lower_limit = "Em paciente oncológico, considerar dose < 5 mg por aplicação."
    upper_limit = "Em paciente oncológico, considerar dose > 5 mg por aplicação."
    grounded = build_ledger_grounded_drafts(
        lecture_id="lecture-001",
        drafts=(
            _draft(1, "Limite inferior", "Texto livre."),
            _draft(2, "Limite superior", "Texto livre."),
        ),
        ledgers=(
            SemanticLedger(
                lecture_id="lecture-001",
                pack_sequence=1,
                facts=[
                    SemanticFact(
                        entity_name="Dose",
                        category="Métrica",
                        clinical_context=lower_limit,
                        timestamp_ref=12.0,
                    ),
                ],
            ),
            SemanticLedger(
                lecture_id="lecture-001",
                pack_sequence=2,
                facts=[
                    SemanticFact(
                        entity_name="Dose",
                        category="Métrica",
                        clinical_context=upper_limit,
                        timestamp_ref=18.0,
                    ),
                ],
            ),
        ),
    )

    assert grounded[0].body_markdown.splitlines() == [
        f"- **Dose (Métrica).** {lower_limit}",
    ]
    assert grounded[1].body_markdown.splitlines() == [
        f"- **Dose (Métrica).** {upper_limit}",
    ]
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Limites clínicos",
        drafts=grounded,
    )
    summary_markdown = render_study_edition_markdown(editions[StudyEditionKind.SUMMARY])
    bundle = build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=editions[StudyEditionKind.SUMMARY],
    )

    assert lower_limit in summary_markdown
    assert upper_limit in summary_markdown
    assert {lower_limit, upper_limit}.issubset(
        {card.back for card in bundle.flashcards}
    )


def test_summary_deduplicates_typography_before_applying_warning_limit() -> None:
    equivalent_warnings = (
        "Risco de sangramento: evitar procedimento imediato.",
        "Risco de sangramento - evitar procedimento imediato.",
        "Risco de sangramento — evitar procedimento imediato.",
        "Risco de sangramento; evitar procedimento imediato.",
        "Risco de sangramento, evitar procedimento imediato.",
        "Risco de sangramento. evitar procedimento imediato.",
        "Risco de sangramento / evitar procedimento imediato.",
        "Risco de sangramento (evitar procedimento imediato).",
    )
    distinct_warning = "Risco de anafilaxia exige intervenção imediata."
    drafts = tuple(
        _draft(
            sequence,
            f"Conduta {sequence}",
            "O tratamento exige avaliação clínica individualizada.",
            warnings=[warning],
        )
        for sequence, warning in enumerate(
            (*equivalent_warnings, distinct_warning),
            start=1,
        )
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Alertas perioperatórios",
        drafts=drafts,
    )
    summary = editions[StudyEditionKind.SUMMARY]
    summary_warnings = tuple(
        warning
        for chapter in summary.chapters
        for section in chapter.sections
        for warning in section.warnings
    )
    bundle = build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=summary,
    )

    assert summary_warnings == (equivalent_warnings[0], distinct_warning)
    assert distinct_warning in {card.back for card in bundle.flashcards}


@pytest.mark.parametrize(
    "value",
    (
        "**lesão número 3 (alerta clínico).**",
        "Fragmento de truquete (procedimento).",
        "**fragmento de truquete (procedimento).** fragmento de truquete",
        "**tricotomia (procedimento).** tricotomia realizada",
        "**anestesia (procedimento).** paciente está anestesiado",
    ),
)
def test_study_selection_rejects_labels_without_clinical_information(
    value: str,
) -> None:
    assert not has_informative_study_content(value)


def test_warning_selection_rejects_a_categorized_label_without_a_claim() -> None:
    assert not _warning_is_study_worthy("Lesão número 3 (alerta clínico).")


def test_summary_excludes_tautologies_and_alert_labels_without_claims() -> None:
    low_value_fact = "**fragmento de truquete (procedimento).** fragmento de truquete"
    low_value_warning = "Lesão número 3 (alerta clínico)."
    useful_fact = (
        "**biópsia incisional (procedimento).** o procedimento recomendado "
        "coleta fragmentos da periferia da lesão"
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Biópsias incisionais",
        drafts=(
            _draft(
                1,
                "Técnica",
                f"- {low_value_fact}\n- {useful_fact}",
                warnings=[low_value_warning],
            ),
        ),
    )

    summary_markdown = render_study_edition_markdown(editions[StudyEditionKind.SUMMARY])

    assert low_value_fact not in summary_markdown
    assert "fragmento de truquete (procedimento)." not in summary_markdown
    assert low_value_warning not in summary_markdown
    assert "o procedimento recomendado coleta fragmentos da periferia" in (
        summary_markdown
    )


def test_categorized_and_plain_forms_share_only_the_assertion_identity() -> None:
    categorized = "**agulha (instrumento).** agulha deve entrar com cuidado no tumor"
    plain = "Agulha deve entrar com cuidado no tumor"

    assert study_fact_assertion_identity(categorized) == (
        study_fact_assertion_identity(plain)
    )
    assert study_assertion_identity(categorized) != study_assertion_identity(plain)


def test_bundle_merges_categorized_and_plain_assertion_evidence() -> None:
    categorized = (
        "**bloqueio anestésico (procedimento).** não gosto de tentar com apenas "
        "bloqueio anestésico"
    )
    plain = "Não gosto de tentar com apenas bloqueio anestésico"
    chapter = StudyChapter(
        title="Biópsias incisionais",
        sections=(
            StudySection(
                title="Técnica",
                body=f"- {categorized}",
                warnings=(plain,),
                source_sequences=(1,),
            ),
        ),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="10009",
        lecture_title="Biópsias incisionais",
        chapters=(chapter,),
        source_unit_count=1,
    )
    summary = replace(complete, kind=StudyEditionKind.SUMMARY)

    bundle = build_personal_study_bundle(complete=complete, summary=summary)
    matching_cards = [
        card
        for card in bundle.flashcards
        if study_fact_assertion_identity(card.back)
        == study_fact_assertion_identity(plain)
    ]
    matching_questions = [
        question
        for question in bundle.questions
        if study_fact_assertion_identity(question.answer)
        == study_fact_assertion_identity(plain)
    ]

    assert len(matching_cards) == 1
    assert set(matching_cards[0].tags) == {"alerta_clinico", "ponto_essencial"}
    assert set(matching_cards[0].source_excerpts) == {categorized, plain}
    assert len(matching_questions) == 1


def test_distinct_categorized_subjects_preserve_a_shared_assertion() -> None:
    insufficient = (
        "**material insuficiente (procedimento).** usar agulha de calibre menor"
    )
    excess = "**material em excesso (procedimento).** usar agulha de calibre menor"
    section = StudySection(
        title="Coleta",
        body=f"- {insufficient}\n- {excess}",
        warnings=(),
        source_sequences=(1,),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="10016",
        lecture_title="Coleta de citologia",
        chapters=(StudyChapter(title="Coleta", sections=(section,)),),
        source_unit_count=1,
    )

    assert study_fact_assertion_identity(insufficient) == (
        study_fact_assertion_identity(excess)
    )
    assert study_assertion_identity(insufficient) != study_assertion_identity(excess)

    bundle = build_personal_study_bundle(
        complete=complete,
        summary=replace(complete, kind=StudyEditionKind.SUMMARY),
    )

    assert {insufficient, excess}.issubset({card.back for card in bundle.flashcards})


def test_ambiguous_plain_assertion_does_not_merge_by_input_order() -> None:
    plain = "Usar agulha de calibre menor"
    insufficient = (
        "**material insuficiente (procedimento).** usar agulha de calibre menor"
    )
    excess = "**material em excesso (procedimento).** usar agulha de calibre menor"
    cards = tuple(
        StudyFlashcard(
            front=f"Pergunta {index}?",
            back=content,
            tags=(f"tag-{index}",),
            evidence=(
                StudySourceEvidence(
                    excerpt=content,
                    source_sequences=(1,),
                ),
            ),
        )
        for index, content in enumerate((plain, insufficient, excess), start=1)
    )

    selected = _deduplicate_flashcards(cards)

    assert len(selected) == 3
    assert all(len(card.source_excerpts) == 1 for card in selected)
    assert {card.back for card in selected} == {plain, insufficient, excess}


def test_ambiguous_assertion_deduplication_scales_linearly() -> None:
    assertion = "Usar agulha de calibre menor"
    cards = (
        StudyFlashcard(
            front="Pergunta simples?",
            back=assertion,
            tags=("plain",),
            evidence=(StudySourceEvidence(excerpt=assertion, source_sequences=(1,)),),
        ),
        *(
            StudyFlashcard(
                front=f"Pergunta categorizada {index}?",
                back=(
                    f"**condição clínica {index} (procedimento).** "
                    f"{assertion.casefold()}"
                ),
                tags=("categorized",),
                evidence=(
                    StudySourceEvidence(
                        excerpt=f"evidência {index}",
                        source_sequences=(1,),
                    ),
                ),
            )
            for index in range(4_000)
        ),
    )

    started_at = perf_counter()
    selected = _deduplicate_flashcards(cards)
    elapsed_seconds = perf_counter() - started_at

    assert len(selected) == len(cards)
    assert elapsed_seconds < 1.0


def test_supplemental_questions_include_study_worthy_warnings() -> None:
    warning = "A agulha deve ser introduzida com cuidado no nódulo."
    section = StudySection(
        title="Técnica",
        body="- A biópsia exige planejamento antes da coleta do fragmento.",
        warnings=(warning,),
        source_sequences=(1,),
    )

    questions = _supplemental_section_questions(section, limit=8)

    assert warning in {question.answer for question in questions}
    assert any(
        question.prompt.startswith("Em Técnica, qual alerta clínico")
        for question in questions
    )


def test_summary_does_not_repeat_a_selected_point_as_a_warning() -> None:
    repeated_warning = "A biópsia incisional não deve incluir a área de transição."
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Biópsias incisionais",
        drafts=(
            _draft(
                1,
                "Técnica",
                repeated_warning,
                warnings=[repeated_warning],
            ),
        ),
    )

    summary_markdown = render_study_edition_markdown(editions[StudyEditionKind.SUMMARY])

    assert summary_markdown.count(repeated_warning) == 1


def test_a_chapter_plan_names_chapters_and_routes_the_rest_to_a_complement() -> None:
    drafts = tuple(
        _draft(
            sequence,
            f"Técnica {sequence}",
            "A biópsia incisional exige planejamento do local de coleta.",
        )
        for sequence in range(1, 5)
    )

    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Biópsias incisionais",
        drafts=drafts,
        chapter_plan=(("Biópsias incisionais", 1, 3),),
    )

    complete = editions[StudyEditionKind.COMPLETE]
    assert [chapter.title for chapter in complete.chapters] == [
        "Biópsias incisionais",
        "Conteúdo complementar",
    ]
    assert [len(chapter.sections) for chapter in complete.chapters] == [3, 1]


def test_without_a_chapter_plan_units_are_grouped_in_numbered_parts() -> None:
    drafts = tuple(
        _draft(
            sequence,
            f"Técnica {sequence}",
            "A biópsia incisional exige planejamento do local de coleta.",
        )
        for sequence in range(1, 4)
    )

    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Biópsias incisionais",
        drafts=drafts,
    )

    assert {
        chapter.title for edition in editions.values() for chapter in edition.chapters
    } == {"Parte 1"}


def test_warning_cleanup_scales_linearly_with_leading_whitespace() -> None:
    warning = f"{' ' * 100_000}Risco clínico sem rótulo."

    started_at = perf_counter()
    cleaned = _clean_warning(warning)
    elapsed_seconds = perf_counter() - started_at

    assert cleaned == warning
    assert elapsed_seconds < 0.5


@pytest.mark.parametrize("separator", [" ", "\t", "\r", "\n", "\u00a0"])
def test_warning_cleanup_normalizes_whitespace_in_study_derivatives(
    separator: str,
) -> None:
    warning = f"Risco clínico{separator}(urgência): intervenção imediata no paciente."
    expected = "Risco clínico: intervenção imediata no paciente."
    chapter = StudyChapter(
        title="Parte 1",
        sections=(
            StudySection(
                title="Conduta",
                body="- O tratamento deve ser individualizado para o paciente.",
                warnings=(warning,),
                source_sequences=(1,),
            ),
        ),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Alertas clínicos",
        chapters=(chapter,),
        source_unit_count=1,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Alertas clínicos",
        chapters=(chapter,),
        source_unit_count=1,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    assert _clean_warning(warning) == expected
    assert expected in {card.back for card in bundle.flashcards}
    assert expected in {question.answer for question in bundle.questions}
    assert "Qual alerta clínico é relevante em Conduta?" in {
        question.prompt for question in bundle.questions
    }
    assert any("sobre o tratamento" in question.prompt for question in bundle.questions)
    assert all("Parte 1" not in question.prompt for question in bundle.questions)


def test_bundle_derives_every_answer_from_the_complete_study_edition() -> None:
    bundle = _bundle()
    source = render_study_edition_markdown(bundle.complete)

    assert bundle.flashcards
    assert bundle.questions
    assert all(card.source_excerpt in source for card in bundle.flashcards)
    assert all(question.source_excerpt in source for question in bundle.questions)
    assert all(turn.text.strip() for turn in bundle.podcast_turns)
    assert {turn.speaker for turn in bundle.podcast_turns} == {
        "Apresentação",
        "Explicação",
    }


def test_bundle_supplements_short_derivatives_from_complete_edition() -> None:
    complete_sections = tuple(
        StudySection(
            title=f"Procedimento {section_index}",
            body="\n".join(
                (
                    f"- **Procedimento clínico {section_index}-{point_index} "
                    "(procedimento).** Deve ser associado à avaliação do paciente "
                    f"em intervalo de {(section_index * 10) + point_index} dias."
                )
                for point_index in range(1, 5)
            ),
            warnings=(),
            source_sequences=(section_index,),
        )
        for section_index in range(1, 6)
    )
    summary_sections = tuple(
        replace(section, body=section.body.splitlines()[0])
        for section in complete_sections
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Procedimentos clínicos",
        chapters=(StudyChapter(title="Parte 1", sections=complete_sections),),
        source_unit_count=5,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Procedimentos clínicos",
        chapters=(StudyChapter(title="Parte 1", sections=summary_sections),),
        source_unit_count=5,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    assert len(bundle.flashcards) == 18
    assert len(bundle.questions) >= 8
    assert len({card.front for card in bundle.flashcards}) == 18
    assert all("(procedimento)" not in card.front for card in bundle.flashcards)
    assert all(
        card.source_excerpt in render_study_edition_markdown(complete)
        for card in bundle.flashcards
    )


def test_summary_cards_reuse_the_complete_edition_clinical_context() -> None:
    point = "movimento em leque (procedimento)."
    complete_point = (
        "**movimento em leque (procedimento).** Técnica usada para a coleta."
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Coleta clínica",
        chapters=(
            StudyChapter(
                title="Parte 1",
                sections=(
                    StudySection(
                        title="Técnica de coleta",
                        body=f"- {complete_point}",
                        warnings=(),
                        source_sequences=(1,),
                    ),
                ),
            ),
        ),
        source_unit_count=1,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Coleta clínica",
        chapters=(
            StudyChapter(
                title="Parte 1",
                sections=(
                    StudySection(
                        title="Pontos essenciais",
                        body=f"- {point}",
                        warnings=(),
                        source_sequences=(1,),
                    ),
                ),
            ),
        ),
        source_unit_count=1,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    matching_card = next(
        card for card in bundle.flashcards if card.back == complete_point
    )
    assert matching_card.front.startswith("Em Técnica de coleta,")


def test_summary_card_context_matches_the_same_source_sequence() -> None:
    summary_point = "movimento em leque (procedimento)."
    wrong_point = "**movimento em leque (procedimento).** Técnica da unidade incorreta."
    correct_point = "**movimento em leque (procedimento).** Técnica da unidade correta."
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Coleta clínica",
        chapters=(
            StudyChapter(
                title="Parte 1",
                sections=(
                    StudySection(
                        title="Contexto incorreto",
                        body=f"- {wrong_point}",
                        warnings=(),
                        source_sequences=(1,),
                    ),
                    StudySection(
                        title="Contexto correto",
                        body=f"- {correct_point}",
                        warnings=(),
                        source_sequences=(2,),
                    ),
                ),
            ),
        ),
        source_unit_count=2,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Coleta clínica",
        chapters=(
            StudyChapter(
                title="Parte 1",
                sections=(
                    StudySection(
                        title="Pontos essenciais",
                        body=f"- {summary_point}",
                        warnings=(),
                        source_sequences=(2,),
                    ),
                ),
            ),
        ),
        source_unit_count=2,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    matching_card = next(
        card for card in bundle.flashcards if card.back == correct_point
    )
    assert matching_card.front.startswith("Em Contexto correto,")
    assert matching_card.source_sequences == (2,)

    wrong_only_complete = replace(
        complete,
        chapters=(
            StudyChapter(
                title="Parte 1",
                sections=(
                    complete.chapters[0].sections[0],
                    StudySection(
                        title="Outro assunto",
                        body=(
                            "- **agulha de coleta (material).** Material da "
                            "unidade correta."
                        ),
                        warnings=(),
                        source_sequences=(2,),
                    ),
                ),
            ),
        ),
    )
    preserved_bundle = build_personal_study_bundle(
        complete=wrong_only_complete,
        summary=summary,
    )
    assert all(card.back != summary_point for card in preserved_bundle.flashcards)
    assert all(
        has_informative_study_content(card.back) for card in preserved_bundle.flashcards
    )


def test_identical_complete_facts_keep_the_selected_source_context() -> None:
    repeated_point = (
        "**movimento em leque (procedimento).** Técnica compartilhada entre unidades."
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Coleta clínica",
        chapters=(
            StudyChapter(
                title="Parte 1",
                sections=(
                    StudySection(
                        title="Contexto da sequência 1",
                        body=f"- {repeated_point}",
                        warnings=(),
                        source_sequences=(1,),
                    ),
                    StudySection(
                        title="Contexto da sequência 2",
                        body=f"- {repeated_point}",
                        warnings=(),
                        source_sequences=(2,),
                    ),
                ),
            ),
        ),
        source_unit_count=2,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Coleta clínica",
        chapters=(
            StudyChapter(
                title="Parte 1",
                sections=(
                    StudySection(
                        title="Pontos essenciais",
                        body=f"- {repeated_point}",
                        warnings=(),
                        source_sequences=(2,),
                    ),
                ),
            ),
        ),
        source_unit_count=2,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    matching_card = next(
        card for card in bundle.flashcards if card.back == repeated_point
    )
    assert matching_card.front.startswith("Em Contexto da sequência 2,")


def test_question_baseline_counts_unique_answers_before_supplementing() -> None:
    repeated_answer = (
        "O procedimento clínico comum exige avaliação individualizada do paciente "
        "antes da intervenção."
    )
    chapters = tuple(
        StudyChapter(
            title=f"Parte {index}",
            sections=(
                StudySection(
                    title=f"Conduta {index}",
                    body=(
                        f"- {repeated_answer}\n"
                        f"- O procedimento clínico exclusivo {index} exige "
                        "monitoramento do paciente."
                    ),
                    warnings=(),
                    source_sequences=(index,),
                ),
            ),
        )
        for index in range(1, 9)
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Condutas clínicas",
        chapters=chapters,
        source_unit_count=8,
    )
    summary = replace(complete, kind=StudyEditionKind.SUMMARY)

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    assert len(bundle.questions) == 8
    assert (
        len({_content_identity(question.answer) for question in bundle.questions}) == 8
    )


def test_ineligible_primary_prompts_trigger_bounded_supplementation() -> None:
    option_names = (
        "alfa",
        "beta",
        "gama",
        "delta",
        "épsilon",
        "zeta",
        "eta",
        "teta",
    )
    chapters = tuple(
        StudyChapter(
            title=f"Parte {index}",
            sections=(
                StudySection(
                    title="Aspiração",
                    body=(
                        "- A conduta clínica de aspiração exige avaliação completa "
                        "do paciente antes do procedimento na opção "
                        f"{option_name}.\n"
                        f"- **Procedimento exclusivo {index} (procedimento).** "
                        "Conduta individualizada para o paciente na opção "
                        f"{option_name}."
                    ),
                    warnings=(),
                    source_sequences=(index,),
                ),
            ),
        )
        for index, option_name in enumerate(option_names, start=1)
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Aspiração clínica",
        chapters=chapters,
        source_unit_count=8,
    )
    summary = replace(complete, kind=StudyEditionKind.SUMMARY)

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    assert len(bundle.questions) == 8
    assert len({question.prompt for question in bundle.questions}) == 8
    assert any(
        "Procedimento exclusivo" in question.prompt for question in bundle.questions
    )


def test_question_candidate_collection_stops_at_the_central_budget() -> None:
    candidates = (
        StudyQuestion(
            prompt=f"Pergunta {index}?",
            answer=f"Resposta clínica {index}.",
            commentary="Comentário.",
            source_sequences=(index,),
            source_excerpt=f"Resposta clínica {index}.",
        )
        for index in range(1, 102)
    )

    selected, candidates_used = _collect_unique_questions(
        candidates,
        candidate_limit=80,
    )

    assert len(selected) == 80
    assert candidates_used == 80


def test_distinct_question_answers_receive_unique_contextual_prompts() -> None:
    section = StudySection(
        title="Aspiração",
        body="\n".join(
            (
                f"- **Aspiração (procedimento).** A conduta clínica {index} exige "
                "avaliação do paciente."
            )
            for index in range(1, 11)
        ),
        warnings=(),
        source_sequences=(1,),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Aspiração clínica",
        chapters=(StudyChapter(title="Parte 1", sections=(section,)),),
        source_unit_count=1,
    )
    summary = replace(complete, kind=StudyEditionKind.SUMMARY)

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    prompts = [question.prompt for question in bundle.questions]
    assert len(prompts) == 8
    assert len(set(prompts)) == 8
    assert all("Variação" not in prompt for prompt in prompts)
    assert sum("conduta clínica" in prompt.casefold() for prompt in prompts) >= 7
    assert prompts.count("Em Aspiração, o que deve ser recordado sobre Aspiração?") <= 1


def test_question_disambiguation_skips_unrecoverable_and_oversized_cues() -> None:
    shared_prompt = "Em Aspiração, o que deve ser recordado sobre Aspiração?"
    short_answer = "A conduta clínica exige avaliação completa antes da opção alfa."
    final_term_answer = (
        "A conduta clínica exige avaliação completa antes da opção beta."
    )
    prefix_answer = (
        "A conduta clínica exige avaliação completa antes da opção alfa e "
        "monitoramento adicional."
    )
    oversized_answer = " ".join(("avaliação",) * 1_000)
    questions = (
        StudyQuestion(shared_prompt, short_answer, "Comentário.", (1,), short_answer),
        StudyQuestion(
            shared_prompt,
            final_term_answer,
            "Comentário.",
            (1,),
            final_term_answer,
        ),
        StudyQuestion(
            shared_prompt,
            prefix_answer,
            "Comentário.",
            (1,),
            prefix_answer,
        ),
        StudyQuestion(
            "Em avaliação, o que deve ser recordado?",
            oversized_answer,
            "Comentário.",
            (2,),
            oversized_answer,
        ),
        StudyQuestion(
            "Em avaliação, o que deve ser recordado?",
            oversized_answer.replace("avaliação", "monitoramento"),
            "Comentário.",
            (2,),
            oversized_answer.replace("avaliação", "monitoramento"),
        ),
    )

    started_at = perf_counter()
    disambiguated = _disambiguate_question_prompts(questions)
    elapsed_seconds = perf_counter() - started_at

    assert elapsed_seconds < 0.5
    assert len({question.prompt for question in disambiguated}) == len(disambiguated)
    assert all("Variação" not in question.prompt for question in disambiguated)
    assert all(
        question.answer.casefold() not in question.prompt.casefold()
        for question in disambiguated
    )


def test_disambiguated_structured_facts_hide_internal_category_labels() -> None:
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Coleta clínica",
        drafts=(
            _draft(
                1,
                "Aspiração",
                "**Aspiração (procedimento).** Coleta por pressão negativa.",
            ),
            _draft(
                2,
                "Aspiração",
                "**Aspiração (procedimento).** Material de difícil obtenção.",
            ),
        ),
    )

    bundle = build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=editions[StudyEditionKind.SUMMARY],
    )

    aspiration_cards = [card for card in bundle.flashcards if "Aspiração" in card.front]
    assert len(aspiration_cards) == 2
    assert all("(procedimento)" not in card.front for card in aspiration_cards)
    printable_typst = render_printable_flashcards_typst(bundle)
    assert "**Aspiração" not in printable_typst
    assert "Aspiração (procedimento)." in printable_typst


def test_podcast_uses_the_complete_edition_without_summary_truncation() -> None:
    points = tuple(
        f"- A avaliação clínica da córnea inclui o ponto numerado {index}."
        for index in range(1, 15)
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=(
            _draft(
                1,
                "Avaliação da córnea",
                "\n".join(points),
            ),
        ),
    )

    bundle = build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=editions[StudyEditionKind.SUMMARY],
    )

    podcast_text = " ".join(turn.text for turn in bundle.podcast_turns)
    assert "ponto numerado 14" in podcast_text


def test_bundle_exposes_the_full_confirmed_material_package() -> None:
    bundle = _bundle()

    documents = (
        critical_review_document(bundle),
        mock_exam_document(bundle),
        review_plan_document(bundle),
        podcast_script_document(bundle),
    )

    assert {document.title for document in documents} == {
        "Resumo crítico",
        "Simulado com gabarito comentado",
        "Roteiro de revisão",
        "Podcast de revisão",
    }
    assert "Gabarito comentado" in render_study_document_markdown(documents[1])
    assert "Apresentação" in render_study_document_markdown(documents[3])
    assert "Explicação" in render_study_document_markdown(documents[3])
    assert "Pergunta" in render_flashcards_markdown(bundle)
    assert "Resposta" in render_flashcards_markdown(bundle)


def _inference_exam_for(*lecture_pack_sequences: tuple[int, str, str]) -> MockExam:
    return MockExam(
        lecture_id="lecture-001",
        questions=[
            MockQuestion(
                question_text=f"Pergunta da unidade {sequence}?",
                options={
                    "A": correct_text,
                    "B": "Alternativa errada",
                    "C": "-",
                    "D": "-",
                },
                correct_answer="A",
                explanation=explanation,
                pack_sequence=sequence,
            )
            for sequence, correct_text, explanation in lecture_pack_sequences
        ],
    )


def test_study_document_section_rejects_item_figures_length_mismatch() -> None:
    figure = SelectedFigure(relative_path="figuras/frame.png", caption="Legenda.")

    with pytest.raises(ValueError, match="one entry per item"):
        StudyDocumentSection(
            title="Questões",
            items=("Pergunta 1?", "Pergunta 2?"),
            item_figures=(figure,),
        )


def test_inference_mock_exam_document_illustrates_question_with_its_units_figure() -> (
    None
):
    """2026-09-15: exam questions never carried a figure at all -- a
    design gap, not a bug (see the 2026-09-15 handoff). Reuses the same
    figures_by_pack_sequence lookup build_personal_study_editions already
    uses for the complete edition's chapters, so a question grounded in
    pack_sequence 1 illustrates with the same frame already selected for
    that unit -- no separate selection or MockQuestion schema change."""
    exam = _inference_exam_for(
        (1, "Alternativa certa", "Explicação da unidade 1."),
        (2, "Outra alternativa certa", "Explicação da unidade 2."),
    )
    figure = SelectedFigure(relative_path="figuras/unidade-1.png", caption="Legenda 1.")

    document = inference_mock_exam_document(
        exam, figures_by_pack_sequence={1: (figure,)}
    )

    questions_section = document.sections[0]
    assert questions_section.item_figures == (figure, None)


def test_inference_mock_exam_document_illustrates_only_the_first_question_of_a_unit() -> (
    None
):
    """Regression: a learning unit produces several questions (5, for a
    real lecture's own exam) that all share the same pack_sequence and
    therefore the same candidate figure. Attaching it to every one of
    them printed the identical slide repeatedly in a row -- confirmed
    against a real compiled simulado.pdf before this guard existed."""
    exam = MockExam(
        lecture_id="lecture-001",
        questions=[
            MockQuestion(
                question_text=f"Pergunta {index} da unidade 1?",
                options={"A": "Certa", "B": "-", "C": "-", "D": "-"},
                correct_answer="A",
                explanation="Explicação.",
                pack_sequence=1,
            )
            for index in range(1, 4)
        ],
    )
    figure = SelectedFigure(relative_path="figuras/unidade-1.png", caption="Legenda 1.")

    document = inference_mock_exam_document(
        exam, figures_by_pack_sequence={1: (figure,)}
    )

    assert document.sections[0].item_figures == (figure, None, None)


def test_inference_mock_exam_document_gives_each_unit_its_own_distinct_figure() -> None:
    """Guards against a simpler-looking but wrong fix for the bug above:
    a single global "already illustrated" flag (instead of tracking each
    pack_sequence separately) would also stop the same slide from
    repeating, but would then illustrate only the exam's very first
    question overall and leave every other unit with no figure at all."""
    exam = MockExam(
        lecture_id="lecture-001",
        questions=[
            MockQuestion(
                question_text=f"Pergunta {index} da unidade {unit}?",
                options={"A": "Certa", "B": "-", "C": "-", "D": "-"},
                correct_answer="A",
                explanation="Explicação.",
                pack_sequence=unit,
            )
            for unit in (1, 2)
            for index in range(1, 3)
        ],
    )
    figure_1 = SelectedFigure(
        relative_path="figuras/unidade-1.png", caption="Legenda 1."
    )
    figure_2 = SelectedFigure(
        relative_path="figuras/unidade-2.png", caption="Legenda 2."
    )

    document = inference_mock_exam_document(
        exam, figures_by_pack_sequence={1: (figure_1,), 2: (figure_2,)}
    )

    assert document.sections[0].item_figures == (figure_1, None, figure_2, None)


def test_inference_mock_exam_document_has_no_figures_without_a_selection_map() -> None:
    exam = _inference_exam_for((1, "Alternativa certa", "Explicação."))

    document = inference_mock_exam_document(exam)

    assert document.sections[0].item_figures is None


def test_typst_document_embeds_the_figure_right_after_its_question() -> None:
    exam = _inference_exam_for((1, "Alternativa certa", "Explicação."))
    figure = SelectedFigure(relative_path="figuras/unidade-1.png", caption="Legenda 1.")
    document = inference_mock_exam_document(
        exam, figures_by_pack_sequence={1: (figure,)}
    )

    typst = render_study_document_typst(document)

    question_index = typst.index("Pergunta da unidade 1?")
    figure_index = typst.index('image("figuras/unidade-1.png"')
    answer_key_index = typst.index("Gabarito comentado")
    assert question_index < figure_index < answer_key_index


def test_markdown_document_embeds_the_figure_right_after_its_question() -> None:
    exam = _inference_exam_for((1, "Alternativa certa", "Explicação."))
    figure = SelectedFigure(relative_path="figuras/unidade-1.png", caption="Legenda 1.")
    document = inference_mock_exam_document(
        exam, figures_by_pack_sequence={1: (figure,)}
    )

    markdown = render_study_document_markdown(document)

    assert "![Legenda 1.](figuras/unidade-1.png)" in markdown


def test_critical_review_document_divides_each_chapter_by_content_type() -> None:
    bundle = _bundle()

    document = critical_review_document(bundle)

    section_titles = [section.title for section in document.sections]
    for chapter in bundle.summary.chapters:
        assert f"{chapter.title} — Pontos essenciais" in section_titles
    assert any(title.endswith("— Alertas clínicos") for title in section_titles)
    assert not any(title.endswith("— Perguntas de revisão") for title in section_titles)
    assert document.sections[-1].title == "Orientação para leitura crítica"


def test_critical_review_document_grounds_review_questions_in_their_own_chapter() -> (
    None
):
    bundle = _bundle()
    exam = _inference_exam_for(
        (1, "Teste de ameaça", "O teste de ameaça avalia a via visual."),
        (2, "Corticoide tópico", "Compromete a cicatrização epitelial da córnea."),
    )
    chapter = next(
        chapter
        for chapter in bundle.summary.chapters
        if {1, 2}
        <= {
            sequence
            for section in chapter.sections
            for sequence in section.source_sequences
        }
    )

    document = critical_review_document(bundle, inference_exam=exam)

    questions_section = next(
        section
        for section in document.sections
        if section.title == f"{chapter.title} — Perguntas de revisão"
    )
    assert questions_section.numbered is True
    assert len(questions_section.items) == 2
    assert "unidade 1" in questions_section.items[0]
    assert "Resposta: A) Teste de ameaça." in questions_section.items[0]
    assert "O teste de ameaça avalia a via visual." in questions_section.items[0]
    assert "unidade 2" in questions_section.items[1]


def test_critical_review_document_omits_review_questions_without_an_inference_exam() -> (
    None
):
    bundle = _bundle()

    document = critical_review_document(bundle)

    assert not any(
        section.title.endswith("— Perguntas de revisão")
        for section in document.sections
    )


def test_critical_review_document_ignores_questions_missing_a_pack_sequence() -> None:
    bundle = _bundle()
    exam = MockExam(
        lecture_id="lecture-001",
        questions=[
            MockQuestion(
                question_text="Pergunta sem unidade de origem conhecida.",
                options={"A": "1", "B": "2", "C": "3", "D": "4"},
                correct_answer="A",
                explanation="Explicação.",
            ),
        ],
    )

    document = critical_review_document(bundle, inference_exam=exam)

    assert not any(
        section.title.endswith("— Perguntas de revisão")
        for section in document.sections
    )


def test_format_question_with_answer_never_doubles_the_closing_period() -> None:
    """Regression (2026-09-15 document review): the correct option's own
    text often already ends in a period; appending another produced a
    visible '..' in the rendered document."""
    question = MockQuestion(
        question_text="Pergunta?",
        options={"A": "Resposta correta.", "B": "-", "C": "-", "D": "-"},
        correct_answer="A",
        explanation="Explicação.",
    )

    formatted = _format_question_with_answer(question)

    assert "Resposta: A) Resposta correta." in formatted
    assert ".." not in formatted


def test_format_question_with_options_puts_the_stem_and_each_option_on_its_own_line() -> (
    None
):
    """Regression (2026-09-15 document review): stem, options, and the
    answer/explanation used to run together as one dense paragraph --
    unreadable as a multiple-choice question. A CommonMark hard line
    break (two trailing spaces before "\\n") separates each, so the same
    string reads correctly rendered as both Typst (typst_string() turns a
    bare "\\n" into a real break on its own) and Markdown."""
    question = MockQuestion(
        question_text="Pergunta?",
        options={"A": "Um", "B": "Dois", "C": "Três", "D": "Quatro"},
        correct_answer="A",
        explanation="Explicação.",
    )

    formatted = _format_question_with_options(question)

    assert formatted == "Pergunta?  \nA) Um  \nB) Dois  \nC) Três  \nD) Quatro"


def test_format_question_with_options_collapses_an_embedded_newline_in_the_stem() -> (
    None
):
    """Regression (2026-09-16 operator report): render_academic_list_item()
    splits its item string on "\\n" and hanging-indents every line after
    the first like an option's own wrapped continuation. A model-produced
    question_text with its own internal "\\n" (the stem split into two
    sentences) used to have its second sentence misread as that,
    rendering hanging-indented like an option instead of flowing as one
    continuous stem. An embedded newline inside an option's own text has
    the same failure mode and is collapsed the same way."""
    question = MockQuestion(
        question_text=(
            "No pós-operatório de esplenectomia, um cão apresenta plaquetas "
            "baixas.\n\nQual conduta é indicada em relação ao anticoagulante?"
        ),
        options={
            "A": "Iniciar anticoagulante\nimediatamente.",
            "B": "Não iniciar.",
            "C": "Aumentar.",
            "D": "Dois anticoagulantes.",
        },
        correct_answer="B",
        explanation="Explicação.",
    )

    formatted = _format_question_with_options(question)

    assert formatted == (
        "No pós-operatório de esplenectomia, um cão apresenta plaquetas "
        "baixas. Qual conduta é indicada em relação ao anticoagulante?  \n"
        "A) Iniciar anticoagulante imediatamente.  \n"
        "B) Não iniciar.  \n"
        "C) Aumentar.  \n"
        "D) Dois anticoagulantes."
    )


def test_flashcards_have_meaningful_cues_and_printable_fronts_and_backs() -> None:
    bundle = _bundle()

    typst = render_printable_flashcards_typst(bundle)

    assert all(
        "ponto essencial" not in card.front.casefold() for card in bundle.flashcards
    )
    assert all(card.front.endswith("?") for card in bundle.flashcards)
    front_position = typst.index("Cartão 1 - pergunta")
    page_break_position = typst.index("#pagebreak()", front_position)
    back_position = typst.index("Cartão 1 - resposta")
    assert front_position < page_break_position < back_position
    assert bundle.flashcards[0].back in typst


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        (
            "A adrenalectomia unilateral para tumores não invasivos foi realizada.",
            "A adrenalectomia unilateral para tumores não invasivos",
        ),
        ("As ressecções foram realizadas.", "As ressecções"),
        ("O protocolo tem indicação clínica.", "O protocolo"),
        ("Os microRNAs têm aplicação diagnóstica.", "Os microRNAs"),
        (
            "A tomografia não diferencia adenoma de carcinoma.",
            "A tomografia",
        ),
    ),
)
def test_flashcard_predicates_produce_bounded_topics(
    content: str,
    expected: str,
) -> None:
    assert _flashcard_topic(content) == expected


def test_flashcard_topics_never_truncate_or_disclose_the_study_cue() -> None:
    assert (
        _flashcard_topic(
            "Considerar a possibilidade de leucopenia como indicativo clínico."
        )
        == "leucopenia"
    )
    assert (
        _flashcard_front(
            "Parte 1",
            "A adrenalectomia unilateral para tumores não invasivos foi realizada.",
        )
        == "Em Parte 1, o que deve ser recordado sobre a adrenalectomia "
        "unilateral para tumores não invasivos?"
    )
    fallback_back = "Uma formulação longa sem qualquer predicado reconhecido."
    fallback_front = _flashcard_front("Parte 1", fallback_back)
    assert fallback_back.rstrip(".") not in fallback_front
    assert fallback_front.startswith("Em Parte 1, complete a informação clínica:")
    assert "____" in fallback_front
    assert (
        _flashcard_front(
            "Técnica de squash",
            "movimento em leque (procedimento).",
        )
        == "Em Técnica de squash, o que deve ser recordado sobre Movimento em leque?"
    )
    central_claim = (
        "O tratamento mostrou redução significativa da mortalidade durante "
        "o acompanhamento clínico."
    )
    central_claim_front = _flashcard_front("Parte 1", central_claim)
    assert "redução significativa da mortalidade" not in central_claim_front
    assert (
        central_claim_front
        == "Em Parte 1, complete a informação clínica: O tratamento ____."
    )
    negative_claim_front = _flashcard_front(
        "Parte 1",
        "Não gosto de tentar com apenas bloqueio anestésico.",
    )
    assert "Não ____" not in negative_claim_front
    assert "Não gosto de ____" in negative_claim_front
    for sentence_final_claim in (
        "O diagnóstico definitivo revelou carcinoma urotelial.",
        "O risco principal corresponde a sangramento severo.",
    ):
        front = _flashcard_front("Parte 1", sentence_final_claim)
        final_terms = sentence_final_claim.rstrip(".").split()[-2:]
        assert all(term.casefold() not in front.casefold() for term in final_terms)


@pytest.mark.parametrize(
    ("fact", "topic"),
    (
        (
            "O caso clínico apresentava histórico de urolitíase.",
            "O caso clínico",
        ),
        (
            "O tratamento incluiu opioide, gabapentina e metoclopramida.",
            "O tratamento",
        ),
        (
            "Estudo retrospectivo de 28 cães demonstrou maior sobrevida.",
            "Estudo retrospectivo de 28 cães",
        ),
        (
            "Hipoglicemia com insulina não suprimida sustenta o diagnóstico.",
            "Hipoglicemia com insulina não suprimida",
        ),
        (
            "Queda progressiva do hematócrito exige investigação.",
            "Queda progressiva do hematócrito",
        ),
        (
            "Tempos convencionais não substituem a avaliação global.",
            "Tempos convencionais",
        ),
    ),
)
def test_past_tense_and_clinical_conclusion_facts_produce_topics(
    fact: str,
    topic: str,
) -> None:
    assert _flashcard_topic(fact) == topic
    assert "____" not in _flashcard_front("Parte 1", fact)


def test_printable_flashcards_mirror_empty_slots_on_a_partial_sheet() -> None:
    bundle = _bundle()
    single_card_bundle = bundle.with_flashcards((bundle.flashcards[0],))

    typst = render_printable_flashcards_typst(single_card_bundle)
    back_page = typst.split("#pagebreak()", maxsplit=1)[1]

    assert typst.count('[#study-card("", [])]') == 10
    assert typst.rstrip().endswith("#pagebreak(weak: true)")
    assert back_page.index('[#study-card("", [])]') < back_page.index(
        "Cartão 1 - resposta"
    )


def test_printable_flashcards_keep_every_duplex_grid_inside_the_page(
    tmp_path: Path,
) -> None:
    typst_executable = shutil.which("typst")
    pdftoppm_executable = shutil.which("pdftoppm")
    if typst_executable is None or pdftoppm_executable is None:
        pytest.skip("Typst and pdftoppm are required for the duplex grid regression")
    bundle = _bundle()
    source_cards = bundle.flashcards
    eighteen_cards = tuple(
        source_cards[(index - 1) % len(source_cards)].with_content(
            front=f"Qual é a pergunta clínica do cartão {index}?",
            back=(
                f"Resposta clínica fundamentada do cartão {index}, mantida "
                "integralmente dentro da célula de impressão."
            ),
        )
        for index in range(1, 19)
    )
    source_path = tmp_path / "flashcards-duplex.typ"
    pdf_path = source_path.with_suffix(".pdf")
    source_path.write_text(
        render_printable_flashcards_typst(bundle.with_flashcards(eighteen_cards)),
        encoding="utf-8",
    )

    subprocess.run(
        [typst_executable, "compile", str(source_path), str(pdf_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    for back_page_number in (2, 4, 6):
        raster_prefix = tmp_path / f"flashcards-back-{back_page_number}"
        subprocess.run(
            [
                pdftoppm_executable,
                "-f",
                str(back_page_number),
                "-l",
                str(back_page_number),
                "-r",
                "72",
                "-gray",
                "-singlefile",
                str(pdf_path),
                str(raster_prefix),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        width, height, pixels = _read_binary_pgm(raster_prefix.with_suffix(".pgm"))
        _assert_complete_flashcard_grid(width, height, pixels)


def _read_binary_pgm(path: Path) -> tuple[int, int, bytes]:
    magic, dimensions, maximum_value, pixels = path.read_bytes().split(b"\n", 3)
    assert magic == b"P5"
    assert maximum_value == b"255"
    width, height = (int(value) for value in dimensions.split())
    assert len(pixels) == width * height
    return width, height, pixels


def _assert_complete_flashcard_grid(
    width: int,
    height: int,
    pixels: bytes,
) -> None:
    pixels_per_millimeter = 72 / 25.4
    margin = 10 * pixels_per_millimeter
    printable_width = width - (2 * margin)
    printable_height = height - (2 * margin)
    expected_rows = tuple(
        margin + (printable_height * row_index / 3) for row_index in range(4)
    )
    expected_columns = tuple(
        margin + (printable_width * column_index / 2) for column_index in range(3)
    )

    assert all(
        _maximum_dark_pixels_in_row_band(
            pixels,
            width=width,
            height=height,
            expected_row=expected_row,
        )
        >= printable_width * 0.95
        for expected_row in expected_rows
    )
    assert all(
        _maximum_dark_pixels_in_column_band(
            pixels,
            width=width,
            height=height,
            expected_column=expected_column,
        )
        >= printable_height * 0.95
        for expected_column in expected_columns
    )


def _maximum_dark_pixels_in_row_band(
    pixels: bytes,
    *,
    width: int,
    height: int,
    expected_row: float,
) -> int:
    candidate_rows = range(
        max(0, round(expected_row) - 2),
        min(height, round(expected_row) + 3),
    )
    return max(
        sum(pixel < 230 for pixel in pixels[row * width : (row + 1) * width])
        for row in candidate_rows
    )


def _maximum_dark_pixels_in_column_band(
    pixels: bytes,
    *,
    width: int,
    height: int,
    expected_column: float,
) -> int:
    candidate_columns = range(
        max(0, round(expected_column) - 2),
        min(width, round(expected_column) + 3),
    )
    return max(
        sum(pixels[(row * width) + column] < 230 for row in range(height))
        for column in candidate_columns
    )


def test_bundle_represents_unextractable_and_same_subject_facts_uniquely() -> None:
    unextractable_fact = (
        "O tratamento mostrou benefício clínico em pacientes de alto risco."
    )
    shared_subject_facts = (
        "O protocolo tem indicação clínica para pacientes estáveis.",
        "O protocolo tem contraindicação importante em pacientes renais.",
    )
    unextractable_warning = (
        "Evitar associação medicamentosa durante o tratamento de pacientes renais."
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        drafts=(
            _draft(
                1,
                "Parte 1",
                unextractable_fact,
                warnings=[unextractable_warning],
            ),
            _draft(2, "Parte 1", shared_subject_facts[0]),
            _draft(3, "Parte 1", shared_subject_facts[1]),
        ),
    )
    bundle = build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=editions[StudyEditionKind.SUMMARY],
    )

    expected_backs = {
        unextractable_fact,
        *shared_subject_facts,
        unextractable_warning,
    }
    assert expected_backs.issubset({card.back for card in bundle.flashcards})
    fronts = [card.front for card in bundle.flashcards]
    assert len(fronts) == len(set(fronts))
    shared_subject_cards = [
        card for card in bundle.flashcards if card.back in shared_subject_facts
    ]
    assert len(shared_subject_cards) == 2
    assert all(
        card.front.startswith("Em Parte 1, complete a informação clínica:")
        for card in shared_subject_cards
    )
    assert {card.front for card in shared_subject_cards} == {
        "Em Parte 1, complete a informação clínica: O protocolo tem indicação ____.",
        "Em Parte 1, complete a informação clínica: O protocolo tem "
        "contraindicação ____.",
    }


def test_bundle_qualifies_same_source_residual_front_collisions() -> None:
    chapter = StudyChapter(
        title="Parte 1",
        sections=(
            StudySection(
                title="Benefício",
                body="- O tratamento mostrou benefício clínico para pacientes renais.",
                warnings=(),
                source_sequences=(1,),
            ),
            StudySection(
                title="Risco",
                body="- O tratamento mostrou benefício relevante contra eventos renais.",
                warnings=(),
                source_sequences=(1,),
            ),
        ),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=1,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=1,
    )

    bundle = build_personal_study_bundle(
        complete=complete,
        summary=summary,
    )

    fronts = [card.front for card in bundle.flashcards]
    assert len(fronts) == len(set(fronts))
    assert {front.split(",", maxsplit=1)[0] for front in fronts} == {
        "Em Benefício",
        "Em Risco",
    }


def test_bundle_disambiguates_conditional_values_without_revealing_answers() -> None:
    lower_limit_warning = (
        "PTH deve estar no limite inferior ou abaixo quando o cálcio está alto"
    )
    zero_warning = "PTH deve estar zero quando o cálcio está alto"
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Endocrinologia veterinária",
        drafts=(
            _draft(
                11,
                "Ajustes na dose de cálcio e calcitriol",
                "O cálcio deve ser monitorado durante o tratamento.",
                warnings=[lower_limit_warning, zero_warning],
            ),
        ),
    )

    bundle = build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=editions[StudyEditionKind.SUMMARY],
    )

    pth_cards = [
        card
        for card in bundle.flashcards
        if card.back in {lower_limit_warning, zero_warning}
    ]
    assert len(pth_cards) == 2
    assert len({card.front for card in pth_cards}) == 2
    lower_limit_front = next(
        card.front for card in pth_cards if card.back == lower_limit_warning
    )
    zero_front = next(card.front for card in pth_cards if card.back == zero_warning)
    assert "limite inferior" not in lower_limit_front
    assert "abaixo" not in lower_limit_front
    assert "zero" not in zero_front
    assert lower_limit_front.endswith("quando o cálcio está alto.")
    assert zero_front.endswith("quando o cálcio está alto.")
    assert lower_limit_front.startswith("Variante ")
    assert zero_front.startswith("Variante ")


def test_conditional_cloze_scans_large_whitespace_without_backtracking() -> None:
    content = f"PTH deve estar{' ' * 100_000}\n\n quando o cálcio está alto"

    assert _conditional_cloze_cue(content) is None


def test_conditional_cloze_preserves_offsets_with_unicode_ligatures() -> None:
    content = "Eﬃcácia deve estar baixa quando o cálcio está alto"

    assert _conditional_cloze_cue(content) == (
        "Eﬃcácia deve estar ____ quando o cálcio está alto"
    )


def test_bundle_merges_repeated_warnings_and_preserves_source_sequences() -> None:
    categorized_warning = (
        "Alerta (tratamento): evitar associação medicamentosa durante o tratamento "
        "de pacientes renais."
    )
    normalized_warning = (
        "Alerta: evitar associação medicamentosa durante o tratamento de pacientes "
        "renais."
    )
    chapter = StudyChapter(
        title="Parte 1",
        sections=(
            StudySection(
                title="Conduta inicial",
                body="- O tratamento deve ser monitorado.",
                warnings=(categorized_warning,),
                source_sequences=(1,),
            ),
            StudySection(
                title="Conduta complementar",
                body="- O acompanhamento deve ser contínuo.",
                warnings=(normalized_warning,),
                source_sequences=(2,),
            ),
        ),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=2,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=2,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    repeated_cards = [
        card for card in bundle.flashcards if card.back == normalized_warning
    ]
    assert len(repeated_cards) == 1
    assert repeated_cards[0].source_sequences == (1, 2)
    assert repeated_cards[0].source_excerpts == (
        categorized_warning,
        normalized_warning,
    )
    assert tuple(
        (item.excerpt, item.source_sequences) for item in repeated_cards[0].evidence
    ) == (
        (categorized_warning, (1,)),
        (normalized_warning, (2,)),
    )
    fronts = [card.front for card in bundle.flashcards]
    assert len(fronts) == len(set(fronts))


def test_bundle_merges_punctuation_variants_with_section_bound_evidence() -> None:
    canonical_point = "Tratamento: reduz o risco clínico significativamente."
    punctuation_variant = "Tratamento - reduz o risco clínico significativamente."
    chapter = StudyChapter(
        title="Parte 1",
        sections=(
            StudySection(
                title="Conduta inicial",
                body=f"- {canonical_point}",
                warnings=(),
                source_sequences=(1,),
            ),
            StudySection(
                title="Conduta complementar",
                body=f"- {punctuation_variant}",
                warnings=(),
                source_sequences=(2,),
            ),
        ),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=2,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=2,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    repeated_cards = [
        card for card in bundle.flashcards if card.back == canonical_point
    ]
    assert len(repeated_cards) == 1
    assert tuple(
        (item.excerpt, item.source_sequences) for item in repeated_cards[0].evidence
    ) == (
        (canonical_point, (1,)),
        (punctuation_variant, (2,)),
    )
    fronts = [card.front for card in bundle.flashcards]
    assert len(fronts) == len(set(fronts))


def test_bundle_preserves_clinically_meaningful_numeric_punctuation() -> None:
    distinct_points = (
        "O tratamento recomenda dose de 1,5 mg por kg em pacientes renais.",
        "O tratamento recomenda dose de 15 mg por kg em pacientes renais.",
        "O tratamento recomenda fração de 1/2 do comprimido para o paciente.",
        "O tratamento recomenda fração de 12 comprimidos para o paciente.",
        "O tratamento recomenda intervalo de 1-2 dias para nova avaliação.",
        "O tratamento recomenda intervalo de 12 dias para nova avaliação.",
        "O tratamento recomenda armazenamento a -5 °C para preservar a medicação.",
        "O tratamento recomenda armazenamento a 5 °C para preservar a medicação.",
        "O tratamento reduz o risco clínico em 5% dos pacientes avaliados.",
        "O tratamento reduz o risco clínico em 5 dos pacientes avaliados.",
        "O tratamento recomenda limite <=5 mg para pacientes renais.",
        "O tratamento recomenda limite de 5 mg para pacientes renais.",
        "O tratamento recomenda temperatura ≤-5 °C para preservar a medicação.",
        "O tratamento recomenda temperatura de -5 °C para preservar a medicação.",
        "O tratamento tem custo de R$ 5 por dose administrada.",
        "O tratamento tem custo de 5 por dose administrada.",
        "O tratamento reduz o risco clínico em 5 % dos pacientes.",
        "O tratamento reduz o risco clínico em 5 dos pacientes.",
        "O tratamento reduz o risco clínico em 5 ‰ dos pacientes.",
        "O tratamento recomenda temperatura de 5 °C para armazenamento.",
        "O tratamento recomenda temperatura de 5 C para armazenamento.",
        "O tratamento recomenda PaCO2<=45 mmHg em pacientes ventilados.",
        "O tratamento recomenda PaCO2 45 mmHg em pacientes ventilados.",
        "O tratamento recomenda pH<7.4 em pacientes críticos.",
        "O tratamento recomenda pH 7.4 em pacientes críticos.",
        "O tratamento recomenda T4≥5 em pacientes avaliados.",
        "O tratamento recomenda T4 5 em pacientes avaliados.",
        "O tratamento recomenda SpO2<95% em pacientes monitorados.",
        "O tratamento recomenda SpO2 95% em pacientes monitorados.",
        "O tratamento recomenda valor ≠5 no controle clínico.",
        "O tratamento recomenda valor 5 no controle clínico.",
        "O tratamento recomenda temperatura ﹣5℃ para armazenamento.",
        "O tratamento recomenda temperatura 5℃ para armazenamento.",
        "O tratamento recomenda carga viral de 10⁶ cópias no controle.",
        "O tratamento recomenda carga viral de 106 cópias no controle.",
        "O tratamento considera marcador HER2+ na avaliação clínica.",
        "O tratamento considera marcador HER2 na avaliação clínica.",
        "O tratamento considera cálcio Ca²⁺ na avaliação clínica.",
        "O tratamento considera cálcio Ca2 na avaliação clínica.",
        "O tratamento considera citocina IL-6 na avaliação clínica.",
        "O tratamento considera citocina IL 6 na avaliação clínica.",
        "O tratamento considera marcador HER2- na avaliação clínica.",
        "O tratamento considera marcador HER2 na avaliação clínica.",
        "O tratamento recomenda valor 2− no controle clínico.",
        "O tratamento recomenda valor 2 no controle clínico.",
        "O tratamento recomenda dose $<5 mg no controle clínico.",
        "O tratamento recomenda dose $5 mg no controle clínico.",
        "O tratamento recomenda dose <5 mg no controle clínico.",
        "O tratamento recomenda intervalo 5−3 dias no controle clínico.",
        "O tratamento recomenda intervalo 53 dias no controle clínico.",
        "O tratamento recomenda variação 5±2 mg no controle clínico.",
        "O tratamento recomenda variação 52 mg no controle clínico.",
        "O tratamento recomenda valor de 5,7 mg no controle clínico.",
        "O tratamento recomenda valor de 5, 7 mg no controle clínico.",
        "O tratamento recomenda dose — 2 mg no controle clínico.",
        "O tratamento recomenda dose -2 mg no controle clínico.",
    )
    chapter = StudyChapter(
        title="Parte 1",
        sections=tuple(
            StudySection(
                title=f"Conduta {sequence}",
                body=f"- {point}",
                warnings=(),
                source_sequences=(sequence,),
            )
            for sequence, point in enumerate(distinct_points, start=1)
        ),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=len(distinct_points),
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=len(distinct_points),
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    assert set(distinct_points).issubset({card.back for card in bundle.flashcards})
    fronts = [card.front for card in bundle.flashcards]
    assert len(fronts) == len(set(fronts))
    assert any(card.front.startswith("Em Conduta ") for card in bundle.flashcards)


def test_bundle_merges_equivalent_unicode_fraction_evidence() -> None:
    expanded_fraction = (
        "O tratamento recomenda 1/2 do comprimido para pacientes renais."
    )
    vulgar_fraction = "O tratamento recomenda ½ do comprimido para pacientes renais."
    spaced_fraction = (
        "O tratamento recomenda 1 / 2 do comprimido para pacientes renais."
    )
    chapter = StudyChapter(
        title="Parte 1",
        sections=(
            StudySection(
                title="Conduta inicial",
                body=f"- {expanded_fraction}",
                warnings=(),
                source_sequences=(1,),
            ),
            StudySection(
                title="Conduta complementar",
                body=f"- {vulgar_fraction}",
                warnings=(),
                source_sequences=(2,),
            ),
            StudySection(
                title="Conduta final",
                body=f"- {spaced_fraction}",
                warnings=(),
                source_sequences=(3,),
            ),
        ),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=3,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=3,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    fraction_cards = [
        card for card in bundle.flashcards if card.back == expanded_fraction
    ]
    assert len(fraction_cards) == 1
    assert tuple(
        (item.excerpt, item.source_sequences) for item in fraction_cards[0].evidence
    ) == (
        (expanded_fraction, (1,)),
        (vulgar_fraction, (2,)),
        (spaced_fraction, (3,)),
    )


def test_bundle_merges_equivalent_range_dashes_in_the_same_source_unit() -> None:
    canonical_range = (
        "O tratamento recomenda intervalo de 1-2 dias para nova avaliação."
    )
    unicode_range = "O tratamento recomenda intervalo de 1–2 dias para nova avaliação."
    chapter = StudyChapter(
        title="Parte 1",
        sections=(
            StudySection(
                title="Conduta inicial",
                body=f"- {canonical_range}",
                warnings=(),
                source_sequences=(1,),
            ),
            StudySection(
                title="Conduta complementar",
                body=f"- {unicode_range}",
                warnings=(),
                source_sequences=(1,),
            ),
        ),
    )
    complete = StudyEdition(
        kind=StudyEditionKind.COMPLETE,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=1,
    )
    summary = StudyEdition(
        kind=StudyEditionKind.SUMMARY,
        lecture_id="lecture-001",
        lecture_title="Cirurgia veterinária",
        chapters=(chapter,),
        source_unit_count=1,
    )

    bundle = build_personal_study_bundle(complete=complete, summary=summary)

    range_cards = [card for card in bundle.flashcards if card.back == canonical_range]
    assert len(range_cards) == 1
    assert tuple(item.excerpt for item in range_cards[0].evidence) == (
        canonical_range,
        unicode_range,
    )


@pytest.mark.parametrize(
    ("qualified_value", "plain_value"),
    (
        ("limite <=5 mg", "limite 5 mg"),
        ("temperatura ≤-5 °C", "temperatura -5 °C"),
        ("custo R$ 5", "custo 5"),
        ("risco 5 %", "risco 5"),
        ("risco 5 ‰", "risco 5"),
        ("temperatura 5 °C", "temperatura 5 C"),
        ("PaCO2<=45 mmHg", "PaCO2 45 mmHg"),
        ("pH<7.4", "pH 7.4"),
        ("T4≥5", "T4 5"),
        ("SpO2<95%", "SpO2 95%"),
        ("valor ≠5", "valor 5"),
        ("temperatura ﹣5℃", "temperatura 5℃"),
        ("dose $<5 mg", "dose $5 mg"),
        ("dose <$5 mg", "dose <5 mg"),
        ("dose R$ <=5 mg", "dose R$ 5 mg"),
        ("intervalo 5−3 dias", "intervalo 53 dias"),
        ("variação 5±2 mg", "variação 52 mg"),
        ("dose 5,7 mg", "dose 5, 7 mg"),
        ("dose — 2 mg", "dose -2 mg"),
        ("marcador HER2—", "marcador HER2-"),
    ),
)
def test_content_identity_preserves_spaced_numeric_modifiers(
    qualified_value: str,
    plain_value: str,
) -> None:
    assert _content_identity(qualified_value) != _content_identity(plain_value)


@pytest.mark.parametrize(
    ("compact_value", "spaced_value"),
    (
        ("limite <=5 mg", "limite <= 5 mg"),
        ("temperatura ≤-5 °C", "temperatura ≤ -5 °C"),
        ("custo R$5", "custo R$ 5"),
        ("risco 5%", "risco 5 %"),
        ("risco 5‰", "risco 5 ‰"),
        ("temperatura 5°C", "temperatura 5 °C"),
        ("PaCO2<=45", "PaCO2<= 45"),
        ("pH<7.4", "pH< 7.4"),
        ("T4≥5", "T4≥ 5"),
        ("SpO2<95%", "SpO2< 95 %"),
        ("valor ≠5", "valor ≠ 5"),
        ("temperatura ﹣5℃", "temperatura -5 °C"),
        ("dose $<5 mg", "dose $ < 5 mg"),
        ("dose <$5 mg", "dose < $ 5 mg"),
        ("dose R$<=5 mg", "dose R$ <= 5 mg"),
        ("variação 5±2 mg", "variação 5 ± 2 mg"),
        ("intervalo 5−3 dias", "intervalo 5 − 3 dias"),
        ("fração 1/2", "fração 1 / 2"),
        ("intervalo 1–2", "intervalo 1-2"),
    ),
)
def test_content_identity_normalizes_spacing_inside_numeric_expressions(
    compact_value: str,
    spaced_value: str,
) -> None:
    assert _content_identity(compact_value) == _content_identity(spaced_value)


def test_content_identity_normalizes_equivalent_nfc_and_nfd_text() -> None:
    nfc_value = "dose café5%"
    nfd_value = unicodedata.normalize("NFD", nfc_value)

    assert _content_identity(nfc_value) == _content_identity(nfd_value)


@pytest.mark.parametrize(
    ("scientific_value", "plain_value"),
    (
        ("carga viral 10⁶", "carga viral 106"),
        ("marcador HER2+", "marcador HER2"),
        ("marcador CD4+", "marcador CD4"),
        ("cálcio Ca²⁺", "cálcio Ca2"),
        ("citocina IL-6", "citocina IL 6"),
        ("infecção COVID-19", "infecção COVID 19"),
        ("gene BRCA1", "gene BRCA 1"),
        ("marcador HER2-", "marcador HER2"),
        ("valor 2−", "valor 2"),
    ),
)
def test_content_identity_preserves_biomedical_identifier_semantics(
    scientific_value: str,
    plain_value: str,
) -> None:
    assert _content_identity(scientific_value) != _content_identity(plain_value)


@pytest.mark.parametrize(
    ("vulgar_fraction", "expanded_fraction"),
    (
        ("dose ½", "dose 1/2"),
        ("dose ¼", "dose 1/4"),
        ("dose ⅝", "dose 5/8"),
    ),
)
def test_content_identity_expands_vulgar_fractions(
    vulgar_fraction: str,
    expanded_fraction: str,
) -> None:
    assert _content_identity(vulgar_fraction) == _content_identity(expanded_fraction)


@pytest.mark.parametrize(
    ("unicode_value", "canonical_value"),
    (
        ("marcador HER2−", "marcador HER2-"),
        ("marcador HER2⁻", "marcador HER2-"),
        ("marcador HER2⁺", "marcador HER2+"),
    ),
)
def test_content_identity_normalizes_equivalent_unicode_signs(
    unicode_value: str,
    canonical_value: str,
) -> None:
    assert _content_identity(unicode_value) == _content_identity(canonical_value)


@pytest.mark.parametrize("verb", ("deve", "devem", "pode", "podem"))
def test_case_topic_has_priority_over_the_generic_predicate(verb: str) -> None:
    assert (
        _flashcard_topic(f"Em caso de hemorragia {verb} interromper o procedimento.")
        == "hemorragia"
    )


def test_printable_flashcard_keeps_compound_medical_terms_unbroken() -> None:
    bundle = _bundle()
    first_card = bundle.flashcards[0].with_content(
        front="Qual cuidado pós-operatório deve ser recordado?",
        back="O cuidado pós-operatório exige monitoramento.",
    )

    typst = render_printable_flashcards_typst(bundle.with_flashcards((first_card,)))

    assert typst.count('#box[#text("pós-operatório")]') == 2


def test_mock_exam_places_the_commented_answer_key_on_a_new_page() -> None:
    document = mock_exam_document(_bundle())
    typst = render_study_document_typst(document)

    assert '#pagebreak()\n#heading(level: 1)[#text("Gabarito comentado")]' in typst
    assert all(not item.startswith("Pergunta ") for item in document.sections[0].items)


def test_typst_documents_follow_the_academic_layout_contract() -> None:
    typst = render_study_document_typst(critical_review_document(_bundle()))

    assert 'paper: "a4"' in typst
    assert "top: 30mm" in typst
    assert "bottom: 20mm" in typst
    assert "left: 30mm" in typst
    assert "right: 20mm" in typst
    assert 'font: "Arial"' in typst
    assert "size: 12pt" in typst
    assert "hyphenate: false" in typst
    assert "justify: true" in typst
    assert "leading: 1em, spacing: 18pt" in typst
    assert "first-line-indent: (amount: 1.25cm, all: true)" in typst


def test_study_edition_separates_consecutive_headings_into_paragraphs() -> None:
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Patologia no transoperatório",
        drafts=(
            _draft(
                1,
                "Diagnóstico transoperatório",
                "A avaliação microscópica orienta a conduta cirúrgica.",
            ),
        ),
    )

    typst = render_study_edition_typst(editions[StudyEditionKind.SUMMARY])

    assert (
        '#heading(level: 1)[#text("Parte 1")]\n'
        "#parbreak()\n"
        '#heading(level: 2)[#text("Pontos essenciais")]\n'
        "#parbreak()"
    ) in typst


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**Ultrassonografia**: disponível.", "Ultrassonografia: disponível."),
        ("__Colestase__: ponto de atenção.", "Colestase: ponto de atenção."),
        ("dose__mg deve ser preservado.", "dose__mg deve ser preservado."),
        (
            "__init__ representa ênfase balanceada.",
            "init representa ênfase balanceada.",
        ),
        ("**marcação sem fechamento", "**marcação sem fechamento"),
        (
            "**Primeiro** e __segundo__ achados.",
            "Primeiro e segundo achados.",
        ),
        ("Use `__init__` no exemplo.", "Use `__init__` no exemplo."),
        (r"\**literal** deve permanecer.", r"\**literal** deve permanecer."),
        (r"\__literal__ deve permanecer.", r"\__literal__ deve permanecer."),
        ("```\n**literal**\n```", "```\n**literal**\n```"),
        ("~~~python\n**literal**\n~~~", "~~~python\n**literal**\n~~~"),
        ("    **literal**", "    **literal**"),
        ("**Primeira linha\nsegunda linha**", "Primeira linha\nsegunda linha"),
        ("pré__literal__pós", "pré__literal__pós"),
        ("`sem fechamento **ênfase**", "`sem fechamento ênfase"),
        ("`__init__```", "`init```"),
        ("``__init__```", "``init```"),
        ("**primeiro\n\nsegundo**", "**primeiro\n\nsegundo**"),
    ],
)
def test_typst_list_items_remove_only_balanced_markdown_emphasis(
    source: str,
    expected: str,
) -> None:
    rendered = render_academic_list_item(
        source,
    )

    # 2026-09-15 document review, round 4: an item carrying embedded "\n"
    # now renders each line as its own hanging-indented paragraph (so an
    # option's own wrap aligns under it) instead of one #text() blob --
    # check each non-empty line's own escaped form is present, rather
    # than the whole multi-line string as a single Typst string literal.
    expected_lines = [line for line in expected.split("\n") if line]
    for line in expected_lines:
        assert typst_string(line) in rendered


def test_multi_option_item_hangs_only_each_options_own_wrap() -> None:
    """Regression: 812b489 dropped hanging-indent from every paragraph in
    this block to fix the stem/option flush-alignment complaint, which
    silently reintroduced round 4's original bug -- a wrapping option's
    own continuation line became indistinguishable from the next lettered
    option starting flush below it. 0bce9e9 restored hanging-indent on
    each option's own paragraph while keeping the stem (no marker of its
    own to hang under) flush. This pins that split so a future edit that
    zeroes `_OPTION_HANGING_INDENT` again fails a test instead of only
    showing up on a manually read PDF."""

    item = "Enunciado da questão\nA) primeira opção\nB) segunda opção"
    rendered = render_academic_list_item(item, marker="1.")

    assert "#set par(first-line-indent: 0pt, hanging-indent: 0pt" in rendered
    option_paragraphs = [
        line
        for line in rendered.splitlines()
        if line.startswith("#par(hanging-indent:")
    ]
    assert len(option_paragraphs) == 2
    for paragraph in option_paragraphs:
        assert "hanging-indent: 0pt" not in paragraph
        assert "hanging-indent: 1.4em" in paragraph


def test_list_item_overrides_leading_tighter_than_the_documents_abnt_prose() -> None:
    """Regression (2026-09-16 operator report, lecture 10003 exam question
    1): a stem long enough to wrap onto a second line inherited the
    document's global ABNT-1.5 `leading: 1em` (academic_document_start()),
    which is larger than this block's own `spacing: 8pt` between two
    distinct paragraphs -- so the stem's own same-paragraph wrap looked
    MORE separated than two genuinely distinct options. Pixel-column
    comparison on a compiled PDF confirmed the wrap itself is flush
    (not a hanging-indent bug); only the vertical gap was wrong. This
    pins the local `leading: 0.65em` override (Typst's own single-spacing
    default) so it is not silently dropped, the same way test above pins
    hanging-indent."""

    rendered = render_academic_list_item("Enunciado", marker="1.")

    # Scoped to the same #set par(...) call as spacing/hanging-indent --
    # a bare substring check would still pass if leading were misplaced
    # onto an unrelated line (principal-code-reviewer finding).
    assert "spacing: 8pt, leading: 0.65em)" in rendered


def test_stem_to_first_option_gap_is_bigger_than_option_to_option_gap() -> None:
    """Regression (2026-09-16, second operator report on the same
    question): once the wrap-gap fix above shipped, the operator asked
    for the opposite at the stem-to-first-option boundary -- a visibly
    bigger gap between the question and "A)" than the block's own
    option-to-option `spacing: 8pt`, to mark "this is where the answer
    list starts". The extra `#v(...)` must appear exactly once, right
    before the first option, and never before B/C/D."""

    item = "Enunciado\nA) primeira opção\nB) segunda opção\nC) terceira opção"
    rendered = render_academic_list_item(item, marker="1.")

    lines = rendered.splitlines()
    stem_index = next(i for i, line in enumerate(lines) if "Enunciado" in line)
    first_option_index = next(
        i for i, line in enumerate(lines) if "primeira opção" in line
    )
    assert lines[stem_index + 1] == f"#v({_STEM_TO_FIRST_OPTION_GAP})"
    assert first_option_index == stem_index + 2
    assert rendered.count(f"#v({_STEM_TO_FIRST_OPTION_GAP})") == 1


def test_stem_to_first_option_gap_never_fires_for_a_plain_multiline_item() -> None:
    """Regression (principal-code-reviewer BLOCK, 2026-09-16): the first
    cut of this feature gated on line *position* alone ("second line of
    any multi-line item"), which leaked the exam-only gap into a
    multi-line warning callout / "Pontos essenciais" summary -- neither
    has lettered options, so a reader would see an unexplained extra
    gap after the first line. Reproduces the reviewer's exact repro
    input (five identical warning lines)."""

    item = "alerta\nalerta\nalerta\nalerta\nalerta"
    rendered = render_academic_list_item(item)

    assert f"#v({_STEM_TO_FIRST_OPTION_GAP})" not in rendered


def test_stem_to_first_option_gap_survives_a_blank_line_before_option_a() -> None:
    """Regression (principal-code-reviewer BLOCK, 2026-09-16): the first
    cut cancelled the pending gap on ANY blank line, so a blank line
    between the stem and option A silently dropped the gap instead of
    composing with it. The gap must still land immediately before the
    first real lettered option, however many blank lines precede it."""

    item = "Enunciado\n\nA) primeira opção\nB) segunda opção"
    rendered = render_academic_list_item(item, marker="1.")

    lines = rendered.splitlines()
    first_option_index = next(
        i for i, line in enumerate(lines) if "primeira opção" in line
    )
    assert lines[first_option_index - 1] == f"#v({_STEM_TO_FIRST_OPTION_GAP})"
    assert rendered.count(f"#v({_STEM_TO_FIRST_OPTION_GAP})") == 1


def test_stem_to_first_option_gap_fires_through_the_real_question_producer() -> None:
    """Regression (principal-code-reviewer CONDITIONAL APPROVAL, 2026-09-16):
    the tests above pin `_OPTION_LETTER_LINE` against hand-typed literals,
    not against `_format_question_with_options`'s own real output -- the
    only live producer of exam-question items. This chains the two real
    functions so a future edit to either side of the "A) " format contract
    (e.g. dropping the trailing space, or switching to "A." markers) fails
    a test instead of staying green on both sides in isolation."""

    question = MockQuestion(
        question_text="Enunciado da questão",
        options={"A": "Um", "B": "Dois", "C": "Três", "D": "Quatro"},
        correct_answer="A",
        explanation="Explicação.",
    )

    rendered = render_academic_list_item(
        _format_question_with_options(question), marker="1."
    )

    assert rendered.count(f"#v({_STEM_TO_FIRST_OPTION_GAP})") == 1
    lines = rendered.splitlines()
    option_a_index = next(i for i, line in enumerate(lines) if "A) Um" in line)
    assert lines[option_a_index - 1] == f"#v({_STEM_TO_FIRST_OPTION_GAP})"


def test_academic_warning_block_partitions_bounded_callouts() -> None:
    rendered = render_academic_warning_block(
        tuple(f"alerta {index}" for index in range(10)),
    )

    assert rendered.count("Alertas e pontos de atenção") == 3
    assert rendered.count("breakable: false") == 3
    for index in range(10):
        assert rendered.count(f'alerta {index}"') == 1


def test_academic_warning_block_labels_only_the_first_chunk_as_the_main_title() -> None:
    """Regression (2026-09-15 document review): every chunk repeating the
    plain "Alertas e pontos de atenção" title read as several distinct,
    unrelated warning boxes stacked back to back instead of one continued
    list for a single section."""
    rendered = render_academic_warning_block(
        tuple(f"alerta {index}" for index in range(10)),
    )

    assert rendered.count("Alertas e pontos de atenção]") == 1
    assert rendered.count("Alertas e pontos de atenção (continuação)]") == 2


def test_academic_warning_block_keeps_oversized_warning_breakable() -> None:
    warning = "alerta " * 100

    rendered = render_academic_warning_block((warning,))

    assert "breakable: true" in rendered


def test_academic_warning_callouts_compile_with_section_inside_page_margins(
    tmp_path: Path,
) -> None:
    warning_blocks = render_academic_warning_block(
        tuple(f"alerta clínico {index}" for index in range(10)),
    )
    section_position = _compile_warning_pagination_document(
        tmp_path,
        warning_blocks,
        filler_height_points=590,
    )

    assert _position_is_inside_page_margins(section_position)


def test_oversized_academic_warning_compiles_without_hiding_following_section(
    tmp_path: Path,
) -> None:
    warning = "alerta clínico extenso " * 200
    warning_blocks = render_academic_warning_block((warning,))
    section_position = _compile_warning_pagination_document(
        tmp_path,
        warning_blocks,
        filler_height_points=0,
    )

    assert section_position["page"] >= 2
    assert typst_string(warning) in warning_blocks
    assert _position_is_inside_page_margins(section_position)


@pytest.mark.parametrize("line_separator", ("\n", "\u2028"))
def test_multiline_academic_warning_compiles_without_hiding_following_section(
    tmp_path: Path,
    line_separator: str,
) -> None:
    warning = line_separator.join("alerta" for _ in range(60))
    warning_blocks = render_academic_warning_block((warning,))

    section_position = _compile_warning_pagination_document(
        tmp_path,
        warning_blocks,
        filler_height_points=0,
    )

    assert len(warning) < 480
    assert "breakable: true" in warning_blocks
    assert section_position["page"] >= 2
    # 2026-09-15 document review, round 4: a "\n"-separated warning now
    # renders each line as its own hanging-indented paragraph rather than
    # one #text() blob (see render_academic_list_item) -- check every
    # "alerta" line's own escaped form is present, matching the count
    # this fixture actually joined.
    if line_separator == "\n":
        assert warning_blocks.count(typst_string("alerta")) == 60
    else:
        assert typst_string(warning) in warning_blocks
    assert _position_is_inside_page_margins(section_position)


def _compile_warning_pagination_document(
    tmp_path: Path,
    warning_blocks: str,
    *,
    filler_height_points: int,
) -> dict[str, int | str]:
    typst_executable = shutil.which("typst")
    if typst_executable is None:
        pytest.skip("Typst is required for the compiled pagination regression")
    source_path = tmp_path / f"warning-pagination-{filler_height_points}.typ"
    output_path = source_path.with_suffix(".pdf")
    source_path.write_text(
        _warning_pagination_document(warning_blocks, filler_height_points),
        encoding="utf-8",
    )
    subprocess.run(
        [typst_executable, "compile", str(source_path), str(output_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return _typst_label_position(typst_executable, source_path, "following-section")


def _warning_pagination_document(
    warning_blocks: str,
    filler_height_points: int,
) -> str:
    return "\n".join(
        (
            '#set page(paper: "a4", margin: (top: 30mm, bottom: 20mm, '
            "left: 30mm, right: 20mm))",
            '#set text(font: "Arial", size: 12pt, hyphenate: false)',
            "#set par(first-line-indent: 0pt, leading: 0.5em, spacing: 6pt)",
            f"#block(height: {filler_height_points}pt)[]",
            warning_blocks,
            '#metadata("following") <following-section>',
            '#text("Seção seguinte")',
        )
    )


def _typst_label_position(
    typst_executable: str,
    source_path: Path,
    label: str,
) -> dict[str, int | str]:
    expression = f"query(<{label}>).first().location().position()"
    completed = subprocess.run(
        [typst_executable, "eval", expression, "--in", str(source_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _typst_points(value: int | str) -> float:
    return float(str(value).removesuffix("pt"))


def _position_is_inside_page_margins(
    position: dict[str, int | str],
) -> bool:
    x_points = _typst_points(position["x"])
    y_points = _typst_points(position["y"])
    return (
        int(position["page"]) >= 2
        and 80.0 <= x_points <= 530.0
        and 80.0 <= y_points <= 760.0
    )


@pytest.mark.parametrize(
    "source",
    [
        "~~~python\n**literal**\n~~~",
        "    **literal**",
    ],
)
def test_academic_body_preserves_emphasis_markers_inside_code(source: str) -> None:
    rendered = "\n".join(render_academic_body(source))

    assert "**literal**" in rendered


def test_flashcard_csv_neutralizes_spreadsheet_formula_prefixes() -> None:
    bundle = _bundle()
    first = bundle.flashcards[0]
    unsafe_bundle = bundle.with_flashcards(
        (
            first.with_content(
                front="=SOMA(1;1)",
                back="+conteúdo",
            ),
        )
    )

    csv_text = render_flashcards_csv(unsafe_bundle)

    assert "'=SOMA(1;1)" in csv_text
    assert "'+conteúdo" in csv_text


@pytest.mark.parametrize(
    "unsafe",
    (
        "\r=SOMA(1;1)",
        "\u00a0=SUM(1,1)",
        "\v+conteúdo",
        "\f-conteúdo",
    ),
)
def test_flashcard_csv_neutralizes_control_prefixed_formulas(unsafe: str) -> None:
    sanitized = _sanitize_csv_field(unsafe)

    assert sanitized.startswith("'")


def test_typst_document_renderer_escapes_untrusted_content() -> None:
    document = critical_review_document(_bundle()).with_title('Resumo "crítico" #1')

    typst = render_study_document_typst(document)

    assert '"Resumo \\"crítico\\" #1"' in typst
    assert "[#it.body]" in typst
    assert "[it.body]" not in typst


def test_typst_prose_keeps_compound_words_on_one_line_without_mutating_text() -> None:
    assert typst_string("cuidado pós-operatório") == '"cuidado pós-operatório"'
    assert '#box[#text("pós-operatório")]' in render_typst_prose(
        "cuidado pós-operatório"
    )
    decomposed_compound = unicodedata.normalize("NFD", "pós-operatório")
    assert f"#box[#text({typst_string(decomposed_compound)})]" in render_typst_prose(
        f"cuidado {decomposed_compound}"
    )
    assert typst_string("- marcador e 18-06-2026") == '"- marcador e 18-06-2026"'
    exact_identifiers = (
        "https://example-clinic.test",
        "DOI abc-def",
        "clinic-test@example.com",
        "code-token",
    )
    for identifier in exact_identifiers:
        assert typst_string(identifier) == f'"{identifier}"'
        rendered = render_typst_prose(identifier)
        assert "‑" not in rendered
        string_literals = re.findall(r'#text\(("(?:\\.|[^"\\])*")\)', rendered)
        assert "".join(json.loads(value) for value in string_literals) == identifier


def test_questions_skip_introductory_noise_and_clean_warning_metadata() -> None:
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=(
            _draft(
                1,
                "Úlcera de córnea",
                "Bem-vindos ao curso. "
                "Hoje, vamos falar sobre o tema. "
                "Coisa que depende da córnea. "
                "A fluoresceína evidencia defeitos epiteliais da córnea.",
                warnings=[
                    "membrana de Descemet (anatomia): membrana de Descemet",
                    "contaminação (red_flag): úlcera em ceratomalácia",
                ],
            ),
        ),
    )

    bundle = build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=editions[StudyEditionKind.SUMMARY],
    )

    assert bundle.questions[0].answer == (
        "A fluoresceína evidencia defeitos epiteliais da córnea."
    )
    assert bundle.flashcards[-1].back == "Contaminação: úlcera em ceratomalácia"
    assert all(
        card.back != "Membrana de Descemet: membrana de Descemet"
        for card in bundle.flashcards
    )


def test_figure_free_section_renders_identically_to_before_figures_existed() -> None:
    document = StudyDocument(
        title="Roteiro de revisão",
        subtitle="Aula de teste",
        sections=(StudyDocumentSection(title="Etapa 1", items=("Ler a apostila.",)),),
    )

    markdown = render_study_document_markdown(document)
    typst = render_study_document_typst(document)

    assert markdown == (
        "# Roteiro de revisão\n\n## Aula de teste\n\n## Etapa 1\n\n- Ler a apostila.\n"
    )
    assert '#heading(level: 1)[#text("Etapa 1")]' in typst
    assert "#image(" not in typst
