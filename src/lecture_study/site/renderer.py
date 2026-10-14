"""Render the study site's pages from the published catalog.

Every page is a pure function of its inputs (SITE-01): no lecture content is
authored here beyond navigation labels and the standing notice that the
material is personal study, never clinically certified (SITE-06). The
interface is pt-BR throughout.

Markdown source text is not read from disk here -- `generator.py` (the
module that already reads and writes files for the site) renders each
material's `.md` source to HTML at generation time, one reading page per
material, and hands this module the result to embed, keeping this module
a function of data it is given rather than a second place that touches
the filesystem.

Boundary note (clean-architecture): this module stays a single file. Its
public surface (`render_home`, `render_lecture`, `render_reading_page`,
`render_watch_page`, `STYLESHEET`, the `anchor_id` helper and the
`BreadcrumbSegment`/`LectureNavigation`/`TocEntry` shapes) is small and
the internal helpers are grouped by the page they serve (home vs. lecture
vs. reading vs. shared card rendering), so a module split was not
warranted for this pass.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from types import MappingProxyType

from lecture_study.site.catalog import (
    Discipline,
    Lecture,
    Material,
    MaterialCard,
    Semester,
    SiteCatalog,
    Topic,
    Year,
)

UNCERTIFIED_NOTICE = (
    "Material de estudo gerado automaticamente a partir da aula, sem "
    "certificação nem revisão clínica humana. Confira sempre com a fonte "
    "original."
)


def uncertified_notice_html() -> str:
    """The standing uncertified-material notice (ADR 0045), shown above the
    content of every page that renders study material. Styled by the
    `uncertified-notice` class each page's own stylesheet defines, so no
    inline style is needed under the hash-based CSP."""
    return f'<p class="uncertified-notice" role="note">{escape(UNCERTIFIED_NOTICE)}</p>'


# A lecture bundle changed within this many days shows "Novo" instead of a
# dated "Atualizado em" badge. There is no per-viewer "already seen" state
# (brief §7, open question 1), so this is a rendering-layer approximation
# of "new" from the bundle's own mtime, not a true per-viewer read signal.
_NEW_BADGE_WINDOW_DAYS = 7

_FORMAT_WORDS = {".pdf": "PDF", ".md": "texto", ".csv": "CSV", ".m4a": "áudio"}


@dataclass(frozen=True, slots=True)
class BreadcrumbSegment:
    """One linked segment of a lecture page's breadcrumb (brief §4)."""

    label: str
    anchor: str


@dataclass(frozen=True, slots=True)
class LectureNavigation:
    """Where a lecture page sits in the catalog: its breadcrumb trail back
    to the home page, and its neighbors within the same topic (brief §4)."""

    breadcrumb: tuple[BreadcrumbSegment, ...]
    previous_lecture: Lecture | None
    next_lecture: Lecture | None


@dataclass(frozen=True, slots=True)
class TocEntry:
    """One heading in a reading page's table of contents."""

    level: int
    text: str
    anchor: str


def anchor_id(*slugs: str) -> str:
    """The home page element id a catalog level renders under.

    Shared by `render_home` (which emits the id) and by whoever builds a
    `BreadcrumbSegment` for `render_lecture` (which links to it), so the two
    never drift apart.
    """
    return "-".join(slugs)


def render_home(
    catalog: SiteCatalog,
    pending: Iterable[str] = (),
    unscheduled_pending: Iterable[str] = (),
    recent: Iterable[Lecture] = (),
    search_entries: Iterable[dict[str, str]] = (),
) -> str:
    """The index: what's new, what's pending, then the full catalog tree.

    `pending` and `unscheduled_pending` are deliberately two sections, not
    one (2026-09-18): a lecture with a confirmed date on the official
    schedule that just has not been published yet is a different claim
    from one the schedule never dated at all -- conflating them under one
    "ainda está sendo gerado" message was misleading for the latter,
    which may not run for a long time yet, if ever.
    """
    context_by_lecture_id = _lecture_contexts(catalog)
    body = (
        _home_header(catalog)
        + _render_search_box()
        + _render_recent_section(tuple(recent), context_by_lecture_id)
        + _render_pending_section(tuple(pending))
        + _render_unscheduled_section(tuple(unscheduled_pending))
        + _render_catalog_tree(catalog)
        + _render_search_index_script(list(search_entries))
    )
    return _document(None, body, depth=0)


def render_lecture(
    lecture: Lecture,
    navigation: LectureNavigation,
    reading_pages: Mapping[str, str] = MappingProxyType({}),
    has_interactive_quiz: bool = False,
) -> str:
    """One lecture page: video, then a card per content type (brief §2.2).

    depth is the lecture directory's distance from the site root, derived
    from its own relative_path -- never assumed -- so the stylesheet and
    every relative link (back link, next/previous, breadcrumb) resolve
    correctly regardless of how many catalog levels the academic structure
    ends up with.

    has_interactive_quiz tells the simulado card whether a compiled
    exam.json actually exists for this lecture (checked once, at
    generation time, against the same directory `site.quiz.read_exam`
    reads from at request time) -- the two must agree, or a published
    simulado.md with no matching exam.json would link to a quiz route
    that 404s for every student who clicks it.
    """
    depth = len(lecture.relative_path.split("/"))
    up = "../" * depth
    body = (
        _lecture_header(lecture, navigation.breadcrumb, up)
        + _render_back_link(navigation.breadcrumb, up)
        + _render_video_section(lecture)
        + _render_material_cards(lecture, reading_pages, has_interactive_quiz)
        + _render_lecture_footer_nav(navigation, up)
    )
    return _document(lecture.title, body, depth=depth)


def render_watch_page(lecture: Lecture) -> str:
    """The lecture's video on its own page: the same recording embedded on
    the lecture page (`_render_video_section`), full width and without the
    material cards competing for attention, for "Assistir em tela cheia"
    (locked decision, 2026-09-18). Sits next to the lecture's own
    `index.html`, so its video URL and depth-based stylesheet link resolve
    exactly as the lecture page's do. No captions or transcript track:
    out of scope for this delivery (see ADR 0040)."""
    video = lecture.video
    if video is None:
        raise ValueError(f"lecture {lecture.lecture_id!r} has no video to watch")
    depth = len(lecture.relative_path.split("/"))
    title = f"Assistir: {lecture.title}"
    body = _watch_bar(lecture) + (
        f'<article class="watch-page">{_video_element(_video_url(lecture, video))}</article>'
    )
    return _document(title, body, depth=depth)


def _watch_bar(lecture: Lecture) -> str:
    return (
        '<header class="reader-bar">'
        f'<a class="reader-back" href="index.html" aria-label="Voltar para {escape(lecture.title)}">'
        "‹ Voltar</a>"
        f'<h1 class="reader-title">{escape(lecture.title)}</h1>'
        "</header>"
    )


def render_reading_page(
    lecture: Lecture,
    material: Material,
    body_html: str,
    toc: tuple[TocEntry, ...],
    pdf_href: str | None,
    previous_material: Material | None,
    next_material: Material | None,
) -> str:
    """A material's own page: read in the browser, with the PDF one link
    away rather than embedded (brief: reading no longer requires the PDF
    viewer an in-app browser cannot render). Sits beside the material's
    source files -- every relative link here, including every figure
    reference inside body_html, resolves without a depth prefix."""
    depth = len(lecture.relative_path.split("/"))
    title = f"{_material_display_name(material)} — {lecture.title}"
    body = (
        _reading_bar(lecture, material, pdf_href)
        + f'<article class="reader-body">{_render_toc(toc)}{body_html}</article>'
        + _reading_footer(lecture, previous_material, next_material)
    )
    return _document(title, body, depth=depth)


def _reading_bar(lecture: Lecture, material: Material, pdf_href: str | None) -> str:
    pdf_link = (
        f'<a class="reader-pdf" href="{escape(pdf_href)}">Baixar PDF</a>'
        if pdf_href is not None
        else ""
    )
    return (
        '<header class="reader-bar">'
        f'<a class="reader-back" href="index.html" aria-label="Voltar para {escape(lecture.title)}">'
        "‹ Voltar</a>"
        f'<h1 class="reader-title">{escape(_material_display_name(material))}</h1>'
        f"{pdf_link}</header>"
    )


def _render_toc(toc: tuple[TocEntry, ...]) -> str:
    if not toc:
        return ""
    items = "".join(
        f'<li class="toc-{entry.level}"><a href="#{escape(entry.anchor, quote=True)}">'
        f"{escape(entry.text)}</a></li>"
        for entry in toc
    )
    return (
        '<details class="reader-toc" open><summary>Nesta página</summary>'
        f"<ul>{items}</ul></details>"
    )


def _reading_footer(
    lecture: Lecture,
    previous_material: Material | None,
    next_material: Material | None,
) -> str:
    previous_link = (
        f'<a class="reader-adjacent" href="{escape(_reading_stem(previous_material))}.html">'
        f"‹ {escape(_material_display_name(previous_material))}</a>"
        if previous_material is not None
        else ""
    )
    next_link = (
        f'<a class="reader-adjacent" href="{escape(_reading_stem(next_material))}.html">'
        f"{escape(_material_display_name(next_material))} ›</a>"
        if next_material is not None
        else ""
    )
    return (
        '<footer class="reader-foot">'
        f'<a class="reader-adjacent" href="index.html">Voltar para {escape(lecture.title)}</a>'
        f"{previous_link}{next_link}</footer>"
    )


def _reading_stem(material: Material) -> str:
    return material.relative_path.rsplit(".", 1)[0]


# --- home page -------------------------------------------------------


def _home_header(catalog: SiteCatalog) -> str:
    lecture_count = len(catalog.lectures)
    return (
        '<header class="masthead"><p class="kicker">Materiais de estudo por '
        "aula</p><h1>Biblioteca de estudo</h1>"
        f'<p class="count">{lecture_count} aula(s) disponível(is)</p></header>'
    )


def _render_search_box() -> str:
    return (
        '<div class="home-search">'
        '<label for="lecture-search">Buscar por aula, tópico ou disciplina</label>'
        '<input type="search" id="lecture-search" placeholder="Digite para filtrar…" '
        'autocomplete="off">'
        "</div>"
    )


def _render_recent_section(
    recent: tuple[Lecture, ...], contexts: dict[str, "_LectureContext"]
) -> str:
    if not recent:
        return (
            '<section class="recent"><h2>Recentes</h2>'
            "<p>Nenhuma aula publicada ainda — o catálogo aparece abaixo assim "
            "que houver material.</p></section>"
        )
    items = "".join(_render_recent_item(lecture, contexts) for lecture in recent)
    return f'<section class="recent"><h2>Recentes</h2><ul class="recent-list">{items}</ul></section>'


def _render_recent_item(
    lecture: Lecture, contexts: dict[str, "_LectureContext"]
) -> str:
    context = contexts.get(lecture.lecture_id)
    trail = escape(context.trail_label) if context is not None else ""
    href = escape(f"{lecture.relative_path}/index.html")
    return (
        '<li class="recent-item">'
        f'<a href="{href}">'
        f'<span class="trail">{trail}</span>'
        f'<span class="title">{escape(lecture.title)}</span>'
        "</a>"
        f"{_recency_badge(lecture.updated_at)}"
        f"{_material_summary(lecture)}"
        "</li>"
    )


def _render_pending_section(pending_ids: tuple[str, ...]) -> str:
    if not pending_ids:
        return ""
    items = "".join(f"<li>{escape(pending_id)}</li>" for pending_id in pending_ids)
    return (
        '<section class="pending"><h2>Em processamento</h2>'
        "<p>Estas aulas têm data confirmada no cronograma oficial, mas o "
        "material ainda não foi publicado.</p>"
        f'<ul class="pending-list">{items}</ul></section>'
    )


def _render_unscheduled_section(unscheduled_ids: tuple[str, ...]) -> str:
    if not unscheduled_ids:
        return ""
    items = "".join(f"<li>{escape(lecture_id)}</li>" for lecture_id in unscheduled_ids)
    return (
        '<section class="pending unscheduled"><h2>Aguardando data oficial</h2>'
        "<p>Estas aulas aparecem no portal, mas ainda não constam no "
        "cronograma oficial -- sem data confirmada, sem previsão de "
        "publicação.</p>"
        f'<ul class="pending-list">{items}</ul></section>'
    )


def _render_catalog_tree(catalog: SiteCatalog) -> str:
    sections = []
    for year in catalog.years:
        for semester in year.semesters:
            sections.append(_render_semester_block(year, semester))
    return f'<section class="catalog"><h2>Catálogo completo</h2>{"".join(sections)}</section>'


def _render_semester_block(year: Year, semester: Semester) -> str:
    semester_anchor = anchor_id(year.slug, semester.slug)
    disciplines = "".join(
        _render_discipline_block(year, semester, discipline)
        for discipline in semester.disciplines
    )
    return (
        f'<details class="semester" id="{escape(semester_anchor)}" open '
        f'data-persist-id="{escape(semester_anchor)}">'
        f"<summary>{escape(semester.title)} de {escape(year.title)}</summary>"
        f"{disciplines}</details>"
    )


def _render_discipline_block(
    year: Year, semester: Semester, discipline: Discipline
) -> str:
    discipline_anchor = anchor_id(year.slug, semester.slug, discipline.slug)
    topics = "".join(
        _render_topic_block(year, semester, discipline, topic)
        for topic in discipline.topics
    )
    return (
        f'<div class="discipline" id="{escape(discipline_anchor)}">'
        f"<h3>{escape(discipline.title)}</h3>{topics}</div>"
    )


def _render_topic_block(
    year: Year, semester: Semester, discipline: Discipline, topic: Topic
) -> str:
    topic_anchor = anchor_id(year.slug, semester.slug, discipline.slug, topic.slug)
    items = "".join(_render_catalog_lecture_item(lecture) for lecture in topic.lectures)
    return (
        f'<div class="topic" id="{escape(topic_anchor)}">'
        f"<h4>{escape(topic.title)}</h4><ul>{items}</ul></div>"
    )


def _render_catalog_lecture_item(lecture: Lecture) -> str:
    return (
        f'<li data-lecture-id="{escape(lecture.lecture_id)}">'
        f'<a href="{escape(lecture.relative_path)}/index.html">{escape(lecture.title)}</a> '
        f'<span class="lecture-id">{escape(lecture.lecture_id)}</span>'
        f"{_material_summary(lecture)}</li>"
    )


def _render_search_index_script(entries: list[dict[str, str]]) -> str:
    # Escape "<" so a "</script>" substring inside any entry (title,
    # discipline, topic) cannot close the script block early and let
    # attacker-controlled markup execute as page HTML (security review
    # finding, 2026-09-15) -- defense in depth even though current catalog
    # slugification already forecloses this path.
    payload = json.dumps(entries, ensure_ascii=False).replace("<", "\\u003c")
    return (
        f'<script type="application/json" id="site-search-index">{payload}</script>'
        f"<script>{HOME_SCRIPT}</script>"
    )


@dataclass(frozen=True, slots=True)
class _LectureContext:
    """A lecture's discipline/topic trail, for the Recentes list (brief
    §1.2) -- `Lecture` itself carries no back-reference to its parents."""

    trail_label: str


def _lecture_contexts(catalog: SiteCatalog) -> dict[str, _LectureContext]:
    return {
        lecture.lecture_id: _LectureContext(
            trail_label=f"{discipline.title} · {topic.title}"
        )
        for year in catalog.years
        for semester in year.semesters
        for discipline in semester.disciplines
        for topic in discipline.topics
        for lecture in topic.lectures
    }


def _recency_badge(updated_at: datetime) -> str:
    age_days = (datetime.now(UTC) - updated_at).total_seconds() / 86400
    if age_days <= _NEW_BADGE_WINDOW_DAYS:
        return '<span class="badge new">novo</span>'
    return f'<span class="badge updated">atualizado em {updated_at.strftime("%d/%m")}</span>'


# --- lecture page ------------------------------------------------------


def _lecture_header(
    lecture: Lecture, breadcrumb: tuple[BreadcrumbSegment, ...], up: str
) -> str:
    trail = " · ".join(
        f'<a href="{escape(up)}index.html#{escape(segment.anchor)}" '
        f'aria-label="Voltar para {escape(segment.label)}">{escape(segment.label)}</a>'
        for segment in breadcrumb
    )
    return (
        f'<header class="masthead"><p class="kicker">{trail}</p>'
        f"<h1>{escape(lecture.title)}</h1>"
        f'<p class="count">Aula {escape(lecture.lecture_id)} · '
        f"{_recency_badge(lecture.updated_at)}</p></header>"
    )


def _render_back_link(breadcrumb: tuple[BreadcrumbSegment, ...], up: str) -> str:
    anchor = breadcrumb[-1].anchor if breadcrumb else ""
    href = f"{up}index.html" + (f"#{anchor}" if anchor else "")
    return f'<p class="back"><a href="{escape(href)}">Voltar ao índice</a></p>'


def _render_video_section(lecture: Lecture) -> str:
    video = lecture.video
    if video is None:
        return ""
    video_element = _video_element(_video_url(lecture, video))
    return (
        '<section class="watch"><h2>Assistir</h2>'
        f"{video_element}"
        '<a class="watch-fullscreen" href="assistir.html">Assistir em tela cheia</a>'
        "</section>"
    )


def _video_url(lecture: Lecture, video: Material) -> str:
    return f"/videos/{lecture.relative_path}/{video.relative_path}"


def _video_element(video_url: str) -> str:
    escaped_url = escape(video_url)
    return (
        f'<video controls preload="metadata" src="{escaped_url}">'
        f"Seu navegador não reproduz vídeo. "
        f'<a href="{escaped_url}">Baixar o vídeo da aula</a>.'
        f"</video>"
        f'<a class="watch-download" href="{escaped_url}" download>Baixar vídeo</a>'
    )


def _render_lecture_footer_nav(navigation: LectureNavigation, up: str) -> str:
    previous_link = _adjacent_lecture_link(
        navigation.previous_lecture, up, "Aula anterior", "‹"
    )
    next_link = _adjacent_lecture_link(navigation.next_lecture, up, "Próxima aula", "›")
    if not previous_link and not next_link:
        return ""
    return (
        '<nav class="lecture-nav" aria-label="Navegação entre aulas">'
        f"{previous_link}{next_link}</nav>"
    )


def _adjacent_lecture_link(
    lecture: Lecture | None, up: str, label: str, glyph: str
) -> str:
    if lecture is None:
        return ""
    href = f"{up}{lecture.relative_path}/index.html"
    text = f"{glyph} {label}" if glyph == "‹" else f"{label} {glyph}"
    return (
        f'<a class="adjacent-lecture" href="{escape(href)}" '
        f'aria-label="{escape(label)}: {escape(lecture.title)}">{escape(text)}</a>'
    )


# --- material cards (brief §2.1, §2.2, §3) ------------------------------


def _render_material_cards(
    lecture: Lecture, reading_pages: Mapping[str, str], has_interactive_quiz: bool
) -> str:
    cards = "".join(
        _render_material_card(
            lecture.lecture_id, card, reading_pages, has_interactive_quiz
        )
        for card in lecture.material_cards
    )
    extras = _render_uncarded_materials(lecture)
    if not cards and not extras:
        return _render_no_materials_notice()
    return (
        f'<section class="materials"><h2>Materiais</h2>'
        f'<div class="card-grid">{cards}</div>{extras}</section>'
    )


def _render_no_materials_notice() -> str:
    """A lecture with only a video (a video-only clip, ADR 0043, or any
    bundle whose materials have not been produced) gets this note, not an
    empty grid under a "Materiais" heading. The wording is true for both."""
    return (
        '<section class="materials"><h2>Materiais</h2>'
        '<p class="no-materials">Vídeo sem material de estudo gerado.</p>'
        "</section>"
    )


def _render_material_card(
    lecture_id: str,
    card: MaterialCard,
    reading_pages: Mapping[str, str],
    has_interactive_quiz: bool,
) -> str:
    card_id = f"card-{lecture_id}-{card.kind}"
    if card.kind == "podcast":
        inner = _render_podcast_card_body(card, reading_pages)
    elif card.kind == "simulado":
        inner = _render_simulado_card_body(lecture_id, card, has_interactive_quiz)
    else:
        inner = _render_material_actions(card.formats, reading_pages)
    return (
        f'<article class="card" id="{escape(card_id)}">'
        f"<h3>{escape(card.label)}</h3>{inner}</article>"
    )


def _render_material_actions(
    formats: tuple[Material, ...], reading_pages: Mapping[str, str]
) -> str:
    """One action per format: a material with a reading page gets a "Ler"
    link into it; a PDF gets a plain link, deliberately without a
    `download` attribute, so a browser's own PDF viewer (with its own
    zoom, search and Share-to-save) opens it instead of forcing a file
    save an in-app browser cannot always complete; a CSV, being a
    machine-import file rather than another view of the same document,
    keeps a real forced download."""
    previewable = tuple(material for material in formats if material.kind != "video")
    if not previewable:
        return ""
    actions = "".join(
        _render_one_action(material, reading_pages) for material in previewable
    )
    return f'<div class="material-actions">{actions}</div>'


def _render_one_action(material: Material, reading_pages: Mapping[str, str]) -> str:
    suffix = _suffix(material)
    if suffix == ".md":
        href = reading_pages.get(material.relative_path)
        if href is not None:
            return (
                f'<a class="action-read" href="{escape(href)}">'
                f"Ler {escape(_material_display_name(material))}</a>"
            )
        return _render_download_icon(material)
    if suffix == ".pdf":
        return (
            f'<a class="action-pdf" href="{escape(material.relative_path)}">'
            "Baixar PDF</a>"
        )
    return _render_download_icon(material)


def _render_download_icon(material: Material) -> str:
    suffix = _suffix(material)
    css_class = suffix.lstrip(".")
    label = f"Baixar {_material_display_name(material)} em {_FORMAT_WORDS.get(suffix, suffix)}"
    return (
        f'<a class="download dl-{css_class}" href="{escape(material.relative_path)}" '
        f'download aria-label="{escape(label)}">Baixar {_FORMAT_WORDS.get(suffix, suffix)}</a>'
    )


def _render_podcast_card_body(
    card: MaterialCard, reading_pages: Mapping[str, str]
) -> str:
    audio = next(
        (material for material in card.formats if _suffix(material) == ".m4a"), None
    )
    roteiro_formats = tuple(
        material for material in card.formats if _suffix(material) != ".m4a"
    )
    audio_block = ""
    if audio is not None:
        audio_block = (
            '<div class="listen">'
            f'<audio controls preload="none" src="{escape(audio.relative_path)}">'
            f'<a href="{escape(audio.relative_path)}">Baixar o áudio</a></audio>'
            f"{_render_download_icon(audio)}</div>"
        )
    roteiro_block = ""
    if roteiro_formats:
        roteiro_block = (
            '<div class="podcast-roteiro"><h4>Roteiro</h4>'
            f"{_render_material_actions(roteiro_formats, reading_pages)}"
            "</div>"
        )
    return audio_block + roteiro_block


def _render_simulado_card_body(
    lecture_id: str, card: MaterialCard, has_interactive_quiz: bool
) -> str:
    """The simulado card has no passive "Ler" view: the server-graded
    interactive quiz (SITE-09, ADR 0041) replaces it outright, alongside
    the PDF download every other card keeps. The `.md`/`.typ` source
    formats stay unlinked here -- they exist only to produce the PDF and
    the retired preview, never a page a student opens directly.

    `has_interactive_quiz` gates the "Responder simulado" link on a
    compiled exam.json actually existing (checked at generation time by
    the caller): a simulado.md/simulado.pdf pair can be published without
    one (a rejected or not-yet-run exam-compilation stage), and offering
    a link into a route that always 404s would be the same broken promise
    to a student this card was rewritten to fix."""
    pdf_action = "".join(
        _render_one_action(material, {})
        for material in card.formats
        if _suffix(material) == ".pdf"
    )
    if not has_interactive_quiz:
        return f'<div class="material-actions">{pdf_action}</div>'
    quiz_action = (
        f'<a class="action-quiz" href="/simulado/{escape(lecture_id)}">'
        "Responder simulado</a>"
    )
    return f'<div class="material-actions">{quiz_action}{pdf_action}</div>'


def _render_uncarded_materials(lecture: Lecture) -> str:
    """Materials that fell outside every known card kind (an unrecognized
    file name, per `catalog._card_kind`) still get listed -- the site never
    silently drops a material a student's bundle actually contains. The
    one exception is an image in a subdirectory (the pipeline's
    figuras/*.png slide frames): published so markdown-preview references
    resolve, unlisted because dozens of frames would bury the table. A
    root-level standalone image IS listed."""
    carded_paths = {
        material.relative_path
        for card in lecture.material_cards
        for material in card.formats
    }
    leftovers = [
        material
        for material in lecture.materials
        if material.kind != "video"
        and not (material.kind == "image" and "/" in material.relative_path)
        and material.relative_path not in carded_paths
    ]
    if not leftovers:
        return ""
    rows = "".join(
        f"<tr><td>{escape(material.label)}</td>"
        f'<td class="size">{_human_size(material.byte_size)}</td>'
        f"<td>{_render_download_icon(material)}</td></tr>"
        for material in leftovers
    )
    return (
        '<div class="table-scroll"><table class="extra-materials">'
        "<caption>Outros materiais</caption>"
        f"<thead><tr><th>Material</th><th>Tamanho</th><th></th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _material_summary(lecture: Lecture) -> str:
    badges = []
    if lecture.video is not None:
        badges.append('<span class="badge video">vídeo</span>')
    if lecture.has_study_guide:
        badges.append('<span class="badge pdf">apostila</span>')
    return "".join(badges)


def _material_display_name(material: Material) -> str:
    return material.label.rsplit(" (", 1)[0]


def _suffix(material: Material) -> str:
    return "." + material.relative_path.rsplit(".", 1)[-1].lower()


def _human_size(byte_size: int) -> str:
    if byte_size >= 1_000_000_000:
        return f"{byte_size / 1_000_000_000:.1f} GB"
    if byte_size >= 1_000_000:
        return f"{byte_size / 1_000_000:.0f} MB"
    if byte_size >= 1_000:
        return f"{byte_size / 1_000:.0f} KB"
    return f"{byte_size} B"


def _document(title: str | None, body: str, depth: int) -> str:
    stylesheet = "../" * depth + "estilo.css"
    logout_link = "../" * depth + "sair"
    page_title = (
        "Biblioteca de estudo" if title is None else f"{title} — Biblioteca de estudo"
    )
    return (
        "<!doctype html>\n"
        '<html lang="pt-BR">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(page_title)}</title>\n"
        f'<link rel="stylesheet" href="{escape(stylesheet)}">\n'
        "</head>\n<body>\n"
        f'<div class="page">{uncertified_notice_html()}{body}'
        "<footer>"
        f'<p><a href="{escape(logout_link)}">Sair</a></p>'
        "</footer></div>\n"
        "</body>\n</html>\n"
    )


# Vanilla JS, no framework (brief §1.1, §1.2): filters the catalog tree by
# the embedded search index, and persists each collapsible section's
# open/closed state per viewer (brief §1.2 Zone C). Kept deliberately small.
HOME_SCRIPT = """
(function () {
  var index = JSON.parse(document.getElementById('site-search-index').textContent);
  var byId = {};
  index.forEach(function (entry) {
    byId[entry.id] = (entry.title + ' ' + entry.discipline + ' ' + entry.topic).toLowerCase();
  });
  var input = document.getElementById('lecture-search');
  input.addEventListener('input', function () {
    var query = input.value.trim().toLowerCase();
    document.querySelectorAll('.catalog li[data-lecture-id]').forEach(function (item) {
      var id = item.getAttribute('data-lecture-id');
      var haystack = byId[id] || '';
      var matches = query === '' || haystack.indexOf(query) !== -1;
      item.hidden = !matches;
      if (matches && query !== '') {
        var details = item.closest('details.semester');
        if (details) { details.open = true; }
      }
    });
  });
  document.querySelectorAll('details[data-persist-id]').forEach(function (details) {
    var key = 'site-catalog-open:' + details.getAttribute('data-persist-id');
    try {
      var saved = localStorage.getItem(key);
      if (saved !== null) { details.open = saved === '1'; }
    } catch (err) {}
    details.addEventListener('toggle', function () {
      try { localStorage.setItem(key, details.open ? '1' : '0'); } catch (err) {}
    });
  });
})();
"""


STYLESHEET = """
:root {
  color-scheme: light dark;
  --paper: #eef1ea;
  --card: #ffffff;
  --ink: #17231f;
  --ink-soft: #46564d;
  --ink-faint: #7c8a80;
  --rule: #d3dbd0;
  --accent: #1f6f5c;
  --accent-warm: #b5651d;
  --accent-soft: #dcece6;
}

@media (prefers-color-scheme: dark) {
  :root {
    --paper: #0d1512;
    --card: #13201b;
    --ink: #e8f0ea;
    --ink-soft: #adc0b5;
    --ink-faint: #7b8c82;
    --rule: #24352c;
    --accent: #4fd3ac;
    --accent-warm: #e2984f;
    --accent-soft: #16332a;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  padding: 0 20px;
  background: var(--paper);
  color: var(--ink);
  font: 16px/1.55 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}

.page {
  max-width: 920px;
  margin: 0 auto;
  padding-block: 32px 64px;
}

.uncertified-notice {
  margin: 0 0 22px; padding: 10px 14px; border-left: 4px solid var(--accent-warm);
  border-radius: 4px; background: var(--card); color: var(--ink-soft); font-size: 14px;
}

.masthead { border-bottom: 2px solid var(--ink); padding-bottom: 14px; margin-bottom: 26px; }
.masthead h1 {
  font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  font-size: clamp(26px, 4vw, 36px);
  margin: 0 0 4px;
  line-height: 1.15;
}
.kicker { margin: 0 0 6px; font-size: 12px; letter-spacing: .06em; color: var(--accent); }
.kicker a { color: inherit; text-decoration: underline; text-underline-offset: 2px; }
.count { margin: 0; color: var(--ink-faint); font-size: 13px; }

h2 {
  font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  font-size: 21px;
  margin: 34px 0 14px;
}
h3 { font-size: 17px; margin: 0 0 10px; }
h4 { font-size: 13px; letter-spacing: .04em; color: var(--ink-faint); margin: 16px 0 6px; }

ul { list-style: none; margin: 0; padding: 0; }

a { color: var(--accent); }

.lecture-id { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: var(--ink-faint); }

.badge { font-size: 11px; letter-spacing: .03em; padding: 1px 8px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); }
.badge.new { background: var(--accent-warm); color: var(--paper); }

.back { margin: 0 0 20px; font-size: 14px; }

video { width: 100%; max-width: 100%; border-radius: 4px; background: #000; }

.watch { margin-bottom: 8px; }
.watch-fullscreen, .watch-download {
  display: inline-block; margin-top: 8px; margin-right: 16px; font-size: 13px; color: var(--ink-soft); text-decoration: none;
}
.watch-fullscreen:hover, .watch-download:hover { color: var(--accent); }
.watch-fullscreen:focus-visible, .watch-download:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.watch-page { max-width: 900px; }

/* -- home search -- */
.home-search { margin-bottom: 28px; }
.home-search label { display: block; font-size: 13px; color: var(--ink-soft); margin-bottom: 6px; }
.home-search input {
  width: 100%;
  padding: 10px 12px;
  font-size: 15px;
  border: 1px solid var(--rule);
  border-radius: 6px;
  background: var(--card);
  color: var(--ink);
}
.home-search input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

/* -- recent zone -- */
.recent-list { display: flex; flex-direction: column; gap: 2px; }
.recent-item {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;
  padding: 10px 0; border-top: 1px solid var(--rule);
}
.recent-item:first-child { border-top: 0; }
.recent-item a { display: flex; flex-direction: column; gap: 2px; text-decoration: none; flex: 1; min-width: 200px; }
.recent-item .trail { font-size: 12px; color: var(--ink-faint); }
.recent-item .title { color: var(--ink); font-weight: 600; }
.badge.updated { background: transparent; border: 1px solid var(--rule); color: var(--ink-faint); }

/* -- pending zone -- */
.pending { background: var(--card); border: 1px dashed var(--rule); border-radius: 6px; padding: 14px 16px; }
.pending h2 { margin-top: 0; }
.pending-list { display: flex; flex-wrap: wrap; gap: 6px; }
.pending-list li { font-family: ui-monospace, Menlo, monospace; font-size: 12px; background: var(--accent-soft); color: var(--accent); padding: 2px 8px; border-radius: 999px; }

/* -- catalog tree -- */
.catalog details.semester { border-top: 1px solid var(--rule); padding: 10px 0; }
.catalog details.semester summary { cursor: pointer; font-weight: 600; padding: 4px 0; }
.catalog details.semester summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.discipline { margin: 10px 0 10px 4px; }
.topic { margin: 8px 0 8px 12px; }
.catalog li {
  padding: 7px 0; border-top: 1px solid var(--rule);
  display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
}
.catalog li:first-child { border-top: 0; }

/* -- material cards -- */
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.card {
  background: var(--card); border: 1px solid var(--rule); border-radius: 8px;
  padding: 16px; display: flex; flex-direction: column; gap: 10px;
}
.material-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.action-read {
  font-size: 14px; font-weight: 600; color: var(--accent); text-decoration: none;
  padding: 6px 12px; border-radius: 6px; background: var(--accent-soft);
}
.action-read:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.action-pdf {
  font-size: 13px; color: var(--ink-soft); text-decoration: none;
  padding: 6px 12px; border-radius: 6px; border: 1px solid var(--rule);
}
.action-pdf:hover { color: var(--accent); border-color: var(--accent); }
.action-pdf:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.action-quiz {
  font-size: 14px; font-weight: 600; color: #fff; text-decoration: none;
  padding: 6px 14px; border-radius: 6px; background: var(--accent);
}
.action-quiz:hover { opacity: 0.9; }
.action-quiz:focus-visible { outline: 2px solid var(--ink); outline-offset: 1px; }

.download {
  display: inline-flex; align-items: center; gap: 4px; font-size: 13px;
  text-decoration: none; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--rule);
}
.download:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

.listen { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.listen audio { flex: 1; min-width: 220px; }
.podcast-roteiro { border-top: 1px solid var(--rule); padding-top: 10px; }

/* -- reading page -- */
.reader-bar {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding-bottom: 14px; margin-bottom: 22px; border-bottom: 2px solid var(--ink);
}
.reader-back { flex: none; color: var(--accent); text-decoration: none; font-size: 15px; }
.reader-back:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.reader-title {
  flex: 1; min-width: 0; margin: 0; font-size: 19px; font-weight: 600;
  font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; color: var(--ink);
}
.reader-pdf {
  flex: none; padding: 5px 14px; border: 1px solid var(--rule); border-radius: 999px;
  font-size: 13px; color: var(--ink-soft); text-decoration: none;
}
.reader-pdf:hover { color: var(--accent); border-color: var(--accent); }
.reader-pdf:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.reader-toc { margin: 0 0 28px; padding: 12px 16px; background: var(--card); border: 1px solid var(--rule); border-radius: 8px; }
.reader-toc summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--ink-soft); }
.reader-toc ul { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.reader-toc a { font-size: 14px; text-decoration: none; }
.reader-toc .toc-3 { padding-left: 16px; font-size: 13px; color: var(--ink-soft); }

.reader-body {
  max-width: 68ch;
  font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  font-size: 19px; line-height: 1.62; color: var(--ink);
}
.reader-body > :first-child { margin-top: 0; }
.reader-body p { margin: 0 0 20px; }
.reader-body h2 { font-size: 23px; margin: 40px 0 12px; padding-top: 18px; border-top: 1px solid var(--rule); }
.reader-body h3 {
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 15px; font-weight: 600; letter-spacing: .02em; color: var(--ink-soft);
  margin: 30px 0 8px;
}
.reader-body ul, .reader-body ol { list-style: revert; margin: 0 0 20px; padding-left: 22px; }
.reader-body li + li { margin-top: 6px; }
.reader-body img { display: block; width: 100%; max-width: 100%; height: auto; border-radius: 6px; background: var(--card); }
.reader-body img + em { display: block; font-family: -apple-system, sans-serif; font-size: 13px; color: var(--ink-faint); font-style: normal; margin: 6px 0 20px; }

.reveal { margin: 24px 0; padding: 14px 16px; border: 1px solid var(--rule); border-radius: 8px; background: var(--card); }
.reveal summary { cursor: pointer; font-weight: 600; color: var(--accent); }
.reveal summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.reveal[open] summary { margin-bottom: 12px; }

.reader-foot {
  max-width: 68ch; margin: 44px 0 0; padding-top: 16px; border-top: 1px solid var(--rule);
  display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px;
}
.reader-adjacent { font-size: 14px; text-decoration: none; }
.reader-adjacent:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* -- lecture footer nav -- */
.lecture-nav { display: flex; justify-content: space-between; gap: 12px; margin-top: 40px; border-top: 1px solid var(--rule); padding-top: 16px; }
.adjacent-lecture { font-size: 14px; text-decoration: none; }
.adjacent-lecture:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
table caption { text-align: left; font-size: 13px; color: var(--ink-faint); margin-bottom: 6px; }
th { text-align: left; font-size: 11px; letter-spacing: .04em; color: var(--ink-faint); padding: 8px 10px; }
td { padding: 9px 10px; border-top: 1px solid var(--rule); }
td.size { font-variant-numeric: tabular-nums; color: var(--ink-faint); white-space: nowrap; }

footer { margin-top: 48px; border-top: 1px solid var(--rule); padding-top: 14px; color: var(--ink-faint); font-size: 12.5px; }

@media (max-width: 700px) {
  .card-grid { grid-template-columns: 1fr; }
  .lecture-nav { flex-direction: column; }
  .reader-foot { flex-direction: column; }
}
"""
