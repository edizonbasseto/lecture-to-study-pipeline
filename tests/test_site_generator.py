"""Site generator tests: the output tree is a pure function of the
published catalog (SITE-01), republishing is idempotent, and video stays
out of the site tree (ADR 0029)."""

import shutil
from pathlib import Path

import pytest
from unittest import mock

from lecture_study.site.generator import SectionCollapseError, generate_site


def _write_lecture(root: Path, files: dict[str, bytes]) -> Path:
    lecture_dir = root / "2026" / "1-semestre" / "d" / "t" / "1-aula"
    lecture_dir.mkdir(parents=True)
    for relative_path, content in files.items():
        path = lecture_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return lecture_dir


class TestGenerateSite:
    def test_publishes_figures_referenced_by_markdown_previews(self, tmp_path: Path):
        # Regression (2026-09-16): figuras/*.png were not publishable, so
        # every markdown-preview image reference on a real lecture's page
        # 404'd. The copy half is pinned here, end to end, so a future
        # material-filter inside _write_lecture cannot silently drop
        # figures again while catalog tests stay green.
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "apostila_completa.md": b"![quadro](figuras/frame_0001.000.png)",
                "figuras/frame_0001.000.png": b"png-bytes",
            },
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        assert (
            output
            / "2026"
            / "1-semestre"
            / "d"
            / "t"
            / "1-aula"
            / "figuras"
            / "frame_0001.000.png"
        ).read_bytes() == b"png-bytes"

    def test_writes_the_index_and_stylesheet(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"apostila_completa.pdf": b"x"})

        generate_site((), study_guides, output, tmp_path / "videos")

        assert (output / "index.html").is_file()
        assert (output / "estilo.css").is_file()

    def test_writes_one_page_per_lecture_with_its_text_materials(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"apostila_completa.pdf": b"conteudo"})

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        assert (lecture_dir / "index.html").is_file()
        assert (lecture_dir / "apostila_completa.pdf").read_bytes() == b"conteudo"

    def test_video_is_written_to_the_video_library_never_to_the_site_tree(
        self, tmp_path: Path
    ):
        """ADR 0029: the site tree is what a container image build would
        copy in, so a lecture's video must never land there."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        video_library = tmp_path / "videos"
        _write_lecture(
            study_guides,
            {"apostila_completa.pdf": b"x", "video/aula.mp4": b"video-bytes"},
        )

        generate_site((), study_guides, output, video_library)

        lecture_relative_path = "2026/1-semestre/d/t/1-aula"
        assert (
            video_library / lecture_relative_path / "video" / "aula.mp4"
        ).read_bytes() == b"video-bytes"
        assert not (output / lecture_relative_path / "video").exists()

    def test_the_lecture_page_links_video_through_the_video_route(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"video/aula.mp4": b"video-bytes"})

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_page = (
            output / "2026" / "1-semestre" / "d" / "t" / "1-aula" / "index.html"
        ).read_text(encoding="utf-8")
        assert "/videos/2026/1-semestre/d/t/1-aula/video/aula.mp4" in lecture_page

    def test_a_lecture_with_video_gets_a_dedicated_watch_page(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"video/aula.mp4": b"video-bytes"})

        generate_site((), study_guides, output, tmp_path / "videos")

        watch_page = (
            output / "2026" / "1-semestre" / "d" / "t" / "1-aula" / "assistir.html"
        ).read_text(encoding="utf-8")
        assert "/videos/2026/1-semestre/d/t/1-aula/video/aula.mp4" in watch_page
        assert "<video controls" in watch_page

    def test_the_lecture_page_links_to_its_watch_page(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"video/aula.mp4": b"video-bytes"})

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_page = (
            output / "2026" / "1-semestre" / "d" / "t" / "1-aula" / "index.html"
        ).read_text(encoding="utf-8")
        assert 'class="watch"' in lecture_page
        assert "<video controls" in lecture_page
        assert 'href="assistir.html"' in lecture_page

    def test_no_watch_page_for_a_lecture_without_video(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"apostila_completa.pdf": b"x"})

        generate_site((), study_guides, output, tmp_path / "videos")

        assert not (
            output / "2026" / "1-semestre" / "d" / "t" / "1-aula" / "assistir.html"
        ).exists()

    def test_the_lecture_page_stylesheet_and_back_link_resolve_from_its_real_depth(
        self, tmp_path: Path
    ):
        """Regression: an earlier version hardcoded the link depth to 4,
        one level short of the real year/semester/discipline/topic/lecture
        nesting, breaking the stylesheet and the back link on every real
        lecture page."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"apostila_completa.pdf": b"x"})

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_page = (
            output / "2026" / "1-semestre" / "d" / "t" / "1-aula" / "index.html"
        ).read_text(encoding="utf-8")
        assert 'href="../../../../../estilo.css"' in lecture_page
        assert 'href="../../../../../index.html#2026-1-semestre-d-t"' in lecture_page
        assert (output / "estilo.css").is_file()

    def test_a_pending_lecture_id_is_named_on_the_index(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"apostila_completa.pdf": b"x"})

        generate_site(("99999",), study_guides, output, tmp_path / "videos")

        assert "99999" in (output / "index.html").read_text(encoding="utf-8")

    def test_regenerating_replaces_stale_output(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        output.mkdir(parents=True)
        (output / "leftover.html").write_text("velho")
        _write_lecture(study_guides, {"apostila_completa.pdf": b"x"})

        generate_site((), study_guides, output, tmp_path / "videos")

        assert not (output / "leftover.html").exists()

    def test_regenerating_a_mount_point_that_cannot_be_removed_still_works(
        self, tmp_path: Path
    ):
        """Regression: output_dir and video_library_dir are mount points
        (a Docker volume, a host volume) in the served container, and
        an earlier version tried to `rmtree` the directory itself, which
        fails with "Device or resource busy" on a real mount point."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        output.mkdir(parents=True)
        (output / "leftover.html").write_text("velho")
        real_rmtree = shutil.rmtree

        def rmtree_that_refuses_the_mount_point(path, *args, **kwargs):
            if Path(path) == output:
                raise OSError(16, "Device or resource busy")
            return real_rmtree(path, *args, **kwargs)

        _write_lecture(study_guides, {"apostila_completa.pdf": b"x"})

        with mock.patch(
            "shutil.rmtree", side_effect=rmtree_that_refuses_the_mount_point
        ):
            generate_site((), study_guides, output, tmp_path / "videos")

        assert not (output / "leftover.html").exists()
        assert (output / "index.html").is_file()

    def test_material_content_matches_the_source_after_regeneration(
        self, tmp_path: Path
    ):
        """A hard link and a fallback copy must both produce byte-identical
        output on a second run against changed source content."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        video_library = tmp_path / "videos"
        _write_lecture(study_guides, {"apostila_completa.pdf": b"versao-1"})
        generate_site((), study_guides, output, video_library)

        (study_guides / "2026/1-semestre/d/t/1-aula/apostila_completa.pdf").write_bytes(
            b"versao-2"
        )
        generate_site((), study_guides, output, video_library)

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        assert (lecture_dir / "apostila_completa.pdf").read_bytes() == b"versao-2"

    def test_a_symlinked_file_is_never_published(self, tmp_path: Path):
        """A symlink inside a lecture directory could point anywhere on
        disk; the catalog must never publish what it resolves to."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        secret = tmp_path / "secret.pdf"
        secret.write_bytes(b"not for students")
        lecture_dir = _write_lecture(study_guides, {"apostila_completa.pdf": b"x"})
        (lecture_dir / "leaked.pdf").symlink_to(secret)

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir_out = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        assert not (lecture_dir_out / "leaked.pdf").exists()


class TestReadingPages:
    def test_writes_a_reading_page_per_markdown_material(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "apostila_completa.md": b"# Titulo\n\nCorpo do texto.",
                "apostila_completa.pdf": b"x",
            },
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "apostila_completa.html").read_text(encoding="utf-8")
        assert "<h1>Titulo</h1>" in page
        assert "Corpo do texto." in page

    def test_the_reading_page_keeps_figure_references_resolvable(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "apostila_completa.md": b"![quadro](figuras/frame_0001.000.png)",
                "figuras/frame_0001.000.png": b"png-bytes",
            },
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "apostila_completa.html").read_text(encoding="utf-8")
        assert 'src="figuras/frame_0001.000.png"' in page
        assert (
            lecture_dir / "figuras" / "frame_0001.000.png"
        ).read_bytes() == b"png-bytes"

    def test_no_reading_page_for_a_pdf_only_card(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"simulado.pdf": b"x"})

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        assert not (lecture_dir / "simulado.html").exists()

    def test_the_reading_page_offers_the_sibling_pdf_download(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {"apostila_completa.md": b"corpo", "apostila_completa.pdf": b"x"},
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "apostila_completa.html").read_text(encoding="utf-8")
        assert 'href="apostila_completa.pdf"' in page

    def test_headings_get_anchors_and_a_table_of_contents(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {"apostila_completa.md": b"# Titulo\n\n## Primeira unidade\n\ntexto"},
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "apostila_completa.html").read_text(encoding="utf-8")
        assert 'id="primeira-unidade"' in page
        assert 'href="#primeira-unidade"' in page

    def test_the_lecture_page_links_the_reading_page(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {"apostila_completa.md": b"corpo", "apostila_completa.pdf": b"x"},
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        index_page = (lecture_dir / "index.html").read_text(encoding="utf-8")
        assert 'href="apostila_completa.html"' in index_page

    def test_regenerating_replaces_a_stale_reading_page(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"apostila_completa.md": b"# Um\n\nversao 1"})
        generate_site((), study_guides, output, tmp_path / "videos")

        (study_guides / "2026/1-semestre/d/t/1-aula/apostila_completa.md").write_bytes(
            b"# Um\n\nversao 2"
        )
        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "apostila_completa.html").read_text(encoding="utf-8")
        assert "versao 2" in page
        assert "versao 1" not in page

    def test_the_simulado_answer_key_is_collapsed_behind_a_disclosure(
        self, tmp_path: Path
    ):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "simulado.md": (
                    b"## Questoes\n\n1. pergunta?\n\n"
                    b"## Gabarito comentado\n\nResposta: A"
                )
            },
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "simulado.html").read_text(encoding="utf-8")
        assert '<details class="reveal"><summary>Gabarito comentado</summary>' in page
        assert "Resposta: A" in page
        assert "pergunta?" in page.split("Gabarito comentado</summary>")[0]

    def test_pending_lectures_split_by_whether_the_schedule_dated_them(
        self, tmp_path: Path
    ):
        """Regression (2026-09-18): the home page's "Em processamento"
        bucket used to lump a lecture with a confirmed schedule date
        together with one the official schedule never dated at all --
        telling a student the latter's material was "sendo gerado" when
        it may have no publication date in sight. The two ids must land
        in their own distinct sections."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        schedule_path = tmp_path / "academic-calendar.local.yaml"
        schedule_path.write_text(
            "entries:\n- {lecture_id: '11111', scheduled_date: 2026-03-10}\n",
            encoding="utf-8",
        )

        generate_site(
            ("11111", "22222"),
            study_guides,
            output,
            tmp_path / "videos",
            academic_schedule_path=schedule_path,
        )

        index_html = (output / "index.html").read_text(encoding="utf-8")
        pending_section, _, unscheduled_section = index_html.partition(
            '<section class="pending unscheduled">'
        )
        assert "11111" in pending_section
        assert "22222" not in pending_section
        assert "22222" in unscheduled_section

    def test_missing_schedule_file_treats_every_pending_lecture_as_unscheduled(
        self, tmp_path: Path
    ):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"

        generate_site(
            ("11111",),
            study_guides,
            output,
            tmp_path / "videos",
            academic_schedule_path=tmp_path / "no-such-file.yaml",
        )

        index_html = (output / "index.html").read_text(encoding="utf-8")
        assert '<section class="pending">' not in index_html
        assert '<section class="pending unscheduled">' in index_html
        assert "11111" in index_html

    def test_simulado_card_links_to_the_quiz_only_when_exam_json_exists(
        self, tmp_path: Path
    ):
        """A published simulado without a matching compiled exam.json
        (a rejected or not-yet-run exam-compilation stage) must not
        advertise a quiz link the running site could never satisfy."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        mock_exams = tmp_path / "mock-exams"
        _write_lecture(
            study_guides,
            {"simulado.md": b"## Questoes\n\npergunta\n\n## Gabarito comentado\n\nA"},
        )
        (mock_exams / "1").mkdir(parents=True)
        (mock_exams / "1" / "exam.json").write_text("{}", encoding="utf-8")

        generate_site(
            (), study_guides, output, tmp_path / "videos", mock_exams_dir=mock_exams
        )

        lecture_page = (
            output / "2026" / "1-semestre" / "d" / "t" / "1-aula" / "index.html"
        ).read_text(encoding="utf-8")
        assert 'href="/simulado/1"' in lecture_page

    def test_simulado_card_omits_the_quiz_link_without_a_compiled_exam(
        self, tmp_path: Path
    ):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {"simulado.md": b"## Questoes\n\npergunta\n\n## Gabarito comentado\n\nA"},
        )

        generate_site(
            (),
            study_guides,
            output,
            tmp_path / "videos",
            mock_exams_dir=tmp_path / "no-mock-exams",
        )

        lecture_page = (
            output / "2026" / "1-semestre" / "d" / "t" / "1-aula" / "index.html"
        ).read_text(encoding="utf-8")
        assert "action-quiz" not in lecture_page

    def test_the_collapse_ignores_a_decoy_paragraph_quoting_the_section_name(
        self, tmp_path: Path
    ):
        """The section must be located by walking the parsed heading
        structure, never by searching the rendered HTML for its name: a
        stray paragraph that merely quotes "Gabarito comentado" (plausible
        pipeline output) must not be mistaken for the heading itself."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "simulado.md": (
                    b"## Questoes\n\n1. pergunta real?\n\n"
                    b"Gabarito comentado\n\n"
                    b"## Gabarito comentado\n\nResposta real: A"
                )
            },
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "simulado.html").read_text(encoding="utf-8")
        before_disclosure = page.split('<details class="reveal"', 1)[0]
        assert "pergunta real?" in before_disclosure
        assert "Resposta real: A" not in before_disclosure
        assert "Resposta real: A" in page

    def test_the_collapse_recognizes_an_emphasized_heading(self, tmp_path: Path):
        """The heading's markdown-rendered emphasis (**bold**) must not
        stop it from matching -- the comparison is against its plain
        reading text, not its raw markdown source."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {"simulado.md": b"## **Gabarito comentado**\n\nResposta: A"},
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "simulado.html").read_text(encoding="utf-8")
        assert '<details class="reveal"><summary>Gabarito comentado</summary>' in page

    def test_a_missing_collapse_heading_fails_the_build_rather_than_exposing_it(
        self, tmp_path: Path
    ):
        """Fail loud, never fall back to leaving the section exposed: a
        simulado.md missing its "Gabarito comentado" heading (a plausible
        pipeline drift, same as the heading being duplicated below) must
        stop the build, not ship the answer key uncollapsed."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(study_guides, {"simulado.md": b"## Questoes\n\n1. pergunta?"})

        with pytest.raises(SectionCollapseError):
            generate_site((), study_guides, output, tmp_path / "videos")

    def test_a_duplicated_collapse_heading_fails_the_build_rather_than_exposing_it(
        self, tmp_path: Path
    ):
        """Regression: matching only the first "Gabarito comentado" left a
        second, real one exposed in the open when the pipeline emitted the
        heading twice -- exactly the wrong-answer-shown failure this
        mechanism exists to prevent."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "simulado.md": (
                    b"## Gabarito comentado\n\nResposta falsa\n\n"
                    b"## Gabarito comentado\n\nResposta real: A"
                )
            },
        )

        with pytest.raises(SectionCollapseError):
            generate_site((), study_guides, output, tmp_path / "videos")

    def test_a_collapse_heading_duplicated_at_a_different_level_still_fails_the_build(
        self, tmp_path: Path
    ):
        """Regression: counting duplicates only among h2 headings let an h3
        with the exact same text slip through uncounted -- the decoy
        rendered in the open, before the real, correctly collapsed h2
        section."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "simulado.md": (
                    b"## Questoes\n\n1. pergunta real?\n\n"
                    b"### Gabarito comentado\n\nResposta vazada: A\n\n"
                    b"## Gabarito comentado\n\nResposta real: A"
                )
            },
        )

        with pytest.raises(SectionCollapseError):
            generate_site((), study_guides, output, tmp_path / "videos")

    @pytest.mark.parametrize(
        "decoy_markup", ["# Gabarito comentado", "#### Gabarito comentado"]
    )
    def test_a_collapse_heading_duplicated_at_any_level_fails_the_build(
        self, tmp_path: Path, decoy_markup: str
    ):
        """Regression: restricting the ambiguity count to an allowlist of
        levels (h2-only, then h2+h3) was tried twice and both times a
        duplicate one level outside the list slipped through uncounted.
        Parametrized over h1 and h4 -- the two levels immediately outside
        the table of contents' own h2/h3 range -- to close the class."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "simulado.md": (
                    f"## Questoes\n\n1. pergunta real?\n\n"
                    f"{decoy_markup}\n\nResposta vazada: A\n\n"
                    f"## Gabarito comentado\n\nResposta real: A"
                ).encode()
            },
        )

        with pytest.raises(SectionCollapseError):
            generate_site((), study_guides, output, tmp_path / "videos")

    def test_duplicate_headings_get_distinct_anchors(self, tmp_path: Path):
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "apostila_completa.md": (
                    b"## Complicacoes\n\ntexto um\n\n## Complicacoes\n\ntexto dois"
                )
            },
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        page = (lecture_dir / "apostila_completa.html").read_text(encoding="utf-8")
        assert 'id="complicacoes"' in page
        assert 'id="complicacoes-2"' in page
        assert page.count('href="#complicacoes"') == 1
        assert page.count('href="#complicacoes-2"') == 1

    def test_material_navigation_follows_the_pedagogical_card_order(
        self, tmp_path: Path
    ):
        """Regression: ordering the reading pages by `lecture.materials`
        left "next material" alphabetical (flashcards before simulado),
        skipping the cards a reader would expect between them."""
        study_guides = tmp_path / "study-guides"
        output = tmp_path / "site"
        _write_lecture(
            study_guides,
            {
                "flashcards.md": b"## Perguntas\n\npergunta\n\n## Respostas\n\nresposta",
                "apostila_completa.md": b"corpo",
                "simulado.md": b"## Questoes\n\npergunta\n\n## Gabarito comentado\n\nresposta",
            },
        )

        generate_site((), study_guides, output, tmp_path / "videos")

        lecture_dir = output / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        apostila_page = (lecture_dir / "apostila_completa.html").read_text(
            encoding="utf-8"
        )
        simulado_page = (lecture_dir / "simulado.html").read_text(encoding="utf-8")
        assert 'href="simulado.html"' in apostila_page
        assert 'href="flashcards.html"' in simulado_page
