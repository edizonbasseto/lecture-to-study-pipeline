"""Generate the static study site from the published catalog (SITE-01).

Every run replaces the site directory's HTML, stylesheet and text/PDF
materials, then hard links them in from `workspace/study-guides/` -- never
copies -- so republishing after new lectures finish costs a directory walk
and some inode entries, not gigabytes of I/O, and stays idempotent: the
same catalog always produces the same tree. Hard linking requires the
output directories to sit on the same filesystem as the study guides.

Video is written to a separate directory, `VIDEO_LIBRARY_DIR`, never into
`SITE_OUTPUT_DIR` (ADR 0029): the site directory is what an OCI image
build would copy in, and it must stay small and rebuildable, while the
video library is meant to live on a volume mounted at run time,
independent of the image.
"""

import os
import re
import shutil
from html import escape
from pathlib import Path

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from lecture_study.site.catalog import (
    STUDY_GUIDES_ROOT,
    Discipline,
    Lecture,
    Material,
    Semester,
    SiteCatalog,
    Topic,
    Year,
    pending_lectures,
    previous_and_next,
    read_catalog,
    recent_lectures,
    search_index,
)
from lecture_study.site.quiz import MOCK_EXAMS_DIR
from lecture_study.site.renderer import (
    STYLESHEET,
    BreadcrumbSegment,
    LectureNavigation,
    TocEntry,
    anchor_id,
    render_home,
    render_lecture,
    render_reading_page,
    render_watch_page,
)

SITE_OUTPUT_DIR = Path("workspace/site")
VIDEO_LIBRARY_DIR = Path("workspace/site-videos")
ACADEMIC_SCHEDULE_PATH = Path(
    os.environ.get(
        "LECTURE_STUDY_SITE_ACADEMIC_SCHEDULE",
        "config/academic-calendar.local.yaml",
    )
)

# Renders every material's ".md" source into its own reading page, at
# generation time (never client-side).
# html=False: the commonmark preset otherwise passes raw HTML through
# unescaped. Source markdown is pipeline-drafted from ASR transcripts, not
# hand-authored by the two trusted site users, so it must be treated as
# untrusted content, not embedded verbatim into the page (security review
# finding, 2026-09-15).
_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"html": False})


def _scheduled_lecture_ids(schedule_path: Path) -> frozenset[str]:
    """Ids the official academic schedule confirms a date for.

    Reads the schedule's `entries[].lecture_id` list directly rather than
    importing `study_materials.academic_catalog`'s full validated
    `AcademicScheduleEntry` shape: the site only ever needs this one set
    of ids, and staying off that module keeps the site package's own
    narrow dependency footprint (Dockerfile.site's docstring) intact.
    Empty when the file is not mounted, matching how
    `infra/docker-entrypoint.sh` already treats a missing
    `targets.local.yaml` -- an optional input's absence degrades the home
    page's pending sections, it never fails the whole build.
    """
    if not schedule_path.is_file():
        return frozenset()
    payload = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return frozenset(
        str(entry["lecture_id"])
        for entry in entries
        if isinstance(entry, dict) and "lecture_id" in entry
    )


def generate_site(
    expected_lecture_ids: tuple[str, ...] = (),
    study_guides_root: Path = STUDY_GUIDES_ROOT,
    output_dir: Path = SITE_OUTPUT_DIR,
    video_library_dir: Path = VIDEO_LIBRARY_DIR,
    mock_exams_dir: Path = MOCK_EXAMS_DIR,
    academic_schedule_path: Path = ACADEMIC_SCHEDULE_PATH,
) -> SiteCatalog:
    """Render the whole site into output_dir, and every lecture's video
    into video_library_dir, kept apart per ADR 0029; return the catalog
    used."""
    catalog = read_catalog(study_guides_root)
    _reset_directory(output_dir)
    _reset_directory(video_library_dir)
    (output_dir / "estilo.css").write_text(STYLESHEET, encoding="utf-8")
    unpublished_ids = pending_lectures(catalog, expected_lecture_ids)
    scheduled_ids = _scheduled_lecture_ids(academic_schedule_path)
    pending = tuple(
        lecture_id for lecture_id in unpublished_ids if lecture_id in scheduled_ids
    )
    unscheduled_pending = tuple(
        lecture_id for lecture_id in unpublished_ids if lecture_id not in scheduled_ids
    )
    recent = recent_lectures(catalog)
    entries = search_index(catalog)
    (output_dir / "index.html").write_text(
        render_home(
            catalog,
            pending=pending,
            unscheduled_pending=unscheduled_pending,
            recent=recent,
            search_entries=entries,
        ),
        encoding="utf-8",
    )
    for year in catalog.years:
        for semester in year.semesters:
            for discipline in semester.disciplines:
                for topic in discipline.topics:
                    for lecture in topic.lectures:
                        _write_lecture(
                            lecture,
                            breadcrumb=_breadcrumb(year, semester, discipline, topic),
                            navigation=previous_and_next(topic, lecture.lecture_id),
                            source_root=study_guides_root,
                            output_dir=output_dir,
                            video_library_dir=video_library_dir,
                            mock_exams_dir=mock_exams_dir,
                        )
    return catalog


def _breadcrumb(
    year: Year, semester: Semester, discipline: Discipline, topic: Topic
) -> tuple[BreadcrumbSegment, ...]:
    """The lecture page's breadcrumb trail, each segment linking back to
    that level's own anchor on the home page (brief §4)."""
    semester_anchor = anchor_id(year.slug, semester.slug)
    discipline_anchor = anchor_id(year.slug, semester.slug, discipline.slug)
    topic_anchor = anchor_id(year.slug, semester.slug, discipline.slug, topic.slug)
    return (
        BreadcrumbSegment(year.title, semester_anchor),
        BreadcrumbSegment(semester.title, semester_anchor),
        BreadcrumbSegment(discipline.title, discipline_anchor),
        BreadcrumbSegment(topic.title, topic_anchor),
    )


def _reset_directory(directory: Path) -> None:
    """Empty directory's contents, keeping the directory itself.

    output_dir and video_library_dir are typically mount points in the
    served container (a Docker volume, a host volume); removing and
    recreating the directory itself fails there with "Device or resource
    busy", so only its contents are cleared.
    """
    if directory.is_dir():
        for child in directory.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        directory.mkdir(parents=True)


def _write_lecture(
    lecture: Lecture,
    breadcrumb: tuple[BreadcrumbSegment, ...],
    navigation: tuple[Lecture | None, Lecture | None],
    source_root: Path,
    output_dir: Path,
    video_library_dir: Path,
    mock_exams_dir: Path = MOCK_EXAMS_DIR,
) -> None:
    lecture_output_dir = output_dir / lecture.relative_path
    lecture_output_dir.mkdir(parents=True, exist_ok=True)
    previous_lecture, next_lecture = navigation
    lecture_navigation = LectureNavigation(breadcrumb, previous_lecture, next_lecture)
    reading_pages = _write_reading_pages(lecture, source_root, lecture_output_dir)
    has_interactive_quiz = (mock_exams_dir / lecture.lecture_id / "exam.json").is_file()
    (lecture_output_dir / "index.html").write_text(
        render_lecture(
            lecture, lecture_navigation, reading_pages, has_interactive_quiz
        ),
        encoding="utf-8",
    )
    _write_watch_page(lecture, lecture_output_dir)
    for material in lecture.materials:
        source_path = source_root / lecture.relative_path / material.relative_path
        if material.kind == "video":
            destination_path = (
                video_library_dir / lecture.relative_path / material.relative_path
            )
        else:
            destination_path = lecture_output_dir / material.relative_path
        _link_or_copy(source_path, destination_path)


def _write_watch_page(lecture: Lecture, lecture_output_dir: Path) -> None:
    """Write the lecture's dedicated video page, `assistir.html`, next to
    its `index.html` -- only for a lecture that actually has a video, so a
    still-processing lecture never gets a fullscreen link to a page with
    nothing to play."""
    if lecture.video is None:
        return
    (lecture_output_dir / "assistir.html").write_text(
        render_watch_page(lecture), encoding="utf-8"
    )


# Whole-section reveal (brief-derived decision): rather than pair each
# question or flashcard with its answer by number -- fragile if the
# pipeline's numbering ever drifts, and wrong pairing on clinical content
# is the one failure this must never risk -- the entire named section is
# collapsed behind one disclosure. Keyed by the material's own filename
# stem, not by card kind, so this stays independent of catalog.py.
_COLLAPSED_SECTION_BY_STEM = {
    "simulado": "Gabarito comentado",
    "flashcards": "Respostas",
}


def _write_reading_pages(
    lecture: Lecture, source_root: Path, lecture_output_dir: Path
) -> dict[str, str]:
    """Render every ".md" material to its own reading page next to the
    source material (same directory, so the figures a preview references
    by relative path resolve without rewriting). Returns
    {material.relative_path: page_href} for the lecture page's cards to
    link into.

    Ordered by `lecture.material_cards` -- the same pedagogical order the
    lecture page's own cards render in (apostila, resumo, simulado,
    flashcards, ...) -- rather than by `lecture.materials`, whose order is
    a sort key the catalog never defined for `.md` filenames and which
    would otherwise leave "next material" navigation alphabetical.
    """
    lecture_source_dir = source_root / lecture.relative_path
    markdown_materials = [
        material
        for card in lecture.material_cards
        for material in card.formats
        if material.relative_path.endswith(".md")
    ]
    reading_pages: dict[str, str] = {}
    for index, material in enumerate(markdown_materials):
        stem = material.relative_path.rsplit(".", 1)[0]
        source_text = (lecture_source_dir / material.relative_path).read_text(
            encoding="utf-8"
        )
        html, toc = _render_reading_html(
            source_text, _COLLAPSED_SECTION_BY_STEM.get(stem)
        )
        pdf_href = _sibling_pdf_href(material, lecture.materials)
        previous_material = markdown_materials[index - 1] if index > 0 else None
        next_material = (
            markdown_materials[index + 1]
            if index + 1 < len(markdown_materials)
            else None
        )
        page_html = render_reading_page(
            lecture=lecture,
            material=material,
            body_html=html,
            toc=toc,
            pdf_href=pdf_href,
            previous_material=previous_material,
            next_material=next_material,
        )
        href = f"{stem}.html"
        (lecture_output_dir / href).write_text(page_html, encoding="utf-8")
        reading_pages[material.relative_path] = href
    return reading_pages


def _sibling_pdf_href(
    material: Material, materials: tuple[Material, ...]
) -> str | None:
    stem = material.relative_path.rsplit(".", 1)[0]
    for candidate in materials:
        if candidate.relative_path == f"{stem}.pdf":
            return candidate.relative_path
    return None


_TOC_HEADING_TAGS = frozenset({"h2", "h3"})


class SectionCollapseError(RuntimeError):
    """Raised when a material's named section (the answer key, the
    flashcard deck's answers) cannot be found exactly once by its heading
    text.

    Deliberately a hard failure, not a silent fallback: a pipeline
    generating this markdown from an ASR transcript can plausibly emit the
    heading twice, or not at all, and both are exactly the conditions
    under which guessing wrong would show an unreviewed answer in the
    open instead of behind its disclosure. Stopping the build is cheaper
    than that.
    """


def _render_reading_html(
    source_text: str, collapse_heading: str | None
) -> tuple[str, tuple[TocEntry, ...]]:
    """Render one material's markdown source to HTML, assigning each h2/h3
    heading a stable anchor id, and -- when collapse_heading names one --
    folding that h2 section into a single `<details>` disclosure.

    The section is located by walking the parsed token stream and
    comparing each heading's own resolved text, never by searching the
    rendered HTML string for that text: a stray paragraph elsewhere in the
    document that happens to quote the section's name (plausible LLM
    output, and this source is pipeline-generated, not hand-authored)
    could otherwise be mistaken for the heading itself, collapsing the
    wrong span in content where a wrong answer must never be shown.
    """
    tokens = _MARKDOWN_RENDERER.parse(source_text)
    headings = _assign_heading_ids(tokens)
    collapse_start, collapse_end = _collapsed_span(tokens, collapse_heading)
    toc = tuple(
        TocEntry(level=int(token.tag[1:]), text=text, anchor=str(token.attrGet("id")))
        for index, token, text in headings
        if not (collapse_start is not None and collapse_start <= index < collapse_end)
    )
    if collapse_start is None or collapse_heading is None:
        html = _MARKDOWN_RENDERER.renderer.render(
            tokens, _MARKDOWN_RENDERER.options, {}
        )
        return html, toc
    html = _render_with_collapsed_span(
        tokens, collapse_start, collapse_end, collapse_heading
    )
    return html, toc


def _assign_heading_ids(tokens: list[Token]) -> list[tuple[int, Token, str]]:
    """Give every h2/h3 heading a stable anchor id, returning each one's
    token index, its heading_open token, and its plain reading text --
    what both the table of contents and the section-collapse search below
    are built from."""
    seen_slugs: set[str] = set()
    headings: list[tuple[int, Token, str]] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag not in _TOC_HEADING_TAGS:
            continue
        text = _heading_text(tokens[index + 1])
        token.attrSet("id", _unique_slug(text, seen_slugs))
        headings.append((index, token, text))
    return headings


def _collapsed_span(
    tokens: list[Token], collapse_heading: str | None
) -> tuple[int | None, int]:
    """The [start, end) token range of the h2 section named
    collapse_heading -- matched against each heading's own resolved text,
    never against the rendered HTML, so a stray paragraph elsewhere that
    happens to quote the section's name cannot be mistaken for it. Returns
    (None, 0) when there is nothing configured to collapse.

    Raises SectionCollapseError, rather than falling back to leaving the
    section exposed, when the heading is missing, duplicated, or found at
    the wrong level. The count is taken across every heading in the
    document, at any level (h1 through h6) -- not just h2/h3, the levels
    the table of contents tracks. Restricting the count to a level
    allowlist was tried twice (h2-only, then h2+h3) and both times a
    duplicate that reappeared one level outside the allowlist slipped
    through uncounted; scanning every heading closes the whole class
    instead of the one level caught most recently.
    """
    if collapse_heading is None:
        return None, 0
    matches = [
        (index, token)
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
        and _heading_text(tokens[index + 1]) == collapse_heading
    ]
    if len(matches) != 1 or matches[0][1].tag != "h2":
        raise SectionCollapseError(
            f'esperava exatamente uma seção h2 "{collapse_heading}", encontrei '
            f"{len(matches)}"
        )
    start = matches[0][0]
    end = next(
        (
            index
            for index, token in enumerate(tokens)
            if index > start and token.type == "heading_open" and token.tag == "h2"
        ),
        len(tokens),
    )
    return start, end


def _heading_text(inline_token: Token) -> str:
    """The heading's plain reading text, ignoring markdown emphasis: the
    section-matching comparison above must recognize "Gabarito comentado"
    whether or not the pipeline emphasized it as "**Gabarito comentado**"."""
    children = inline_token.children or []
    return "".join(
        child.content for child in children if child.type in ("text", "code_inline")
    )


def _unique_slug(text: str, seen: set[str]) -> str:
    base = re.sub(r"[^\w]+", "-", text.strip().lower()).strip("-") or "secao"
    slug = base
    suffix = 2
    while slug in seen:
        slug = f"{base}-{suffix}"
        suffix += 1
    seen.add(slug)
    return slug


def _render_with_collapsed_span(
    tokens: list[Token], collapse_start: int, collapse_end: int, heading_text: str
) -> str:
    """Render three token slices in order -- everything before the
    section, the section's own body wrapped in a disclosure, everything
    after -- so the collapsed span is a structural slice of the parsed
    document, never a string search over its rendered output."""
    options = _MARKDOWN_RENDERER.options
    renderer = _MARKDOWN_RENDERER.renderer
    before_html: str = renderer.render(tokens[:collapse_start], options, {})
    # +3 skips this section's own heading_open/inline/heading_close triad.
    body_html: str = renderer.render(
        tokens[collapse_start + 3 : collapse_end], options, {}
    )
    after_html: str = renderer.render(tokens[collapse_end:], options, {})
    disclosure = (
        f'<details class="reveal"><summary>{escape(heading_text)}</summary>'
        f"{body_html}</details>"
    )
    return before_html + disclosure + after_html


def _link_or_copy(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination_path.hardlink_to(source_path)
    except OSError:
        shutil.copy2(source_path, destination_path)
