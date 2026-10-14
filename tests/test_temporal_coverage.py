"""Temporal accountability tests for personal study materials."""

import pytest

from lecture_study.domain.models import (
    DurationException,
    DurationExceptionReason,
    EvidencePack,
    LearningUnitDraft,
    TranscriptionSegment,
)
from lecture_study.study_materials.compiler import (
    StudyEditionKind,
    build_personal_study_editions,
)
from lecture_study.study_materials.temporal_coverage import (
    CoverageDisposition,
    CoverageStatus,
    build_temporal_coverage_report,
    render_temporal_coverage_csv,
    temporal_coverage_document,
)


def _pack(
    sequence: int,
    start_time: float,
    end_time: float,
    text: str,
) -> EvidencePack:
    return EvidencePack(
        lecture_id="lecture-001",
        pack_sequence=sequence,
        start_time=start_time,
        end_time=end_time,
        segments=[
            TranscriptionSegment(
                start_time=start_time,
                end_time=end_time,
                text=text,
            )
        ],
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


def _report():
    packs = (
        _pack(
            1,
            0.0,
            60.0,
            "A fluoresceína identifica defeitos epiteliais da córnea.",
        ),
        _pack(
            2,
            60.0,
            120.0,
            "Encerramento da aula e agradecimentos.",
        ),
    )
    drafts = (
        _draft(
            1,
            "Úlcera de córnea",
            "A fluoresceína identifica defeitos epiteliais da córnea.",
            warnings=["Corticoide tópico é contraindicado na úlcera de córnea."],
        ),
        _draft(2, "Encerramento da aula", "Agradecimentos finais."),
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )
    return build_temporal_coverage_report(
        video_duration_seconds=120.0,
        packs=packs,
        drafts=drafts,
        complete=editions[StudyEditionKind.COMPLETE],
    )


def test_report_dispositions_every_minute_without_silent_drops() -> None:
    report = _report()

    assert report.status is CoverageStatus.PROTECTED
    assert report.structurally_complete
    assert report.covered_seconds == 120.0
    assert len(report.minutes) == 2
    assert report.minutes[0].disposition is CoverageDisposition.GENERATED
    assert report.minutes[0].temperature == 3
    assert report.minutes[1].disposition is CoverageDisposition.EXCLUDED_ADMINISTRATIVE
    assert report.minutes[1].temperature == 0


def test_report_fails_when_a_source_pack_has_no_draft() -> None:
    drafts = (
        _draft(
            1,
            "Córnea",
            "A fluoresceína identifica defeitos epiteliais da córnea.",
        ),
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )

    with pytest.raises(ValueError, match="pack sequences differ"):
        build_temporal_coverage_report(
            video_duration_seconds=120.0,
            packs=(
                _pack(1, 0.0, 60.0, "Conteúdo clínico sobre a córnea."),
                _pack(2, 60.0, 120.0, "Conteúdo clínico sobre a retina."),
            ),
            drafts=drafts,
            complete=editions[StudyEditionKind.COMPLETE],
        )


def test_report_rejects_an_unaccounted_timeline_gap() -> None:
    drafts = (
        _draft(1, "Córnea", "A fluoresceína avalia a córnea."),
        _draft(2, "Retina", "A oftalmoscopia avalia a retina."),
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )

    with pytest.raises(ValueError, match="unaccounted timeline gap"):
        build_temporal_coverage_report(
            video_duration_seconds=120.0,
            packs=(
                _pack(1, 0.0, 50.0, "Conteúdo clínico sobre a córnea."),
                _pack(2, 60.0, 120.0, "Conteúdo clínico sobre a retina."),
            ),
            drafts=drafts,
            complete=editions[StudyEditionKind.COMPLETE],
        )


def test_report_accounts_for_a_short_internal_gap() -> None:
    drafts = (
        _draft(1, "Avaliação", "A avaliação define a conduta."),
        _draft(2, "Tratamento", "O tratamento segue a avaliação."),
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oncologia veterinária",
        drafts=drafts,
    )

    report = build_temporal_coverage_report(
        video_duration_seconds=120.0,
        packs=(
            _pack(1, 0.0, 50.0, "Conteúdo clínico de avaliação."),
            _pack(2, 51.4, 120.0, "Conteúdo clínico de tratamento."),
        ),
        drafts=drafts,
        complete=editions[StudyEditionKind.COMPLETE],
    )

    assert report.status is CoverageStatus.ATTENTION
    assert report.covered_seconds == pytest.approx(118.6)
    assert any(
        "Pausa interna sem bloco de evidência: 00:50-00:51" in warning
        for warning in report.warnings
    )


def test_report_accounts_for_a_short_trailing_edge_gap() -> None:
    drafts = (
        _draft(
            1,
            "Afecções palpebrais",
            "O exame palpebral orienta o diagnóstico.",
        ),
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )

    report = build_temporal_coverage_report(
        video_duration_seconds=120.0,
        packs=(
            _pack(
                1,
                0.0,
                104.41,
                "Conteúdo clínico seguido por encerramento sem fala.",
            ),
        ),
        drafts=drafts,
        complete=editions[StudyEditionKind.COMPLETE],
    )

    assert report.status is CoverageStatus.ATTENTION
    assert report.covered_seconds == pytest.approx(104.41)
    assert any(
        "Trecho final sem bloco de evidência: 01:44-02:00" in warning
        for warning in report.warnings
    )


def test_report_rejects_a_long_trailing_edge_gap() -> None:
    drafts = (_draft(1, "Córnea", "A fluoresceína avalia a córnea."),)
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )

    with pytest.raises(ValueError, match="after final pack"):
        build_temporal_coverage_report(
            video_duration_seconds=120.0,
            packs=(_pack(1, 0.0, 59.0, "Conteúdo clínico sobre a córnea."),),
            drafts=drafts,
            complete=editions[StudyEditionKind.COMPLETE],
        )


def test_report_documents_missing_audio_after_a_short_lecture_narration() -> None:
    drafts = (_draft(1, "Córnea", "A fluoresceína avalia a córnea."),)
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )
    short_pack = _pack(1, 0.0, 204.0, "Conteúdo clínico sobre a córnea.").model_copy(
        update={
            "duration_exception": DurationException(
                reason=DurationExceptionReason.LECTURE_SHORTER_THAN_MINIMUM_UNIT
            )
        }
    )

    report = build_temporal_coverage_report(
        video_duration_seconds=657.0,
        packs=(short_pack,),
        drafts=drafts,
        complete=editions[StudyEditionKind.COMPLETE],
    )

    assert report.status is CoverageStatus.ATTENTION
    assert any(
        warning.startswith("Áudio ausente: a narração termina em 03:24")
        for warning in report.warnings
    )


def _marked(pack: EvidencePack) -> EvidencePack:
    return pack.model_copy(
        update={
            "duration_exception": DurationException(
                reason=DurationExceptionReason.LECTURE_SHORTER_THAN_MINIMUM_UNIT
            )
        }
    )


def _coverage_for(packs: tuple[EvidencePack, ...], video_seconds: float):
    drafts = tuple(
        _draft(pack.pack_sequence, "Córnea", "Texto clínico.") for pack in packs
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001", lecture_title="Oftalmologia", drafts=drafts
    )
    return build_temporal_coverage_report(
        video_duration_seconds=video_seconds,
        packs=packs,
        drafts=drafts,
        complete=editions[StudyEditionKind.COMPLETE],
    )


def test_marker_on_last_pack_of_multi_pack_lecture_keeps_trailing_gap_fatal() -> None:
    packs = (
        _pack(1, 0.0, 400.0, "Primeira parte."),
        _marked(_pack(2, 400.0, 600.0, "Segunda parte.")),
    )

    with pytest.raises(ValueError, match="after final pack"):
        _coverage_for(packs, 1_200.0)


def test_marker_on_a_pack_of_minimum_duration_keeps_trailing_gap_fatal() -> None:
    packs = (_marked(_pack(1, 0.0, 300.0, "Conteúdo.")),)

    with pytest.raises(ValueError, match="after final pack"):
        _coverage_for(packs, 900.0)


def test_unnarrated_tail_beyond_the_maximum_is_rejected() -> None:
    packs = (_marked(_pack(1, 0.0, 204.0, "Conteúdo.")),)

    with pytest.raises(ValueError, match="after final pack"):
        _coverage_for(packs, 204.0 + 1_801.0)


def test_report_flags_coarse_or_highly_compressed_source_units() -> None:
    source = " ".join(["córnea"] * 500)
    packs = (_pack(1, 0.0, 1_000.0, source),)
    drafts = (_draft(1, "Córnea", "A fluoresceína avalia a córnea."),)
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )

    report = build_temporal_coverage_report(
        video_duration_seconds=1_000.0,
        packs=packs,
        drafts=drafts,
        complete=editions[StudyEditionKind.COMPLETE],
    )

    assert report.status is CoverageStatus.ATTENTION
    assert any("granularidade" in warning for warning in report.warnings)
    assert any("compressão" in warning for warning in report.warnings)


def test_report_flags_high_global_compression_even_below_unit_threshold() -> None:
    source = " ".join(["córnea"] * 70)
    drafts = (
        _draft(
            1,
            "Córnea",
            "A fluoresceína avalia defeitos epiteliais na superfície da córnea.",
        ),
    )
    editions = build_personal_study_editions(
        lecture_id="lecture-001",
        lecture_title="Oftalmologia veterinária",
        drafts=drafts,
    )

    report = build_temporal_coverage_report(
        video_duration_seconds=60.0,
        packs=(_pack(1, 0.0, 60.0, source),),
        drafts=drafts,
        complete=editions[StudyEditionKind.COMPLETE],
    )

    assert report.status is CoverageStatus.ATTENTION
    assert any("global elevada" in warning for warning in report.warnings)


def test_report_renders_human_and_machine_readable_minute_maps() -> None:
    report = _report()

    document = temporal_coverage_document(report)
    csv_text = render_temporal_coverage_csv(report)

    assert document.title == "Mapa de temperatura e cobertura temporal"
    assert "00:00-01:00" in str(document.sections)
    assert "01:00-02:00" in str(document.sections)
    assert "intervalo,temperatura,situacao" in csv_text
    assert "Úlcera de córnea" in csv_text
