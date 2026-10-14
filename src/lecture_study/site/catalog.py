"""Read the published study-guide tree into a navigable catalog.

The tree under `workspace/study-guides/` is the single source of truth for
the site (SITE-01): every page is derived from files that the pipeline
already produced and validated, and nothing here writes, invents or
rephrases lecture content. A lecture that has no bundle yet is not
silently absent -- `pending_lectures` reports it, so the site can list it
as still being processed instead of pretending the catalog is complete
(SITE-06).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

STUDY_GUIDES_ROOT = Path("workspace/study-guides")

_LECTURE_DIRECTORY_PATTERN = re.compile(r"^(?P<id>[A-Za-z0-9]+)-(?P<slug>.+)$")
_VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mov"})
_AUDIO_SUFFIXES = frozenset({".m4a", ".mp3", ".wav"})
_IMAGE_SUFFIXES = frozenset({".png"})
_IGNORED_NAMES = frozenset({".DS_Store", ".locks", "indice.md"})

# Only these are student-facing materials. ".typ" is the pipeline's own
# Typst source (compiled into the .pdf sitting right beside it) and
# ".bundle.json" is internal delivery bookkeeping -- neither is something
# a student asked for, and both must never reach the site. ".png" is the
# figure format the pipeline's slide-frame extraction emits; figures are
# embedded assets referenced by the markdown previews (figuras/*.png),
# published so those references resolve, not standalone materials.
_PUBLISHABLE_SUFFIXES = frozenset(
    {".pdf", ".md", ".csv", ".png", ".mp4", ".m4v", ".mov", ".m4a", ".mp3", ".wav"}
)

# One label per published file name, so the site names a material the way a
# student would ask for it rather than by its file name. A file with no
# label here is still listed, under its own name -- the catalog never hides
# a material it does not recognize.
_MATERIAL_LABELS: dict[str, str] = {
    "apostila_completa.pdf": "Apostila completa (PDF)",
    "apostila_completa.md": "Apostila completa (texto)",
    "apostila_resumida.pdf": "Apostila resumida (PDF)",
    "apostila_resumida.md": "Apostila resumida (texto)",
    "resumo_critico.pdf": "Resumo crítico (PDF)",
    "resumo_critico.md": "Resumo crítico (texto)",
    "flashcards.pdf": "Flashcards para impressão (PDF)",
    "flashcards.md": "Flashcards para leitura (texto)",
    "flashcards.csv": "Flashcards para Anki (CSV)",
    "simulado.pdf": "Simulado (PDF)",
    "simulado.md": "Simulado (texto)",
    "roteiro_revisao.pdf": "Roteiro de revisão (PDF)",
    "roteiro_revisao.md": "Roteiro de revisão (texto)",
    "mapa_temporal.pdf": "Mapa temporal (PDF)",
    "mapa_temporal.md": "Mapa temporal (texto)",
    "mapa_temporal.csv": "Mapa temporal (CSV)",
    "podcast_revisao.m4a": "Podcast de revisão (áudio)",
    "podcast_roteiro.pdf": "Roteiro do podcast (PDF)",
    "podcast_roteiro.md": "Roteiro do podcast (texto)",
}

_MATERIAL_ORDER: tuple[str, ...] = (
    "apostila_completa.pdf",
    "apostila_resumida.pdf",
    "resumo_critico.pdf",
    "simulado.pdf",
    "flashcards.pdf",
    "roteiro_revisao.pdf",
    "mapa_temporal.pdf",
    "podcast_revisao.m4a",
    "podcast_roteiro.pdf",
)

# Maps a material's file stem to the study-site card it belongs to (brief
# §2.1). "podcast_revisao" (the audio) and "podcast_roteiro" (its
# transcript) fold into one "podcast" card -- two related materials, one
# card, per the brief's own framing.
_CARD_KIND_BY_STEM: dict[str, str] = {
    "apostila_completa": "apostila_completa",
    "apostila_resumida": "apostila_resumida",
    "resumo_critico": "resumo_critico",
    "simulado": "simulado",
    "roteiro_revisao": "roteiro_revisao",
    "mapa_temporal": "mapa_temporal",
    "flashcards": "flashcards",
    "podcast_revisao": "podcast",
    "podcast_roteiro": "podcast",
}

# Display order for the 8 material cards on a lecture page (brief §2.2,
# items 2-9; the video is item 1 and stays its own top-level section).
_CARD_KIND_ORDER: tuple[str, ...] = (
    "apostila_completa",
    "apostila_resumida",
    "resumo_critico",
    "simulado",
    "roteiro_revisao",
    "mapa_temporal",
    "flashcards",
    "podcast",
)

_CARD_KIND_LABELS: dict[str, str] = {
    "apostila_completa": "Apostila completa",
    "apostila_resumida": "Apostila resumida",
    "resumo_critico": "Resumo crítico",
    "simulado": "Simulado",
    "roteiro_revisao": "Roteiro de revisão",
    "mapa_temporal": "Mapa temporal",
    "flashcards": "Flashcards",
    "podcast": "Podcast de revisão",
}

# Within a card, formats show in the order a student would reach for them:
# listen/preview first, the machine-import CSV last (brief §2.1, §2.2).
_FORMAT_SUFFIX_ORDER: tuple[str, ...] = (".m4a", ".pdf", ".md", ".csv")


@dataclass(frozen=True, slots=True)
class Material:
    """One downloadable file inside a lecture's bundle."""

    label: str
    relative_path: str
    byte_size: int
    kind: str


@dataclass(frozen=True, slots=True)
class MaterialCard:
    """One study-site card: a content type and every format it comes in.

    Groups the flat `Lecture.materials` tuple into the card layout the
    brief specifies (§2.1) -- format twins (PDF/MD) and same-purpose,
    different-tool files (PDF/MD/CSV, or the podcast audio and its
    transcript) collapse into one card instead of one row each.
    """

    kind: str
    label: str
    formats: tuple[Material, ...]


@dataclass(frozen=True, slots=True)
class Lecture:
    """One published lecture and every material it carries."""

    lecture_id: str
    title: str
    slug: str
    relative_path: str
    materials: tuple[Material, ...]
    updated_at: datetime

    @property
    def video(self) -> Material | None:
        return next(
            (material for material in self.materials if material.kind == "video"), None
        )

    @property
    def has_study_guide(self) -> bool:
        return any(
            material.relative_path.endswith("apostila_completa.pdf")
            or material.relative_path.endswith("apostila_completa.md")
            for material in self.materials
        )

    @property
    def material_cards(self) -> tuple[MaterialCard, ...]:
        return _group_into_cards(self.materials)


@dataclass(frozen=True, slots=True)
class Topic:
    """One topic inside a discipline, as the academic catalog groups it."""

    title: str
    slug: str
    lectures: tuple[Lecture, ...]


@dataclass(frozen=True, slots=True)
class Discipline:
    """One discipline of one semester."""

    title: str
    slug: str
    topics: tuple[Topic, ...]


@dataclass(frozen=True, slots=True)
class Semester:
    """One semester of one academic year."""

    title: str
    slug: str
    disciplines: tuple[Discipline, ...]


@dataclass(frozen=True, slots=True)
class Year:
    """One academic year."""

    title: str
    slug: str
    semesters: tuple[Semester, ...]


@dataclass(frozen=True, slots=True)
class SiteCatalog:
    """The whole published tree, in the order the site presents it."""

    years: tuple[Year, ...]

    @property
    def lectures(self) -> tuple[Lecture, ...]:
        return tuple(
            lecture
            for year in self.years
            for semester in year.semesters
            for discipline in semester.disciplines
            for topic in discipline.topics
            for lecture in topic.lectures
        )


def read_catalog(root: Path = STUDY_GUIDES_ROOT) -> SiteCatalog:
    """Read the published tree. An absent root yields an empty catalog."""
    if not root.is_dir():
        return SiteCatalog(years=())
    years = tuple(
        Year(
            title=_year_title(year_directory.name),
            slug=year_directory.name,
            semesters=_read_semesters(year_directory, root),
        )
        for year_directory in _sorted_directories(root)
    )
    return SiteCatalog(years=tuple(year for year in years if year.semesters))


def recent_lectures(catalog: SiteCatalog, limit: int = 10) -> tuple[Lecture, ...]:
    """The most recently published/changed lectures, newest first.

    Powers the home page's "Recentes" zone (brief §1.2); sourced from
    `Lecture.updated_at`, itself the lecture bundle's own filesystem mtime
    -- no separate state store.
    """
    return tuple(
        sorted(catalog.lectures, key=lambda lecture: lecture.updated_at, reverse=True)
    )[:limit]


def previous_and_next(
    topic: Topic, lecture_id: str
) -> tuple[Lecture | None, Lecture | None]:
    """The lecture before and after `lecture_id` within `topic`'s order.

    Powers the lecture page's "Aula anterior" / "Próxima aula" footer nav
    (brief §4); `Topic.lectures` already carries the course's own order, so
    no new sorting logic is needed here.
    """
    for index, lecture in enumerate(topic.lectures):
        if lecture.lecture_id == lecture_id:
            previous = topic.lectures[index - 1] if index > 0 else None
            next_index = index + 1
            following = (
                topic.lectures[next_index] if next_index < len(topic.lectures) else None
            )
            return previous, following
    return None, None


def search_index(catalog: SiteCatalog) -> list[dict[str, str]]:
    """One JSON-serializable dict per lecture, for client-side search.

    Meant to be embedded as a `<script type="application/json">` block by
    the renderer (brief §1.1, §1.2) and filtered in the browser -- kept a
    pure function returning plain dicts, not a dataclass, since this is a
    serialization boundary, not a domain shape.
    """
    return [
        {
            "id": lecture.lecture_id,
            "title": lecture.title,
            "discipline": discipline.title,
            "topic": topic.title,
            "url": f"{lecture.relative_path}/index.html",
        }
        for year in catalog.years
        for semester in year.semesters
        for discipline in semester.disciplines
        for topic in discipline.topics
        for lecture in topic.lectures
    ]


# TODO(brief §7 item 2): extend each pending entry with its discipline/topic
# context once `expected_lecture_ids` is sourced from something that carries
# that context -- today's callers pass bare lecture ids with no upstream
# discipline/topic data to attach, so pending entries stay bare ids.
def pending_lectures(
    catalog: SiteCatalog, expected_lecture_ids: Iterable[str]
) -> tuple[str, ...]:
    """Lecture ids the course has but the site cannot publish yet."""
    published = {lecture.lecture_id for lecture in catalog.lectures}
    return tuple(
        sorted(
            lecture_id
            for lecture_id in expected_lecture_ids
            if lecture_id not in published
        )
    )


def _read_semesters(year_directory: Path, root: Path) -> tuple[Semester, ...]:
    semesters = tuple(
        Semester(
            title=_semester_title(directory.name),
            slug=directory.name,
            disciplines=_read_disciplines(directory, root),
        )
        for directory in _sorted_directories(year_directory)
    )
    return tuple(semester for semester in semesters if semester.disciplines)


def _read_disciplines(semester_directory: Path, root: Path) -> tuple[Discipline, ...]:
    disciplines = tuple(
        Discipline(
            title=_title_from_slug(directory.name),
            slug=directory.name,
            topics=_read_topics(directory, root),
        )
        for directory in _sorted_directories(semester_directory)
    )
    return tuple(discipline for discipline in disciplines if discipline.topics)


def _read_topics(discipline_directory: Path, root: Path) -> tuple[Topic, ...]:
    topics = tuple(
        Topic(
            title=_title_from_slug(directory.name),
            slug=directory.name,
            lectures=_read_lectures(directory, root),
        )
        for directory in _sorted_directories(discipline_directory)
    )
    return tuple(topic for topic in topics if topic.lectures)


def _read_lectures(topic_directory: Path, root: Path) -> tuple[Lecture, ...]:
    lectures = []
    for directory in _sorted_directories(topic_directory):
        match = _LECTURE_DIRECTORY_PATTERN.match(directory.name)
        if match is None:
            continue
        materials = _read_materials(directory)
        if not materials:
            continue
        lectures.append(
            Lecture(
                lecture_id=match.group("id"),
                title=_title_from_slug(match.group("slug")),
                slug=directory.name,
                relative_path=directory.relative_to(root).as_posix(),
                materials=materials,
                updated_at=_bundle_updated_at(directory),
            )
        )
    return tuple(lectures)


def _bundle_updated_at(lecture_directory: Path) -> datetime:
    """The most recent change to any file in the lecture's bundle."""
    mtimes = (
        path.stat().st_mtime for path in lecture_directory.rglob("*") if path.is_file()
    )
    latest = max(mtimes, default=lecture_directory.stat().st_mtime)
    return datetime.fromtimestamp(latest, tz=UTC)


def _read_materials(lecture_directory: Path) -> tuple[Material, ...]:
    materials = [
        _material(path, lecture_directory)
        for path in sorted(lecture_directory.rglob("*"))
        if path.is_file()
        and path.name not in _IGNORED_NAMES
        and path.suffix.lower() in _PUBLISHABLE_SUFFIXES
        and not path.is_symlink()
    ]
    return tuple(sorted(materials, key=_material_sort_key))


def _material(path: Path, lecture_directory: Path) -> Material:
    name = path.name
    suffix = path.suffix.lower()
    if suffix in _VIDEO_SUFFIXES:
        kind = "video"
        label = "Vídeo da aula"
    elif suffix in _IMAGE_SUFFIXES:
        kind = "image"
        label = name
    elif suffix in _AUDIO_SUFFIXES:
        kind = "audio"
        label = _MATERIAL_LABELS.get(name, name)
    elif suffix == ".pdf":
        kind = "pdf"
        label = _MATERIAL_LABELS.get(name, name)
    else:
        kind = "texto"
        label = _MATERIAL_LABELS.get(name, name)
    return Material(
        label=label,
        relative_path=path.relative_to(lecture_directory).as_posix(),
        byte_size=path.stat().st_size,
        kind=kind,
    )


def _material_sort_key(material: Material) -> tuple[int, str]:
    name = material.relative_path.rsplit("/", 1)[-1]
    if material.kind == "video":
        return (0, name)
    if name in _MATERIAL_ORDER:
        return (1 + _MATERIAL_ORDER.index(name), name)
    return (len(_MATERIAL_ORDER) + 2, name)


def _group_into_cards(materials: tuple[Material, ...]) -> tuple[MaterialCard, ...]:
    """Group non-video materials into `MaterialCard`s, in display order.

    A material whose stem is not one of the known card kinds (brief §2.1)
    is left off the card layout -- it still exists on `Lecture.materials`
    for anything that lists every file, it just has no card of its own.
    """
    by_kind: dict[str, list[Material]] = {}
    for material in materials:
        kind = _card_kind(material)
        if kind is not None:
            by_kind.setdefault(kind, []).append(material)
    return tuple(
        MaterialCard(
            kind=kind,
            label=_CARD_KIND_LABELS[kind],
            formats=tuple(sorted(by_kind[kind], key=_format_sort_key)),
        )
        for kind in _CARD_KIND_ORDER
        if kind in by_kind
    )


def _card_kind(material: Material) -> str | None:
    if material.kind in ("video", "image"):
        # Videos live in the separate video library (ADR 0029) and images
        # are embedded assets of the markdown previews -- neither is a
        # card of its own.
        return None
    stem = material.relative_path.rsplit("/", 1)[-1].split(".", 1)[0]
    return _CARD_KIND_BY_STEM.get(stem)


def _format_sort_key(material: Material) -> int:
    suffix = "." + material.relative_path.rsplit(".", 1)[-1].lower()
    if suffix in _FORMAT_SUFFIX_ORDER:
        return _FORMAT_SUFFIX_ORDER.index(suffix)
    return len(_FORMAT_SUFFIX_ORDER)


def _sorted_directories(directory: Path) -> Sequence[Path]:
    return sorted(
        (
            child
            for child in directory.iterdir()
            if child.is_dir() and child.name not in _IGNORED_NAMES
        ),
        key=lambda child: child.name,
    )


def _year_title(slug: str) -> str:
    return slug


def _semester_title(slug: str) -> str:
    match = re.match(r"^(\d)-semestre$", slug)
    return f"{match.group(1)}º semestre" if match else _title_from_slug(slug)


def _title_from_slug(slug: str) -> str:
    words = slug.replace("_", "-").split("-")
    lowercase = {
        "de",
        "da",
        "do",
        "das",
        "dos",
        "e",
        "em",
        "na",
        "no",
        "para",
        "a",
        "o",
    }
    titled = [
        word if index and word in lowercase else word.capitalize()
        for index, word in enumerate(words)
    ]
    return " ".join(titled)
