"""Personal study-guide edition and pt-BR rendering tests."""

import pytest

from lecture_study.domain.models import LearningUnitDraft
from lecture_study.study_materials.compiler import (
    StudyEditionKind,
    build_personal_study_editions,
    is_administrative_study_unit,
    render_study_edition_markdown,
    render_study_edition_typst,
)


def _draft(
    sequence: int,
    title: str,
    body: str,
    *,
    lecture_id: str = "lecture-001",
    warnings: list[str] | None = None,
) -> LearningUnitDraft:
    return LearningUnitDraft(
        lecture_id=lecture_id,
        pack_sequence=sequence,
        title=title,
        body_markdown=body,
        clinical_warnings=warnings or [],
    )


def test_builds_complete_and_summarized_editions_from_clinical_units() -> None:
    drafts = (
        _draft(1, "Boas-vindas ao curso", "Avisos administrativos."),
        _draft(
            4,
            "Sobre a docente",
            "Biografia e credenciais da docente.",
        ),
        _draft(
            2,
            "Teste de ameaça",
            "O teste de ameaça auxilia na avaliação da visão. "
            "O reflexo pupilar fotomotor não comprova visão.",
        ),
        _draft(
            3,
            "Úlcera de córnea",
            "A fluoresceína evidencia defeitos epiteliais da córnea.",
            warnings=["Corticoide tópico é contraindicado na úlcera de córnea."],
        ),
    )

    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )

    assert set(editions) == {
        StudyEditionKind.COMPLETE,
        StudyEditionKind.SUMMARY,
    }
    complete = editions[StudyEditionKind.COMPLETE]
    summary = editions[StudyEditionKind.SUMMARY]
    assert complete.source_unit_count == 2
    assert "Avisos administrativos" not in render_study_edition_markdown(complete)
    assert len(render_study_edition_markdown(summary)) < len(
        render_study_edition_markdown(complete)
    )


def test_normalizes_known_english_terms_and_pt_br_document_labels() -> None:
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Corneal ulcer treatment Super Video",
        drafts=(
            _draft(
                1,
                "Crosslinking e melting",
                "Laser pointer e crosslinking são termos usados na aula. "
                "O crosslinking é usado. O melting exige atenção. "
                "O flap não é recomendado. "
                "Tiras (strips), flare, hypopyon e pocket pets. "
                "Reflexo popular fotomotor, gase e cotonete estéreo. "
                "Midriase associada a alterações neurooftalmicas. "
                "Fluorescina e Oxnest. Hidroxiridina e fanciclovir. "
                "Carcinoma urotenal. Cetologia. Divulgação romba. "
                "Vaso epigástrica superficial caudal. "
                "Sarcoma de inflamação felino. "
                "Disease of larynx, neurological disorders, anesthesia, "
                "vomiting e palatine cleft. Von Langenbeck technique.",
                warnings=[
                    "quimioterapia (treatment): carcinoma urotenal",
                    "necrose (red_flag), lesão (complications), "
                    "úlcera (condition) e midríase (stress)",
                    "anisocoria (neuro-ophthalmic alterations): topical medication",
                    "únçera com antibiôtico",
                ],
            ),
        ),
    )

    markdown = render_study_edition_markdown(editions[StudyEditionKind.COMPLETE])

    assert "Apostila completa" in markdown
    assert "ponteiro laser" in markdown
    assert "reticulação corneana" in markdown
    assert "a reticulação corneana é usada" in markdown.lower()
    assert "ceratomalácia" in markdown
    assert "crosslinking" not in markdown.lower()
    assert "melting" not in markdown.lower()
    assert "retalho não é recomendado" in markdown
    assert "flap" not in markdown.lower()
    assert "Tiras, efeito Tyndall, hipópio" in markdown
    assert "animais de companhia não convencionais" in markdown
    assert "alerta clínico" in markdown
    assert "complicações" in markdown
    assert "condição" in markdown
    assert "estresse" in markdown
    assert "alterações neuro-oftálmicas" in markdown
    assert "medicação tópica" in markdown
    assert "úlcera com antibiótico" in markdown
    assert "reflexo pupilar fotomotor" in markdown.lower()
    assert "gaze e cotonete estéril" in markdown
    assert "midríase associada a alterações neuro-oftálmicas" in markdown
    assert "fluoresceína e Oxinest" in markdown
    assert "idoxuridina e famciclovir" in markdown
    assert "carcinoma urotelial" in markdown
    assert "citologia" in markdown
    assert "cetologia" not in markdown.casefold()
    assert "divulsão romba" in markdown
    assert "vaso epigástrico superficial caudal" in markdown
    assert "sarcoma de aplicação felino" in markdown
    assert "doença da laringe" in markdown
    assert "distúrbios neurológicos" in markdown
    assert "anestesia" in markdown
    assert "vômito" in markdown
    assert "fenda palatina" in markdown
    assert "técnica de von Langenbeck" in markdown
    for english_term in (
        "strips",
        "flare",
        "hypopyon",
        "pocket pets",
        "disease of larynx",
        "neurological disorders",
        "anesthesia",
        "vomiting",
        "palatine cleft",
        "von Langenbeck technique",
    ):
        assert english_term not in markdown.lower()
    for english_label in (
        "red_flag",
        "complications",
        "condition",
        "stress",
    ):
        assert f"({english_label})" not in markdown.lower()
    assert "(tratamento)" in markdown
    assert "treatment" not in markdown.lower()
    assert "Corneal ulcer treatment" not in markdown
    assert "vídeo complementar" in markdown
    assert "super video" not in markdown.lower()


@pytest.mark.parametrize(
    "lecture_title",
    [
        "Técnica cirúrgica Super Video",
        "Técnica cirúrgica\\nSuper Video",
        "Técnica cirúrgica\nSuper Video",
    ],
)
def test_normalizes_the_supplementary_video_lecture_title(lecture_title: str) -> None:
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title=lecture_title,
        drafts=(_draft(1, "Técnica cirúrgica", "Conteúdo cirúrgico."),),
    )

    markdown = render_study_edition_markdown(editions[StudyEditionKind.COMPLETE])

    assert "Técnica cirúrgica - vídeo complementar" in markdown
    assert "super video" not in markdown.lower()


@pytest.mark.parametrize(
    "title",
    [
        "Sobre a docente",
        "Sobre o docente",
        "Sobre a professora",
        "Sobre o professor",
        "Sobre a palestrante",
        "Sobre o palestrante",
        "SOBRE O PALESTRANTE convidado",
    ],
)
def test_speaker_introductions_are_administrative_units(title: str) -> None:
    assert is_administrative_study_unit(title)


@pytest.mark.parametrize(
    "title",
    [
        "Sobre a técnica de sutura",
        "Professor de cirurgia demonstra a sutura",
        "Sobre os docentes da disciplina",
    ],
)
def test_clinical_titles_mentioning_staff_are_not_administrative(title: str) -> None:
    assert not is_administrative_study_unit(title)


def test_rejects_draft_from_another_lecture() -> None:
    try:
        build_personal_study_editions(
            lecture_id="lecture-001",
            lecture_title="Oftalmologia",
            drafts=(
                _draft(
                    1,
                    "Úlcera de córnea",
                    "Conteúdo clínico.",
                    lecture_id="lecture-002",
                ),
            ),
        )
    except ValueError as error:
        assert "does not match lecture" in str(error)
    else:
        raise AssertionError("mismatched lecture draft was accepted")


def test_typst_renderer_escapes_model_control_characters() -> None:
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title='Olho "vermelho"',
        drafts=(
            _draft(
                1,
                "Teste #1",
                'Texto com "aspas", barra \\ e marcador #.',
            ),
        ),
    )

    typst = render_study_edition_typst(editions[StudyEditionKind.COMPLETE])

    assert '"Olho \\"vermelho\\""' in typst
    assert "barra \\\\ e marcador #" in typst
    assert "[#it.body]" in typst
    assert "[it.body]" not in typst
