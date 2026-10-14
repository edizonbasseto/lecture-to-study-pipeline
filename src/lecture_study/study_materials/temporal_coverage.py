"""Audit temporal accountability from source video to personal study material."""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from io import StringIO
from typing import Final, Sequence

from lecture_study.domain.models import EvidencePack, LearningUnitDraft
from lecture_study.evidence.chunker import MINIMUM_LEARNING_UNIT_DURATION_SECONDS
from lecture_study.study_materials.bundle import (
    StudyDocument,
    StudyDocumentSection,
)
from lecture_study.study_materials.compiler import (
    StudyEdition,
    StudyEditionKind,
    is_administrative_study_unit,
)

_GAP_WARNING_SECONDS: Final = 1.0
_INTERNAL_GAP_TOLERANCE_SECONDS: Final = 5.0
_EDGE_GAP_TOLERANCE_SECONDS: Final = 60.0
_MAXIMUM_UNNARRATED_TAIL_SECONDS: Final = 1800.0
_MINIMUM_UNIT_SECONDS: Final = MINIMUM_LEARNING_UNIT_DURATION_SECONDS
_COARSE_UNIT_SECONDS: Final = 900.0
_HIGH_COMPRESSION_RATIO: Final = 12.0
_HIGH_GLOBAL_COMPRESSION_RATIO: Final = 6.0
_HIGH_TEMPERATURE_MARKERS: Final = (
    "alerta",
    "complica",
    "contraind",
    "dose",
    "mg/",
    "necrose",
    "perfuração",
    "risco",
    "urgência",
)
_CSV_FORMULA_TRIGGERS: Final = ("=", "+", "-", "@", "\t")


class CoverageDisposition(StrEnum):
    """The explicit study-material outcome for a source interval."""

    GENERATED = "generated"
    EXCLUDED_ADMINISTRATIVE = "excluded_administrative"

    @property
    def pt_br_label(self) -> str:
        if self is CoverageDisposition.GENERATED:
            return "Material gerado"
        return "Trecho administrativo excluído"


class CoverageStatus(StrEnum):
    """The structural temporal-coverage verdict."""

    PROTECTED = "protected"
    ATTENTION = "attention"

    @property
    def pt_br_label(self) -> str:
        if self is CoverageStatus.PROTECTED:
            return "PROTEGIDA"
        return "ATENÇÃO"


@dataclass(frozen=True, slots=True)
class TemporalCoverageUnit:
    """One evidence-pack disposition with its source-to-study compression."""

    pack_sequence: int
    start_time: float
    end_time: float
    chapter_title: str
    section_title: str
    disposition: CoverageDisposition
    temperature: int
    source_word_count: int
    study_word_count: int

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time

    @property
    def compression_ratio(self) -> float:
        return self.source_word_count / max(self.study_word_count, 1)


@dataclass(frozen=True, slots=True)
class TemporalCoverageMinute:
    """One minute-level temperature and material destination."""

    start_time: float
    end_time: float
    temperature: int
    disposition: CoverageDisposition
    section_titles: tuple[str, ...]
    pack_sequences: tuple[int, ...]
    source_word_count: int
    study_word_count: int

    @property
    def interval_label(self) -> str:
        return f"{_timestamp(self.start_time)}-{_timestamp(self.end_time)}"


@dataclass(frozen=True, slots=True)
class TemporalCoverageReport:
    """A structurally complete, minute-level source accountability report."""

    lecture_id: str
    lecture_title: str
    video_duration_seconds: float
    covered_seconds: float
    source_word_count: int
    study_word_count: int
    units: tuple[TemporalCoverageUnit, ...]
    minutes: tuple[TemporalCoverageMinute, ...]
    warnings: tuple[str, ...]
    status: CoverageStatus
    structurally_complete: bool = True

    @property
    def coverage_percent(self) -> float:
        return 100.0 * self.covered_seconds / self.video_duration_seconds

    @property
    def compression_ratio(self) -> float:
        return self.source_word_count / max(self.study_word_count, 1)


def build_temporal_coverage_report(
    *,
    video_duration_seconds: float,
    packs: Sequence[EvidencePack],
    drafts: Sequence[LearningUnitDraft],
    complete: StudyEdition,
) -> TemporalCoverageReport:
    """Build a fail-closed temporal disposition for every source pack."""

    _validate_inputs(video_duration_seconds, packs, drafts, complete)
    ordered_packs = tuple(sorted(packs, key=lambda pack: pack.pack_sequence))
    drafts_by_sequence = {draft.pack_sequence: draft for draft in drafts}
    study_destinations = _study_destinations(complete)
    units = tuple(
        _coverage_unit(
            pack,
            drafts_by_sequence[pack.pack_sequence],
            study_destinations,
        )
        for pack in ordered_packs
    )
    warnings = (
        *_timeline_gap_warnings(video_duration_seconds, ordered_packs),
        *_coverage_warnings(units),
    )
    return TemporalCoverageReport(
        lecture_id=complete.lecture_id,
        lecture_title=complete.lecture_title,
        video_duration_seconds=video_duration_seconds,
        covered_seconds=_covered_seconds(ordered_packs, video_duration_seconds),
        source_word_count=sum(unit.source_word_count for unit in units),
        study_word_count=sum(unit.study_word_count for unit in units),
        units=units,
        minutes=_minute_map(video_duration_seconds, units),
        warnings=warnings,
        status=(CoverageStatus.ATTENTION if warnings else CoverageStatus.PROTECTED),
    )


def temporal_coverage_document(
    report: TemporalCoverageReport,
) -> StudyDocument:
    """Present the coverage verdict and minute map as a study document."""

    diagnostic_items = (
        f"Duração do vídeo: {_timestamp(report.video_duration_seconds)}.",
        (f"Cobertura temporal estrutural: {report.coverage_percent:.2f}% do vídeo."),
        f"Situação: {report.status.pt_br_label}.",
        (
            f"Compressão textual: {report.source_word_count} palavras de origem "
            f"para {report.study_word_count} palavras de estudo "
            f"({report.compression_ratio:.1f}:1)."
        ),
        (
            "Escala: T0 administrativo; T2 conteúdo de estudo; "
            "T3 alerta, risco, dose ou ponto crítico."
        ),
        (
            "Este mapa prova a destinação temporal dos blocos, não equivalência "
            "semântica integral entre a fala e o material."
        ),
    )
    warning_section = (
        (
            StudyDocumentSection(
                title="Pontos que exigem revisão",
                items=report.warnings,
            ),
        )
        if report.warnings
        else ()
    )
    timeline_sections = tuple(
        StudyDocumentSection(
            title=f"Minutos {group_start + 1} a {min(group_start + 10, len(report.minutes))}",
            items=tuple(
                _minute_document_item(minute)
                for minute in report.minutes[group_start : group_start + 10]
            ),
        )
        for group_start in range(0, len(report.minutes), 10)
    )
    return StudyDocument(
        title="Mapa de temperatura e cobertura temporal",
        subtitle=report.lecture_title,
        sections=(
            StudyDocumentSection(
                title="Diagnóstico de cobertura",
                items=diagnostic_items,
            ),
            *warning_section,
            *timeline_sections,
        ),
    )


def render_temporal_coverage_csv(report: TemporalCoverageReport) -> str:
    """Render the minute map as a spreadsheet-safe CSV."""

    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        (
            "intervalo",
            "temperatura",
            "situacao",
            "secoes",
            "blocos_fonte",
            "palavras_origem",
            "palavras_estudo",
        )
    )
    for minute in report.minutes:
        writer.writerow(
            (
                minute.interval_label,
                f"T{minute.temperature}",
                minute.disposition.pt_br_label,
                _safe_csv_field(" | ".join(minute.section_titles)),
                " ".join(str(sequence) for sequence in minute.pack_sequences),
                minute.source_word_count,
                minute.study_word_count,
            )
        )
    return output.getvalue()


def _validate_inputs(
    video_duration_seconds: float,
    packs: Sequence[EvidencePack],
    drafts: Sequence[LearningUnitDraft],
    complete: StudyEdition,
) -> None:
    if video_duration_seconds <= 0:
        raise ValueError("video duration must be greater than zero")
    if complete.kind is not StudyEditionKind.COMPLETE:
        raise ValueError("temporal coverage requires the complete edition")
    if not packs:
        raise ValueError("temporal coverage requires evidence packs")
    pack_sequences = {pack.pack_sequence for pack in packs}
    draft_sequences = {draft.pack_sequence for draft in drafts}
    if len(pack_sequences) != len(packs) or len(draft_sequences) != len(drafts):
        raise ValueError("duplicate pack or draft sequence")
    if pack_sequences != draft_sequences:
        raise ValueError("pack sequences differ from draft sequences")
    if any(str(pack.lecture_id) != complete.lecture_id for pack in packs):
        raise ValueError("evidence pack belongs to another lecture")
    if any(str(draft.lecture_id) != complete.lecture_id for draft in drafts):
        raise ValueError("draft belongs to another lecture")
    _validate_timeline(video_duration_seconds, packs)
    _validate_study_dispositions(drafts, complete)


def _validate_timeline(
    video_duration_seconds: float,
    packs: Sequence[EvidencePack],
) -> None:
    timeline = tuple(sorted(packs, key=lambda pack: (pack.start_time, pack.end_time)))
    if timeline[0].start_time > _EDGE_GAP_TOLERANCE_SECONDS:
        raise ValueError("unaccounted timeline gap before first pack")
    for previous, current in zip(timeline, timeline[1:]):
        gap = current.start_time - previous.end_time
        if gap > _INTERNAL_GAP_TOLERANCE_SECONDS:
            raise ValueError(
                f"unaccounted timeline gap of {gap:.2f} seconds "
                f"before pack {current.pack_sequence}"
            )
    trailing_gap = video_duration_seconds - timeline[-1].end_time
    tolerated_tail = _is_tolerated_unnarrated_tail(timeline, trailing_gap)
    if trailing_gap > _EDGE_GAP_TOLERANCE_SECONDS and not tolerated_tail:
        raise ValueError(
            f"unaccounted timeline gap of {trailing_gap:.2f} seconds after final pack"
        )
    if timeline[-1].end_time - video_duration_seconds > _EDGE_GAP_TOLERANCE_SECONDS:
        raise ValueError("evidence timeline exceeds video duration")


def _validate_study_dispositions(
    drafts: Sequence[LearningUnitDraft],
    complete: StudyEdition,
) -> None:
    included_sequences = set(_study_destinations(complete))
    for draft in drafts:
        if (
            draft.pack_sequence not in included_sequences
            and not is_administrative_study_unit(draft.title)
        ):
            raise ValueError(
                f"non-administrative draft {draft.pack_sequence} was excluded"
            )


def _study_destinations(
    complete: StudyEdition,
) -> dict[int, tuple[str, str]]:
    destinations: dict[int, tuple[str, str]] = {}
    for chapter in complete.chapters:
        for section in chapter.sections:
            for sequence in section.source_sequences:
                if sequence in destinations:
                    raise ValueError(
                        f"source sequence {sequence} has multiple study destinations"
                    )
                destinations[sequence] = (chapter.title, section.title)
    return destinations


def _coverage_unit(
    pack: EvidencePack,
    draft: LearningUnitDraft,
    study_destinations: dict[int, tuple[str, str]],
) -> TemporalCoverageUnit:
    destination = study_destinations.get(pack.pack_sequence)
    disposition = (
        CoverageDisposition.GENERATED
        if destination is not None
        else CoverageDisposition.EXCLUDED_ADMINISTRATIVE
    )
    chapter_title, section_title = destination or (
        "Conteúdo administrativo",
        draft.title,
    )
    return TemporalCoverageUnit(
        pack_sequence=pack.pack_sequence,
        start_time=pack.start_time,
        end_time=pack.end_time,
        chapter_title=chapter_title,
        section_title=section_title,
        disposition=disposition,
        temperature=_temperature(draft, disposition),
        source_word_count=sum(_word_count(segment.text) for segment in pack.segments),
        study_word_count=(
            _word_count(draft.body_markdown)
            + sum(_word_count(warning) for warning in draft.clinical_warnings)
            if disposition is CoverageDisposition.GENERATED
            else 0
        ),
    )


def _temperature(
    draft: LearningUnitDraft,
    disposition: CoverageDisposition,
) -> int:
    if disposition is CoverageDisposition.EXCLUDED_ADMINISTRATIVE:
        return 0
    content = " ".join(
        (draft.title, draft.body_markdown, *draft.clinical_warnings)
    ).casefold()
    if draft.clinical_warnings or any(
        marker in content for marker in _HIGH_TEMPERATURE_MARKERS
    ):
        return 3
    return 2


def _coverage_warnings(
    units: Sequence[TemporalCoverageUnit],
) -> tuple[str, ...]:
    warnings: list[str] = []
    generated_units = tuple(
        unit for unit in units if unit.disposition is CoverageDisposition.GENERATED
    )
    global_compression_ratio = sum(
        unit.source_word_count for unit in generated_units
    ) / max(sum(unit.study_word_count for unit in generated_units), 1)
    if global_compression_ratio > _HIGH_GLOBAL_COMPRESSION_RATIO:
        warnings.append(
            "A aula apresenta compressão textual global elevada: "
            f"{global_compression_ratio:.1f}:1."
        )
    for unit in units:
        if unit.disposition is not CoverageDisposition.GENERATED:
            continue
        if unit.duration_seconds > _COARSE_UNIT_SECONDS:
            warnings.append(
                f"O bloco {unit.pack_sequence} tem granularidade insuficiente: "
                f"{unit.duration_seconds / 60:.1f} minutos em uma única unidade."
            )
        if unit.compression_ratio > _HIGH_COMPRESSION_RATIO:
            warnings.append(
                f"O bloco {unit.pack_sequence} apresenta compressão elevada: "
                f"{unit.compression_ratio:.1f}:1."
            )
    return tuple(warnings)


def _timeline_gap_warnings(
    video_duration_seconds: float,
    packs: Sequence[EvidencePack],
) -> tuple[str, ...]:
    timeline = tuple(sorted(packs, key=lambda pack: (pack.start_time, pack.end_time)))
    warnings: list[str] = []
    leading_gap = max(0.0, timeline[0].start_time)
    if leading_gap > _GAP_WARNING_SECONDS:
        warnings.append(
            "Trecho inicial sem bloco de evidência: "
            f"00:00-{_timestamp(timeline[0].start_time)} "
            f"({leading_gap:.2f} segundos)."
        )
    for previous, current in zip(timeline, timeline[1:]):
        gap = current.start_time - previous.end_time
        if gap > _GAP_WARNING_SECONDS:
            warnings.append(
                "Pausa interna sem bloco de evidência: "
                f"{_timestamp(previous.end_time)}-"
                f"{_timestamp(current.start_time)} "
                f"({gap:.2f} segundos), antes do bloco "
                f"{current.pack_sequence}."
            )
    trailing_gap = max(0.0, video_duration_seconds - timeline[-1].end_time)
    if trailing_gap > _EDGE_GAP_TOLERANCE_SECONDS and _is_tolerated_unnarrated_tail(
        timeline, trailing_gap
    ):
        warnings.append(_missing_audio_warning(timeline[-1], video_duration_seconds))
    elif trailing_gap > _GAP_WARNING_SECONDS:
        warnings.append(
            "Trecho final sem bloco de evidência: "
            f"{_timestamp(timeline[-1].end_time)}-"
            f"{_timestamp(video_duration_seconds)} "
            f"({trailing_gap:.2f} segundos)."
        )
    return tuple(warnings)


def _is_tolerated_unnarrated_tail(
    timeline: Sequence[EvidencePack],
    trailing_gap: float,
) -> bool:
    """A marked sole short pack whose video continues, bounded, after the narration."""
    only_pack = timeline[0]
    return (
        len(timeline) == 1
        and only_pack.duration_exception is not None
        and only_pack.end_time - only_pack.start_time < _MINIMUM_UNIT_SECONDS
        and trailing_gap <= _MAXIMUM_UNNARRATED_TAIL_SECONDS
    )


def _missing_audio_warning(
    final_pack: EvidencePack,
    video_duration_seconds: float,
) -> str:
    missing_seconds = video_duration_seconds - final_pack.end_time
    return (
        "Áudio ausente: a narração termina em "
        f"{_timestamp(final_pack.end_time)} e o restante do vídeo "
        f"({_timestamp(final_pack.end_time)}-{_timestamp(video_duration_seconds)}, "
        f"{missing_seconds:.0f} segundos) não tem fala. Esse trecho não foi "
        "transcrito e não está coberto por este material; o conteúdo visual "
        "dele deve ser consultado no vídeo original."
    )


def _minute_map(
    video_duration_seconds: float,
    units: Sequence[TemporalCoverageUnit],
) -> tuple[TemporalCoverageMinute, ...]:
    return tuple(
        _coverage_minute(
            start_time=float(minute_index * 60),
            end_time=min(video_duration_seconds, float((minute_index + 1) * 60)),
            units=units,
        )
        for minute_index in range(math.ceil(video_duration_seconds / 60))
    )


def _coverage_minute(
    *,
    start_time: float,
    end_time: float,
    units: Sequence[TemporalCoverageUnit],
) -> TemporalCoverageMinute:
    overlapping = tuple(
        unit
        for unit in units
        if unit.start_time < end_time and unit.end_time > start_time
    )
    generated = tuple(
        unit
        for unit in overlapping
        if unit.disposition is CoverageDisposition.GENERATED
    )
    selected = generated or overlapping
    disposition = (
        CoverageDisposition.GENERATED
        if generated
        else CoverageDisposition.EXCLUDED_ADMINISTRATIVE
    )
    return TemporalCoverageMinute(
        start_time=start_time,
        end_time=end_time,
        temperature=max((unit.temperature for unit in selected), default=0),
        disposition=disposition,
        section_titles=tuple(dict.fromkeys(unit.section_title for unit in selected)),
        pack_sequences=tuple(unit.pack_sequence for unit in selected),
        source_word_count=round(
            sum(
                _allocated_words(unit, start_time, end_time, source=True)
                for unit in selected
            )
        ),
        study_word_count=round(
            sum(
                _allocated_words(unit, start_time, end_time, source=False)
                for unit in selected
            )
        ),
    )


def _allocated_words(
    unit: TemporalCoverageUnit,
    start_time: float,
    end_time: float,
    *,
    source: bool,
) -> float:
    overlap = max(0.0, min(unit.end_time, end_time) - max(unit.start_time, start_time))
    word_count = unit.source_word_count if source else unit.study_word_count
    return word_count * overlap / unit.duration_seconds


def _covered_seconds(
    packs: Sequence[EvidencePack],
    video_duration_seconds: float,
) -> float:
    timeline = sorted(
        (
            max(0.0, pack.start_time),
            min(video_duration_seconds, pack.end_time),
        )
        for pack in packs
    )
    covered = 0.0
    current_start, current_end = timeline[0]
    for start_time, end_time in timeline[1:]:
        if start_time <= current_end:
            current_end = max(current_end, end_time)
            continue
        covered += current_end - current_start
        current_start, current_end = start_time, end_time
    return covered + current_end - current_start


def _minute_document_item(minute: TemporalCoverageMinute) -> str:
    sections = "; ".join(minute.section_titles)
    packs = ", ".join(str(sequence) for sequence in minute.pack_sequences)
    return (
        f"{minute.interval_label} | T{minute.temperature} | "
        f"{minute.disposition.pt_br_label} | {sections} | blocos {packs}."
    )


def _timestamp(seconds: float) -> str:
    rounded_seconds = max(0, round(seconds))
    hours, remainder = divmod(rounded_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"
    return f"{minutes:02d}:{seconds_part:02d}"


def _word_count(value: str) -> int:
    return len(re.findall(r"\b\w+\b", value, flags=re.UNICODE))


def _safe_csv_field(value: str) -> str:
    stripped = value.lstrip(" \t")
    return f"'{value}" if stripped.startswith(_CSV_FORMULA_TRIGGERS) else value
