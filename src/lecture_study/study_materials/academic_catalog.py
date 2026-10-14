"""Academic-period organization for local personal-study bundles."""

from __future__ import annotations

from collections.abc import Collection
from collections import defaultdict
from datetime import date
import os
from pathlib import Path
import re
import stat
from typing import Literal, Self
import unicodedata

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    field_validator,
    model_validator,
)

from lecture_study.domain.models import SafeIdentifier

_SAFE_IDENTIFIER_ADAPTER: TypeAdapter[SafeIdentifier] = TypeAdapter(SafeIdentifier)
# Named here, not inside ACADEMIC_BUNDLE_DOCUMENT_FILES below: that frozenset is
# the legacy bundle shape the shape validator compares for exact equality
# (_validate_legacy_bundle_shape), and the 24 already delivered bundles do not
# contain a figures subdirectory. Adding it there would mark every one of them
# incomplete -- see plan 01-11.
BUNDLE_FIGURES_SUBDIRECTORY = "figuras"
ACADEMIC_BUNDLE_DOCUMENT_FILES = frozenset(
    {
        "apostila_completa.md",
        "apostila_completa.pdf",
        "apostila_completa.typ",
        "apostila_resumida.md",
        "apostila_resumida.pdf",
        "apostila_resumida.typ",
        "flashcards.csv",
        "flashcards.md",
        "flashcards.pdf",
        "flashcards.typ",
        "indice.md",
        "mapa_temporal.csv",
        "mapa_temporal.md",
        "mapa_temporal.pdf",
        "mapa_temporal.typ",
        "podcast_revisao.m4a",
        "podcast_roteiro.md",
        "podcast_roteiro.pdf",
        "podcast_roteiro.typ",
        "resumo_critico.md",
        "resumo_critico.pdf",
        "resumo_critico.typ",
        "roteiro_revisao.md",
        "roteiro_revisao.pdf",
        "roteiro_revisao.typ",
        "simulado.md",
        "simulado.pdf",
        "simulado.typ",
    }
)


class AcademicBundleIdentityManifest(BaseModel):
    """Stable identity persisted at each personal-study bundle root."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1]
    lecture_id: SafeIdentifier


class AcademicLesson(BaseModel):
    """One validated lesson from the local portal catalog."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    activity_id: SafeIdentifier
    moodle_order: int
    section: str
    display_title: str
    url: str
    enabled: bool
    discovery_timestamp: str

    @field_validator("section", "display_title", "url")
    @classmethod
    def reject_blank_path_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("metadado acadêmico vazio")
        return value

    @property
    def is_video(self) -> bool:
        return "/mod/supervideo/" in self.url


class AcademicDiscipline(BaseModel):
    """One validated discipline and its portal lessons."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    title: str
    url: str
    program: str
    lessons: list[AcademicLesson]

    @field_validator("title", "url")
    @classmethod
    def reject_blank_group_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("metadado da disciplina vazio")
        return value


class AcademicCatalog(BaseModel):
    """Validated academic catalog consumed by study-material policies."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    disciplines: list[AcademicDiscipline]

    @model_validator(mode="after")
    def reject_duplicate_lesson_ids(self) -> Self:
        seen_ids: set[str] = set()
        for discipline in self.disciplines:
            for lesson in discipline.lessons:
                lecture_id = str(lesson.activity_id)
                if lecture_id in seen_ids:
                    raise ValueError(
                        f"catálogo acadêmico contém activity_id duplicado: {lecture_id}"
                    )
                seen_ids.add(lecture_id)
        return self


class AcademicScheduleEntry(BaseModel):
    """One lecture date confirmed by the official class schedule."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    scheduled_date: date
    schedule_topic: str

    @property
    def academic_period(self) -> str:
        return academic_period_for(self.scheduled_date)


def academic_period_for(scheduled_date: date) -> str:
    """Return the civil-semester identifier derived from an official date."""

    semester = 1 if scheduled_date.month <= 6 else 2
    return f"{scheduled_date.year}.{semester}"


def load_academic_schedule(path: Path) -> dict[str, AcademicScheduleEntry]:
    """Load and validate the local official-schedule transcription."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError(f"cronograma acadêmico inválido: {path}")
    entries = tuple(
        AcademicScheduleEntry.model_validate(entry) for entry in payload["entries"]
    )
    by_id = {entry.lecture_id: entry for entry in entries}
    if len(by_id) != len(entries):
        raise ValueError("cronograma acadêmico contém lecture_id duplicado")
    return by_id


def load_academic_catalog(path: Path) -> AcademicCatalog:
    """Load and validate the local portal catalog at its input boundary."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        return AcademicCatalog.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"catálogo acadêmico inválido: {path}") from exc


def academic_lecture_paths(
    catalog: AcademicCatalog,
    schedule: dict[str, AcademicScheduleEntry],
    *,
    lecture_ids: Collection[str] | None = None,
) -> dict[str, Path]:
    """Validate and return canonical paths for the selected video lectures."""

    paths: dict[str, Path] = {}
    owners_by_path: dict[Path, str] = {}
    missing_schedule: list[str] = []
    for discipline, lesson in _video_lessons(catalog):
        lecture_id = str(lesson.activity_id)
        if lecture_ids is not None and lecture_id not in lecture_ids:
            continue
        entry = schedule.get(lecture_id)
        if entry is None:
            missing_schedule.append(lecture_id)
            continue
        if str(entry.lecture_id) != lecture_id:
            raise ValueError(
                f"chave do cronograma não corresponde à videoaula: {lecture_id}"
            )
        relative_path = _academic_lecture_relative_path(discipline, lesson, entry)
        prior_owner = owners_by_path.get(relative_path)
        if prior_owner is not None:
            raise ValueError(
                "duas videoaulas resultam no mesmo diretório acadêmico: "
                f"{prior_owner}, {lecture_id}"
            )
        paths[lecture_id] = relative_path
        owners_by_path[relative_path] = lecture_id
    if missing_schedule:
        raise ValueError(
            "videoaulas sem data no cronograma oficial: "
            + ", ".join(sorted(missing_schedule))
        )
    return paths


def academic_lecture_relative_path(
    catalog: AcademicCatalog,
    schedule: dict[str, AcademicScheduleEntry],
    lecture_id: str,
) -> Path:
    """Return the canonical semester/discipline/section path for a video."""

    entry = schedule.get(lecture_id)
    if entry is None:
        raise ValueError(f"videoaula sem data no cronograma oficial: {lecture_id}")
    if str(entry.lecture_id) != lecture_id:
        raise ValueError(
            f"chave do cronograma não corresponde à videoaula: {lecture_id}"
        )
    discipline, lesson = _find_video_lesson(catalog, lecture_id)
    return _academic_lecture_relative_path(discipline, lesson, entry)


def read_academic_bundle_identity(bundle_dir: Path) -> str | None:
    """Return a validated manifest or legacy-index lecture identity."""

    if bundle_dir.is_symlink():
        raise ValueError(
            f"diretório acadêmico não pode ser link simbólico: {bundle_dir}"
        )
    if not bundle_dir.exists():
        return None
    _reject_symlink_components(bundle_dir.absolute())
    if not bundle_dir.is_dir():
        raise ValueError(f"destino acadêmico não é um diretório: {bundle_dir}")
    manifest_path = bundle_dir / ".bundle.json"
    index_path = bundle_dir / "indice.md"
    if manifest_path.exists() or manifest_path.is_symlink():
        _reject_identity_symlink(manifest_path)
        manifest = AcademicBundleIdentityManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        if not index_path.exists() and not index_path.is_symlink():
            raise ValueError(f"pacote acadêmico sem índice: {bundle_dir}")
        legacy_id = _read_legacy_index_identity(index_path)
        if legacy_id is not None and legacy_id != manifest.lecture_id:
            raise ValueError(
                f"manifesto e índice divergem no pacote acadêmico: {bundle_dir}"
            )
        return str(manifest.lecture_id)
    if index_path.exists() or index_path.is_symlink():
        legacy_id = _read_legacy_index_identity(index_path)
        if legacy_id is not None:
            _validate_legacy_bundle_shape(bundle_dir)
        return legacy_id
    return None


def academic_bundle_is_complete(
    bundle_dir: Path,
    lecture_id: str,
) -> bool:
    """Return completion only for a matching, non-symlink bundle identity."""

    if not bundle_dir.exists() and not bundle_dir.is_symlink():
        return False
    canonical_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(lecture_id, strict=True)
    identity = read_academic_bundle_identity(bundle_dir)
    if identity is None:
        raise ValueError(f"destino acadêmico ocupado sem identidade: {bundle_dir}")
    if identity != canonical_id:
        raise ValueError(
            f"destino acadêmico pertence à aula {identity}, não à {canonical_id}"
        )
    return True


def _academic_lecture_relative_path(
    discipline: AcademicDiscipline,
    lesson: AcademicLesson,
    entry: AcademicScheduleEntry,
) -> Path:
    year, semester = entry.academic_period.split(".")
    title = re.sub(
        r"\s*Super\s+Video\s*$",
        "",
        " ".join(lesson.display_title.split()),
        flags=re.IGNORECASE,
    )
    lecture_id = str(entry.lecture_id)
    return (
        Path(year)
        / f"{semester}-semestre"
        / _slugify_path_segment(discipline.title)
        / _slugify_path_segment(lesson.section)
        / f"{lecture_id}-{_slugify_path_segment(title)}"
    )


def render_academic_catalog(
    catalog: AcademicCatalog,
    schedule: dict[str, AcademicScheduleEntry],
    study_guides_root: Path,
    *,
    include_unscheduled: bool = False,
) -> str:
    """Render every portal video grouped by civil semester and discipline."""

    grouped: dict[str, list[tuple[str, AcademicLesson, AcademicScheduleEntry]]] = (
        defaultdict(list)
    )
    video_lessons = tuple(_video_lessons(catalog))
    scheduled_lecture_ids = {
        str(lesson.activity_id)
        for _, lesson in video_lessons
        if str(lesson.activity_id) in schedule
    }
    paths = academic_lecture_paths(
        catalog,
        schedule,
        lecture_ids=scheduled_lecture_ids if include_unscheduled else None,
    )
    unscheduled: list[tuple[str, AcademicLesson]] = []
    for discipline_record, lesson in video_lessons:
        lecture_id = str(lesson.activity_id)
        if lecture_id not in schedule:
            unscheduled.append((discipline_record.title, lesson))
            continue
        entry = schedule[lecture_id]
        grouped[entry.academic_period].append((discipline_record.title, lesson, entry))
    for lessons in grouped.values():
        lessons.sort(
            key=lambda item: (
                item[0],
                item[1].section,
                item[2].scheduled_date,
                item[1].moodle_order,
            )
        )

    lines = [
        "# Catálogo acadêmico das videoaulas",
        "",
        "Classificação por semestre civil derivada das datas do cronograma "
        "oficial da turma.",
        "",
    ]
    for period in sorted(grouped):
        year, semester = period.split(".")
        lines.extend((f"## {semester}º semestre de {year}", ""))
        current_discipline = ""
        current_section = ""
        for discipline_title, lesson, entry in grouped[period]:
            if discipline_title != current_discipline:
                current_discipline = discipline_title
                current_section = ""
                lines.extend((f"### {discipline_title}", ""))
            section = lesson.section.strip()
            if section != current_section:
                current_section = section
                lines.extend((f"#### {section}", ""))
            lecture_id = str(lesson.activity_id)
            relative_lecture_path = paths[lecture_id]
            bundle_dir = study_guides_root / relative_lecture_path
            title = " ".join(lesson.display_title.split())
            complete = academic_bundle_is_complete(bundle_dir, lecture_id)
            status = "Concluída" if complete else "Pendente"
            relative_index = (relative_lecture_path / "indice.md").as_posix()
            title_text = f"[{title}]({relative_index})" if complete else title
            lines.append(
                f"- {entry.scheduled_date:%d/%m/%Y} - {title_text} "
                f"(`{lecture_id}`) - **{status}**"
            )
        lines.append("")
    if include_unscheduled and unscheduled:
        unscheduled.sort(
            key=lambda item: (
                item[0],
                item[1].section,
                item[1].moodle_order,
            )
        )
        lines.extend(
            (
                "## Sem data oficial",
                "",
                "Estas videoaulas foram encontradas no portal, mas não foram "
                "atribuídas a um semestre porque não constam no cronograma oficial.",
                "",
            )
        )
        current_discipline = ""
        current_section = ""
        for discipline_title, lesson in unscheduled:
            if discipline_title != current_discipline:
                current_discipline = discipline_title
                current_section = ""
                lines.extend((f"### {discipline_title}", ""))
            section = lesson.section.strip()
            if section != current_section:
                current_section = section
                lines.extend((f"#### {section}", ""))
            title = " ".join(lesson.display_title.split())
            lines.append(
                f"- {title} (`{lesson.activity_id}`) - **Aguardando data oficial**"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _find_video_lesson(
    catalog: AcademicCatalog,
    lecture_id: str,
) -> tuple[AcademicDiscipline, AcademicLesson]:
    for discipline, lesson in _video_lessons(catalog):
        if lesson.activity_id == lecture_id:
            return discipline, lesson
    raise ValueError(f"videoaula ausente do catálogo do portal: {lecture_id}")


def _video_lessons(
    catalog: AcademicCatalog,
) -> tuple[tuple[AcademicDiscipline, AcademicLesson], ...]:
    return tuple(
        (discipline, lesson)
        for discipline in catalog.disciplines
        for lesson in discipline.lessons
        if lesson.is_video
    )


def _read_legacy_index_identity(index_path: Path) -> str | None:
    _reject_identity_symlink(index_path)
    if not index_path.is_file():
        raise ValueError(f"índice acadêmico não é um arquivo: {index_path}")
    prefix = index_path.read_text(encoding="utf-8")[:2_048]
    match = re.search(r"(?m)^Aula `([A-Za-z0-9_-]+)` - ", prefix)
    if match is None:
        return None
    return _SAFE_IDENTIFIER_ADAPTER.validate_python(match.group(1), strict=True)


def _validate_legacy_bundle_shape(bundle_dir: Path) -> None:
    actual_files: set[str] = set()
    video_files: list[str] = []
    for raw_dir, directory_names, filenames in os.walk(
        bundle_dir,
        followlinks=False,
    ):
        directory = Path(raw_dir)
        for name in directory_names:
            child = directory / name
            if child.is_symlink():
                raise ValueError(f"pacote legado contém diretório simbólico: {child}")
        for name in filenames:
            file_path = directory / name
            if file_path.is_symlink() or not file_path.is_file():
                raise ValueError(
                    f"pacote legado contém arquivo não regular: {file_path}"
                )
            relative_path = file_path.relative_to(bundle_dir).as_posix()
            if re.fullmatch(r"video/aula\.[A-Za-z0-9]+", relative_path):
                video_files.append(relative_path)
            else:
                actual_files.add(relative_path)
    if actual_files != ACADEMIC_BUNDLE_DOCUMENT_FILES or len(video_files) != 1:
        raise ValueError(f"pacote legado incompleto ou ambíguo: {bundle_dir}")


def _reject_identity_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"identidade acadêmica não pode ser link simbólico: {path}")


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(f"componente acadêmico é link simbólico: {current}")


def slugify_path_segment(value: str) -> str:
    """Public slugifier used for every academic path segment."""

    return _slugify_path_segment(value)


def _slugify_path_segment(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if not slug:
        raise ValueError(f"segmento acadêmico sem caracteres utilizáveis: {value!r}")
    return slug
