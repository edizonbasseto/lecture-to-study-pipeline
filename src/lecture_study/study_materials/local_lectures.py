"""Local lectures: videos that have no portal id (additive, fail-closed).

A local lecture is registered under a synthetic numeric id so every existing
phase keeps working on `workspace/data/01_raw/<id>.mp4` plus a `queue.json`
row. Its academic metadata (discipline, section, official date) lives in a
gitignored YAML file, merged into the portal catalog and schedule in memory
only at the study-guide compile boundary.

Publication gate: a local lecture is compiled into a private root that the
deploy never syncs, unless its row explicitly carries `publish: true`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from lecture_study.domain.models import SafeIdentifier
from lecture_study.ops.catalog_bridge import QUEUE_PATH, upsert_lecture_in_queue
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.strict_yaml import duplicated_key_paths
from lecture_study.study_materials.academic_catalog import (
    AcademicCatalog,
    AcademicDiscipline,
    AcademicLesson,
    AcademicScheduleEntry,
    slugify_path_segment,
)

LOCAL_LECTURES_PATH = Path("config/local-lectures.local.yaml")
RAW_VIDEO_DIR = Path("workspace/data/01_raw")
PRIVATE_STUDY_GUIDES_ROOT = Path("workspace/study-guides-local")
PUBLIC_STUDY_GUIDES_ROOT = Path("workspace/study-guides")
LOCAL_PROGRAM = "VIDEOAULAS LOCAIS"
DEFAULT_SECTION = "Videoaulas locais"
RESERVED_ID_RANGES = (range(925100, 925200), range(925200, 925300))
VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm"})
# Contains "/mod/supervideo/" so AcademicLesson.is_video accepts it.
LOCAL_URL_PREFIX = "local://"
_LOCAL_URL_TEMPLATE = LOCAL_URL_PREFIX + "mod/supervideo/{lecture_id}"
_DIGITS_ONLY = re.compile(r"[0-9]+")
_SEMESTER = re.compile(r"(?P<year>\d{4})\.(?P<half>[12])")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_DISCOVERY_TIMESTAMP = "local"

VideoProbe = Callable[[Path], None]
LectureKind = Literal["study", "video-only"]
VIDEO_ONLY_KIND: LectureKind = "video-only"


def is_reserved_local_id(value: str) -> bool:
    """True only for canonical digits (str(int(v)) == v) inside a reserved range."""

    if not _DIGITS_ONLY.fullmatch(value) or value != str(int(value)):
        return False
    return any(int(value) in id_range for id_range in RESERVED_ID_RANGES)


class LocalLectureError(ValueError):
    """Registration or local-lecture metadata is invalid or inconsistent."""


class LocalLecture(BaseModel):
    """One locally registered lecture and its academic placement."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    lecture_id: SafeIdentifier
    title: str
    discipline: str
    section: str
    scheduled_date: date
    publish: bool = False
    remote_llm_allowed: bool = False
    kind: LectureKind = "study"

    @property
    def is_video_only(self) -> bool:
        return self.kind == VIDEO_ONLY_KIND

    @field_validator("lecture_id")
    @classmethod
    def require_reserved_numeric_id(cls, value: str) -> str:
        if not is_reserved_local_id(value):
            raise ValueError(
                "id deve ser numérico canônico (sem zeros à esquerda) nas faixas "
                f"reservadas 925100-925199 e 925200-925299: {value!r}"
            )
        return value

    @field_validator("title", "discipline", "section")
    @classmethod
    def require_pathable_text(cls, value: str) -> str:
        if _CONTROL_CHARACTERS.search(value):
            raise ValueError("texto não pode conter quebras de linha ou controle")
        collapsed = " ".join(value.split())
        slugify_path_segment(collapsed)
        return collapsed


_LOCAL_LECTURES_ADAPTER = TypeAdapter(list[LocalLecture])


def default_scheduled_date(semester: str) -> date:
    """First day of the civil semester `YYYY.S` (used when no date is given)."""

    match = _SEMESTER.fullmatch(semester)
    if match is None:
        raise ValueError(f"semestre deve ter o formato AAAA.S (1 ou 2): {semester!r}")
    return date(int(match["year"]), 1 if match["half"] == "1" else 7, 1)


def load_local_lectures(path: Path = LOCAL_LECTURES_PATH) -> list[LocalLecture]:
    """Return the registered local lectures; empty when the file is absent.

    A registry that cannot be read, repeats a mapping key, or lists an id twice
    raises ``LocalLectureError``: its egress and publication flags would
    otherwise resolve to whichever contradictory value was parsed last.
    """

    try:
        return _parse_registry(path)
    except (yaml.YAMLError, ValidationError, UnicodeDecodeError) as error:
        raise LocalLectureError(f"aulas locais inválidas em {path}: {error}") from error
    except LocalLectureError as error:
        raise LocalLectureError(f"aulas locais inválidas em {path}: {error}") from error


def _parse_registry(path: Path) -> list[LocalLecture]:
    text = _read_registry_text(path)
    if text is None:
        return []
    payload = _parse_registry_yaml(text) or {}
    if not isinstance(payload, dict):
        raise LocalLectureError("raiz do arquivo deve ser um mapa")
    lectures = _LOCAL_LECTURES_ADAPTER.validate_python(
        payload.get("lectures", []), strict=False
    )
    _require_unique_lecture_ids(lectures)
    return lectures


def _read_registry_text(path: Path) -> str | None:
    """The registry text, or None only when the file is absent."""

    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LocalLectureError(f"arquivo ilegível ({type(error).__name__})") from error


def _parse_registry_yaml(text: str) -> object:
    """Parse the registry text, refusing repeated keys and unparseable scalars.

    ``ValueError`` (a calendar-invalid timestamp such as ``2001-13-45``),
    ``RecursionError`` (deep nesting), ``KeyError`` and ``AttributeError``
    (explicit invalid tags such as ``!!bool .nan`` and ``!!timestamp 0x``) are
    mapped to a typed error that names only the exception type, never the
    document content.
    """

    try:
        repeated = duplicated_key_paths(text)
        payload = yaml.safe_load(text)
    except (ValueError, RecursionError, KeyError, AttributeError) as error:
        raise LocalLectureError(
            f"conteúdo YAML não interpretável ({type(error).__name__})"
        ) from None
    if repeated:
        raise LocalLectureError("chaves repetidas: " + ", ".join(repeated))
    return payload


def _require_unique_lecture_ids(lectures: list[LocalLecture]) -> None:
    """Reject a registry that lists one id twice: its clearance flags could disagree."""

    counts = Counter(item.lecture_id for item in lectures)
    duplicated = sorted(lecture_id for lecture_id, n in counts.items() if n > 1)
    if duplicated:
        raise LocalLectureError(
            "ids de aula local repetidos no arquivo: " + ", ".join(duplicated)
        )


def unpublished_lecture_ids(lectures: list[LocalLecture]) -> frozenset[str]:
    """Ids of local lectures that must not reach the public study-guides tree."""

    return frozenset(item.lecture_id for item in lectures if not item.publish)


def video_only_lectures(lectures: list[LocalLecture]) -> dict[str, LocalLecture]:
    """Video-only lectures by id: compiled as a bare video bundle (ADR 0043)."""

    return {item.lecture_id: item for item in lectures if item.is_video_only}


def select_output_root(
    lecture_id: str,
    unpublished_ids: frozenset[str],
    public_root: Path,
    private_root: Path,
) -> Path:
    """Public root unless the lecture is an unpublished local one (fail closed)."""

    if lecture_id not in unpublished_ids:
        return public_root
    if _is_same_or_inside(private_root, public_root):
        raise LocalLectureError(
            f"raiz privada {private_root} não pode ficar dentro da raiz pública "
            f"{public_root}"
        )
    return private_root


def _comparable_parts(path: Path) -> tuple[str, ...]:
    normalized = Path(os.path.normpath(path.absolute()))
    return tuple(part.casefold() for part in normalized.parts)


def _is_same_or_inside(candidate: Path, container: Path) -> bool:
    container_parts = _comparable_parts(container)
    return _comparable_parts(candidate)[: len(container_parts)] == container_parts


def withheld_lecture_ids(lectures: list[LocalLecture]) -> list[str]:
    """Reserved ids that must not be synced: every one not explicitly published."""

    published = {item.lecture_id for item in lectures if item.publish}
    return [
        str(lecture_id)
        for id_range in RESERVED_ID_RANGES
        for lecture_id in id_range
        if str(lecture_id) not in published
    ]


def withheld_exam_lecture_ids(lectures: list[LocalLecture]) -> list[str]:
    """Ids kept off the exam channel: the withheld ones plus every video-only
    lecture, which has no exam even when published."""

    video_only = video_only_lectures(lectures).keys()
    return sorted({*withheld_lecture_ids(lectures), *video_only})


def merge_local_lectures(
    catalog: AcademicCatalog,
    schedule: dict[str, AcademicScheduleEntry],
    lectures: list[LocalLecture],
) -> tuple[AcademicCatalog, dict[str, AcademicScheduleEntry]]:
    """Return the portal catalog and schedule extended with local lectures."""

    if not lectures:
        return catalog, schedule
    _require_no_portal_collision(catalog, schedule, lectures)
    merged_schedule = {
        **schedule,
        **{
            lecture.lecture_id: AcademicScheduleEntry(
                lecture_id=lecture.lecture_id,
                scheduled_date=lecture.scheduled_date,
                schedule_topic=lecture.title,
            )
            for lecture in lectures
        },
    }
    merged_catalog = AcademicCatalog(
        disciplines=[*catalog.disciplines, *_local_disciplines(lectures)]
    )
    return merged_catalog, merged_schedule


def register_local_lecture(
    lecture: LocalLecture,
    source_video: Path,
    *,
    portal_ids: frozenset[str],
    local_lectures_path: Path = LOCAL_LECTURES_PATH,
    raw_video_dir: Path = RAW_VIDEO_DIR,
    queue_path: Path = QUEUE_PATH,
    video_probe: VideoProbe | None = None,
    public_root: Path = PUBLIC_STUDY_GUIDES_ROOT,
) -> Path:
    """Register one local lecture transactionally and idempotently.

    Everything is validated before the first write. Writes happen under an
    exclusive file lock in the order link, metadata, queue; a failure rolls
    the earlier writes back. A video-only lecture is never enqueued, so no
    pipeline stage can pick it up. The source video is never modified. A rerun with
    the same id and source is a no-op; it may change only the publish and
    remote_llm_allowed flags, and demoting a lecture whose public bundle exists is
    refused. Returns the raw-video link path.
    """

    source = _require_source_video(source_video, video_probe or probe_video_stream)
    link_path = raw_video_dir / f"{lecture.lecture_id}.mp4"
    with _exclusive_lock(queue_path.with_suffix(".lock")):
        _require_registrable(
            lecture,
            source,
            link_path,
            portal_ids,
            local_lectures_path,
            queue_path,
            public_root,
        )
        _write_registration(lecture, source, link_path, local_lectures_path, queue_path)
    return link_path


def probe_video_stream(video_path: Path) -> None:
    """Raise unless ffprobe finds a video stream in the file."""

    environment = sanitized_child_process_environment()
    absolute_path = video_path.absolute()
    executable = resolve_trusted_executable(
        "ffprobe", search_path=environment.get("PATH")
    )
    try:
        result = _run_ffprobe(executable, absolute_path, environment)
    except subprocess.TimeoutExpired as error:
        raise LocalLectureError(f"ffprobe excedeu o tempo: {video_path}") from error
    if result.returncode != 0 or "video" not in result.stdout:
        raise LocalLectureError(f"arquivo sem fluxo de vídeo legível: {video_path}")


def _run_ffprobe(
    executable: str, absolute_path: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(absolute_path),
        ],
        capture_output=True,
        text=True,
        env=sanitized_child_process_environment(environment),
        timeout=60,
        check=False,
    )


def _require_source_video(source_video: Path, video_probe: VideoProbe) -> Path:
    if not source_video.is_file():
        raise FileNotFoundError(f"vídeo de origem não encontrado: {source_video}")
    if source_video.suffix.lower() not in VIDEO_EXTENSIONS:
        raise LocalLectureError(f"extensão de vídeo não suportada: {source_video}")
    video_probe(source_video)
    return source_video.resolve()


def _require_registrable(
    lecture: LocalLecture,
    source: Path,
    link_path: Path,
    portal_ids: frozenset[str],
    local_lectures_path: Path,
    queue_path: Path,
    public_root: Path,
) -> None:
    lecture_id = lecture.lecture_id
    if lecture_id in portal_ids:
        raise LocalLectureError(f"id {lecture_id} já em uso (catálogo do portal)")
    for existing in load_local_lectures(local_lectures_path):
        if existing.lecture_id != lecture_id:
            continue
        if _without_operator_flags(existing, lecture) != lecture:
            raise LocalLectureError(f"id {lecture_id} já em uso (aulas locais)")
        if existing.publish and not lecture.publish:
            _require_no_public_bundle(lecture_id, public_root)
    queue_row = _queue_rows(queue_path).get(lecture_id)
    if queue_row is not None and not str(queue_row.get("url", "")).startswith(
        LOCAL_URL_PREFIX
    ):
        raise LocalLectureError(f"id {lecture_id} já em uso (fila)")
    if _occupied(link_path) and not _links_to(link_path, source):
        raise LocalLectureError(f"vídeo bruto já existe com outra origem: {link_path}")


def _without_operator_flags(
    existing: LocalLecture, requested: LocalLecture
) -> LocalLecture:
    """`existing` carrying the requested flags: a rerun may change only these."""

    return existing.model_copy(
        update={
            "publish": requested.publish,
            "remote_llm_allowed": requested.remote_llm_allowed,
        }
    )


def _require_no_public_bundle(lecture_id: str, public_root: Path) -> None:
    if public_root.is_dir() and any(public_root.rglob(f"{lecture_id}-*")):
        raise LocalLectureError(
            f"aula {lecture_id} tem pacote público em {public_root}; "
            "remova-o antes de despublicar"
        )


def _write_registration(
    lecture: LocalLecture,
    source: Path,
    link_path: Path,
    local_lectures_path: Path,
    queue_path: Path,
) -> None:
    created_link = _ensure_link(link_path, source)
    previous_metadata = _read_optional_text(local_lectures_path)
    try:
        _upsert_local_lecture(lecture, local_lectures_path)
        if not lecture.is_video_only:
            _ensure_queue_row(lecture, queue_path)
    except BaseException:
        _restore_text(local_lectures_path, previous_metadata)
        if created_link:
            link_path.unlink(missing_ok=True)
        raise


def _ensure_link(link_path: Path, source: Path) -> bool:
    if _occupied(link_path):
        return False
    link_path.parent.mkdir(parents=True, exist_ok=True)
    link_path.symlink_to(source)
    return True


def _ensure_queue_row(lecture: LocalLecture, queue_path: Path) -> None:
    if lecture.lecture_id in _queue_rows(queue_path):
        return
    upsert_lecture_in_queue(
        {
            "activity_id": lecture.lecture_id,
            "display_title": lecture.title,
            "url": _LOCAL_URL_TEMPLATE.format(lecture_id=lecture.lecture_id),
        },
        lecture.discipline,
        queue_path=queue_path,
    )


def _upsert_local_lecture(lecture: LocalLecture, path: Path) -> None:
    existing = load_local_lectures(path)
    if lecture in existing:
        return
    kept = [item for item in existing if item.lecture_id != lecture.lecture_id]
    payload = {"lectures": [item.model_dump() for item in [*kept, lecture]]}
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    )


def _queue_rows(queue_path: Path) -> dict[str, dict[str, str]]:
    if not queue_path.is_file():
        return {}
    try:
        rows = json.loads(queue_path.read_text(encoding="utf-8"))
        return {str(row["id"]): row for row in rows}
    except (ValueError, KeyError, TypeError) as error:
        raise LocalLectureError(f"fila inválida em {queue_path}: {error}") from error


def _occupied(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _links_to(link_path: Path, source: Path) -> bool:
    return link_path.is_symlink() and link_path.resolve() == source


def _read_optional_text(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _restore_text(path: Path, previous: str | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write_text(path, previous)


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(text, encoding="utf-8")
    temporary_path.replace(path)


@contextmanager
def _exclusive_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _require_no_portal_collision(
    catalog: AcademicCatalog,
    schedule: dict[str, AcademicScheduleEntry],
    lectures: list[LocalLecture],
) -> None:
    portal_ids = {
        str(lesson.activity_id)
        for discipline in catalog.disciplines
        for lesson in discipline.lessons
    } | set(schedule)
    collisions = sorted(
        item.lecture_id for item in lectures if item.lecture_id in portal_ids
    )
    if collisions:
        raise LocalLectureError(
            "id de aula local colide com o catálogo/cronograma do portal: "
            + ", ".join(collisions)
        )


def _local_disciplines(lectures: list[LocalLecture]) -> list[AcademicDiscipline]:
    by_discipline: dict[str, list[LocalLecture]] = defaultdict(list)
    for lecture in lectures:
        by_discipline[lecture.discipline].append(lecture)
    return [
        AcademicDiscipline(
            title=title,
            url=f"{LOCAL_URL_PREFIX}course/{_DISCOVERY_TIMESTAMP}",
            program=LOCAL_PROGRAM,
            lessons=[
                _academic_lesson(lecture, order)
                for order, lecture in enumerate(group, start=1)
            ],
        )
        for title, group in by_discipline.items()
    ]


def _academic_lesson(lecture: LocalLecture, order: int) -> AcademicLesson:
    return AcademicLesson(
        activity_id=lecture.lecture_id,
        moodle_order=order,
        section=lecture.section,
        display_title=lecture.title,
        url=_LOCAL_URL_TEMPLATE.format(lecture_id=lecture.lecture_id),
        enabled=True,
        discovery_timestamp=_DISCOVERY_TIMESTAMP,
    )
