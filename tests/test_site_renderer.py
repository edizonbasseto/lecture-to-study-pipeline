"""Renderer tests: the study-site UX pass (site-ux-brief.md) -- the
Recentes/pending/catalog home layout, the 9-card lecture page, the
preview/download interaction, wayfinding links, and accessibility
requirements it specifies."""

from datetime import UTC, datetime, timedelta

import pytest

from lecture_study.site.catalog import (
    Discipline,
    Lecture,
    Material,
    SiteCatalog,
    Semester,
    Topic,
    Year,
)
from lecture_study.site.renderer import (
    BreadcrumbSegment,
    LectureNavigation,
    anchor_id,
    render_home,
    render_lecture,
    render_watch_page,
)

_NOW = datetime.now(UTC)


def _material(name: str, kind: str) -> Material:
    return Material(label=f"label-{name}", relative_path=name, byte_size=10, kind=kind)


def _lecture(
    lecture_id: str = "1",
    slug: str = "1-aula",
    materials: tuple[Material, ...] = (),
    updated_at: datetime = _NOW,
) -> Lecture:
    return Lecture(
        lecture_id=lecture_id,
        title="Aula de Teste",
        slug=slug,
        relative_path=f"2026/1-semestre/d/t/{slug}",
        materials=materials,
        updated_at=updated_at,
    )


def _catalog_with(lecture: Lecture) -> SiteCatalog:
    topic = Topic(title="Tópico", slug="t", lectures=(lecture,))
    discipline = Discipline(title="Disciplina", slug="d", topics=(topic,))
    semester = Semester(
        title="1º semestre", slug="1-semestre", disciplines=(discipline,)
    )
    year = Year(title="2026", slug="2026", semesters=(semester,))
    return SiteCatalog(years=(year,))


class TestAnchorId:
    def test_joins_slugs_with_a_dash(self):
        assert anchor_id("2026", "1-semestre", "d", "t") == "2026-1-semestre-d-t"


class TestRenderHomeRecentZone:
    def test_recent_lecture_is_listed_with_its_discipline_and_topic_trail(self):
        lecture = _lecture(materials=(_material("apostila_completa.pdf", "pdf"),))
        catalog = _catalog_with(lecture)

        html = render_home(catalog, recent=(lecture,))

        assert "Recentes" in html
        assert "Disciplina · Tópico" in html
        assert "Aula de Teste" in html

    def test_empty_recent_list_still_renders_a_soft_labeled_zone(self):
        html = render_home(SiteCatalog(years=()), recent=())

        assert "Recentes" in html

    def test_a_recently_updated_lecture_is_badged_novo(self):
        lecture = _lecture(updated_at=_NOW)
        catalog = _catalog_with(lecture)

        html = render_home(catalog, recent=(lecture,))

        assert "novo" in html

    def test_an_old_lecture_is_badged_with_its_update_date(self):
        old = _NOW - timedelta(days=30)
        lecture = _lecture(updated_at=old)
        catalog = _catalog_with(lecture)

        html = render_home(catalog, recent=(lecture,))

        assert f"atualizado em {old.strftime('%d/%m')}" in html


class TestRenderHomePendingZone:
    def test_pending_ids_render_in_a_labeled_section_not_a_stray_paragraph(self):
        html = render_home(SiteCatalog(years=()), pending=("99999",))

        assert '<section class="pending">' in html
        assert "Em processamento" in html
        assert "99999" in html

    def test_pending_section_never_claims_active_generation(self):
        """Regression (2026-09-18): a lecture with a confirmed schedule
        date but no published material yet is not necessarily "being
        generated" -- it may be permanently blocked by a fail-closed
        pipeline check. The section must not assert an in-progress claim
        it cannot back up."""
        html = render_home(SiteCatalog(years=()), pending=("99999",))

        assert "ainda está sendo gerado" not in html

    def test_no_pending_section_when_nothing_is_pending(self):
        html = render_home(SiteCatalog(years=()), pending=())

        assert 'class="pending"' not in html

    def test_unscheduled_ids_render_in_their_own_distinct_section(self):
        """A lecture the portal lists but the official schedule never
        dated is a different claim from a dated-but-unpublished one
        (2026-09-18) -- conflating the two into one "Em processamento"
        bucket told students material was "sendo gerado" for lectures
        that may not run for a long time, if ever."""
        html = render_home(SiteCatalog(years=()), unscheduled_pending=("10014",))

        assert '<section class="pending unscheduled">' in html
        assert "Aguardando data oficial" in html
        assert "10014" in html

    def test_no_unscheduled_section_when_nothing_is_unscheduled(self):
        html = render_home(SiteCatalog(years=()), unscheduled_pending=())

        assert "unscheduled" not in html

    def test_pending_and_unscheduled_sections_coexist_without_mixing_ids(self):
        html = render_home(
            SiteCatalog(years=()),
            pending=("11111",),
            unscheduled_pending=("22222",),
        )

        pending_section = html.split('<section class="pending unscheduled">')[0]
        assert "11111" in pending_section
        assert "22222" not in pending_section
        assert "22222" in html.split('<section class="pending unscheduled">')[1]


class TestRenderHomeCatalogTree:
    def test_semester_discipline_and_topic_carry_the_anchors_a_breadcrumb_links_to(
        self,
    ):
        lecture = _lecture(materials=(_material("apostila_completa.pdf", "pdf"),))
        catalog = _catalog_with(lecture)

        html = render_home(catalog)

        assert 'id="2026-1-semestre"' in html
        assert 'id="2026-1-semestre-d"' in html
        assert 'id="2026-1-semestre-d-t"' in html

    def test_semester_sections_use_native_details_summary(self):
        lecture = _lecture(materials=(_material("apostila_completa.pdf", "pdf"),))
        catalog = _catalog_with(lecture)

        html = render_home(catalog)

        assert "<details" in html
        assert "<summary>" in html

    def test_search_index_is_embedded_as_json_for_the_client_side_filter(self):
        lecture = _lecture(materials=(_material("apostila_completa.pdf", "pdf"),))
        catalog = _catalog_with(lecture)

        html = render_home(
            catalog,
            search_entries=[
                {
                    "id": "1",
                    "title": "Aula de Teste",
                    "discipline": "Disciplina",
                    "topic": "Tópico",
                    "url": "2026/1-semestre/d/t/1-aula/index.html",
                }
            ],
        )

        assert 'id="site-search-index"' in html
        assert '"Aula de Teste"' in html


def _navigation(
    breadcrumb: tuple[BreadcrumbSegment, ...] = (),
    previous_lecture: Lecture | None = None,
    next_lecture: Lecture | None = None,
) -> LectureNavigation:
    return LectureNavigation(breadcrumb, previous_lecture, next_lecture)


class TestPageFooterLogoutLink:
    def test_the_home_page_links_to_logout_at_its_own_depth(self):
        catalog = _catalog_with(_lecture())

        html = render_home(catalog)

        assert '<a href="sair">Sair</a>' in html

    def test_a_lecture_page_links_to_logout_relative_to_its_depth(self):
        lecture = _lecture()

        html = render_lecture(lecture, _navigation())

        assert '<a href="../../../../../sair">Sair</a>' in html


class TestRenderLectureBreadcrumb:
    def test_every_segment_is_a_real_link_to_its_anchor(self):
        lecture = _lecture()
        breadcrumb = (
            BreadcrumbSegment("2026", "2026-1-semestre"),
            BreadcrumbSegment("1º semestre", "2026-1-semestre"),
            BreadcrumbSegment("Disciplina", "2026-1-semestre-d"),
            BreadcrumbSegment("Tópico", "2026-1-semestre-d-t"),
        )

        html = render_lecture(lecture, _navigation(breadcrumb))

        assert '<a href="../../../../../index.html#2026-1-semestre-d"' in html
        assert 'aria-label="Voltar para Disciplina"' in html

    def test_back_link_targets_the_last_breadcrumb_segments_anchor(self):
        lecture = _lecture()
        breadcrumb = (BreadcrumbSegment("Tópico", "2026-1-semestre-d-t"),)

        html = render_lecture(lecture, _navigation(breadcrumb))

        assert (
            'class="back"><a href="../../../../../index.html#2026-1-semestre-d-t"'
            in html
        )


class TestRenderLectureFooterNav:
    def test_previous_and_next_links_carry_full_titles_in_their_aria_label(self):
        lecture = _lecture(lecture_id="2", slug="2-aula")
        previous_lecture = Lecture(
            lecture_id="1",
            title="Aula Anterior",
            slug="1-aula",
            relative_path="2026/1-semestre/d/t/1-aula",
            materials=(),
            updated_at=_NOW,
        )
        next_lecture = Lecture(
            lecture_id="3",
            title="Próxima Aula",
            slug="3-aula",
            relative_path="2026/1-semestre/d/t/3-aula",
            materials=(),
            updated_at=_NOW,
        )

        html = render_lecture(lecture, _navigation((), previous_lecture, next_lecture))

        assert 'aria-label="Aula anterior: Aula Anterior"' in html
        assert 'aria-label="Próxima aula: Próxima Aula"' in html
        assert "../../../../../2026/1-semestre/d/t/1-aula/index.html" in html

    def test_no_nav_rendered_when_there_are_no_neighbors(self):
        lecture = _lecture()

        html = render_lecture(lecture, _navigation())

        assert "lecture-nav" not in html


class TestRenderLectureMaterialCards:
    def test_a_pdf_md_twin_renders_one_card_with_both_actions(self):
        lecture = _lecture(
            materials=(
                _material("apostila_completa.pdf", "pdf"),
                _material("apostila_completa.md", "texto"),
            )
        )

        html = render_lecture(
            lecture,
            _navigation(),
            reading_pages={"apostila_completa.md": "apostila_completa.html"},
        )

        assert html.count('id="card-1-apostila_completa"') == 1
        assert 'class="action-pdf" href="apostila_completa.pdf"' in html
        assert 'class="action-read" href="apostila_completa.html"' in html

    def test_a_markdown_material_without_a_reading_page_falls_back_to_download(self):
        """Regression: the generator must always produce a reading page for
        a .md material, but the renderer stays a pure function of what it
        is given -- if no page exists for it, offer the file instead of a
        link to nothing."""
        lecture = _lecture(materials=(_material("roteiro_revisao.md", "texto"),))

        html = render_lecture(lecture, _navigation())

        assert "action-read" not in html
        assert 'class="download dl-md" href="roteiro_revisao.md" download' in html

    def test_download_link_has_a_material_specific_aria_label(self):
        lecture = _lecture(materials=(_material("roteiro_revisao.csv", "texto"),))

        html = render_lecture(lecture, _navigation())

        assert 'aria-label="Baixar label-roteiro_revisao.csv em CSV"' in html

    def test_the_reading_link_names_the_material(self):
        lecture = _lecture(materials=(_material("roteiro_revisao.md", "texto"),))

        html = render_lecture(
            lecture,
            _navigation(),
            reading_pages={"roteiro_revisao.md": "roteiro_revisao.html"},
        )

        assert (
            '<a class="action-read" href="roteiro_revisao.html">Ler label-roteiro_revisao.md</a>'
            in html
        )

    def test_pdf_download_carries_no_download_attribute(self):
        """Deliberate: an in-app browser (WhatsApp on iOS) cannot always
        complete a forced file save, but it can open the platform's own PDF
        viewer -- which has its own way to save the file."""
        lecture = _lecture(materials=(_material("simulado.pdf", "pdf"),))

        html = render_lecture(lecture, _navigation())

        assert '<a class="action-pdf" href="simulado.pdf">Baixar PDF</a>' in html

    def test_csv_bearing_card_exposes_three_separate_actions(self):
        lecture = _lecture(
            materials=(
                _material("flashcards.pdf", "pdf"),
                _material("flashcards.md", "texto"),
                _material("flashcards.csv", "texto"),
            )
        )

        html = render_lecture(
            lecture, _navigation(), reading_pages={"flashcards.md": "flashcards.html"}
        )

        assert 'class="action-pdf" href="flashcards.pdf"' in html
        assert 'class="action-read" href="flashcards.html"' in html
        assert "Baixar CSV" in html

    def test_simulado_card_links_to_the_interactive_quiz_not_a_reading_page(self):
        """The simulado card has no passive "Ler" view (SITE-09, ADR 0041):
        only the interactive quiz route and the PDF download."""
        lecture = _lecture(
            lecture_id="10015",
            materials=(
                _material("simulado.md", "texto"),
                _material("simulado.pdf", "pdf"),
            ),
        )

        html = render_lecture(
            lecture,
            _navigation(),
            reading_pages={"simulado.md": "simulado.html"},
            has_interactive_quiz=True,
        )

        assert (
            '<a class="action-quiz" href="/simulado/10015">Responder simulado</a>'
            in html
        )
        assert '<a class="action-pdf" href="simulado.pdf">Baixar PDF</a>' in html
        assert "action-read" not in html
        assert "simulado.html" not in html

    def test_simulado_card_omits_the_quiz_link_when_no_exam_json_exists(self):
        """A published simulado.md/simulado.pdf pair without a matching
        compiled exam.json (a rejected or not-yet-run exam-compilation
        stage) must never advertise a quiz route that would 404 for every
        student who clicks it -- the card falls back to the PDF alone."""
        lecture = _lecture(
            lecture_id="10015",
            materials=(
                _material("simulado.md", "texto"),
                _material("simulado.pdf", "pdf"),
            ),
        )

        html = render_lecture(
            lecture,
            _navigation(),
            reading_pages={"simulado.md": "simulado.html"},
            has_interactive_quiz=False,
        )

        assert "action-quiz" not in html
        assert '<a class="action-pdf" href="simulado.pdf">Baixar PDF</a>' in html

    def test_podcast_card_has_an_inline_audio_player_and_a_roteiro_subsection(self):
        lecture = _lecture(
            materials=(
                _material("podcast_revisao.m4a", "audio"),
                _material("podcast_roteiro.pdf", "pdf"),
            )
        )

        html = render_lecture(lecture, _navigation())

        assert "<audio controls" in html
        assert "Roteiro" in html

    def test_an_uncarded_material_still_appears_in_a_fallback_table(self):
        lecture = _lecture(materials=(_material("nota_do_professor.md", "texto"),))

        html = render_lecture(lecture, _navigation())

        assert "Outros materiais" in html
        assert "label-nota_do_professor.md" in html

    def test_an_embedded_subdirectory_image_is_never_listed_as_a_material(
        self,
    ):
        # Figures are published so markdown-preview references resolve,
        # but a real lecture carries dozens of slide frames -- listing
        # them would bury the fallback table.
        lecture = _lecture(
            materials=(
                _material("figuras/frame_0001.000.png", "image"),
                _material("nota_do_professor.md", "texto"),
            )
        )

        html = render_lecture(lecture, _navigation())

        assert "frame_0001" not in html
        assert "label-nota_do_professor.md" in html

    def test_a_standalone_root_level_image_is_still_listed(self):
        lecture = _lecture(materials=(_material("mapa_diagrama.png", "image"),))

        html = render_lecture(lecture, _navigation())

        assert "label-mapa_diagrama.png" in html

    def test_video_only_lecture_shows_a_notice_instead_of_an_empty_grid(self):
        lecture = _lecture(materials=(_material("video/aula.mp4", "video"),))

        html = render_lecture(lecture, _navigation())

        assert "Vídeo sem material de estudo gerado" in html
        assert 'class="card-grid"' not in html

    def test_video_section_is_omitted_when_there_is_no_video(self):
        lecture = _lecture(materials=(_material("simulado.pdf", "pdf"),))

        html = render_lecture(lecture, _navigation())

        assert 'class="watch"' not in html

    def test_video_section_renders_when_present(self):
        lecture = _lecture(materials=(_material("video/aula.mp4", "video"),))

        html = render_lecture(lecture, _navigation())

        assert 'class="watch"' in html
        assert "<video controls" in html

    def test_video_section_links_to_the_dedicated_watch_page(self):
        lecture = _lecture(materials=(_material("video/aula.mp4", "video"),))

        html = render_lecture(lecture, _navigation())

        assert 'href="assistir.html"' in html

    def test_video_section_offers_a_download_of_the_video(self):
        lecture = _lecture(materials=(_material("video/aula.mp4", "video"),))

        html = render_lecture(lecture, _navigation())

        assert (
            '<a class="watch-download" '
            'href="/videos/2026/1-semestre/d/t/1-aula/video/aula.mp4" download>'
            "Baixar vídeo</a>"
        ) in html


class TestRenderWatchPage:
    def test_offers_a_download_of_the_video(self):
        lecture = _lecture(materials=(_material("video/aula.mp4", "video"),))

        html = render_watch_page(lecture)

        assert 'class="watch-download"' in html
        assert 'video/aula.mp4" download>' in html

    def test_renders_the_video_with_its_streaming_route(self):
        lecture = _lecture(materials=(_material("video/aula.mp4", "video"),))

        html = render_watch_page(lecture)

        video_url = "/videos/2026/1-semestre/d/t/1-aula/video/aula.mp4"
        assert f'src="{video_url}"' in html
        assert "<video controls" in html

    def test_title_and_back_link_reference_the_lecture(self):
        lecture = _lecture(materials=(_material("video/aula.mp4", "video"),))

        html = render_watch_page(lecture)

        assert "<title>Assistir: Aula de Teste" in html
        assert 'href="index.html"' in html

    def test_stylesheet_link_resolves_from_the_lecture_depth(self):
        lecture = _lecture(materials=(_material("video/aula.mp4", "video"),))

        html = render_watch_page(lecture)

        assert '<link rel="stylesheet" href="../../../../../estilo.css">' in html

    def test_raises_when_the_lecture_has_no_video(self):
        lecture = _lecture(materials=(_material("simulado.pdf", "pdf"),))

        with pytest.raises(ValueError):
            render_watch_page(lecture)
