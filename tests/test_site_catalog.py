"""Site catalog reader tests: the published tree is the only source of
truth (SITE-01), and a lecture with no bundle yet is reported, not hidden."""

import os
import time
from pathlib import Path

import pytest

from lecture_study.site.catalog import (
    pending_lectures,
    previous_and_next,
    read_catalog,
    recent_lectures,
    search_index,
)


def _write_lecture(
    root: Path,
    year: str,
    semester: str,
    discipline: str,
    topic: str,
    lecture_slug: str,
    files: dict[str, bytes],
) -> Path:
    lecture_dir = root / year / semester / discipline / topic / lecture_slug
    lecture_dir.mkdir(parents=True)
    for relative_path, content in files.items():
        path = lecture_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return lecture_dir


class TestReadCatalog:
    def test_absent_root_yields_an_empty_catalog(self, tmp_path: Path):
        catalog = read_catalog(tmp_path / "does-not-exist")

        assert catalog.years == ()
        assert catalog.lectures == ()

    def test_reads_one_lecture_with_its_materials(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "disciplina-a",
            "topico-3",
            "10012-neoplasias-mamarias",
            {
                "apostila_completa.pdf": b"pdf-bytes",
                "simulado.md": b"# simulado",
                "video/aula.mp4": b"video-bytes",
            },
        )

        catalog = read_catalog(tmp_path)

        assert len(catalog.lectures) == 1
        lecture = catalog.lectures[0]
        assert lecture.lecture_id == "10012"
        assert lecture.title == "Neoplasias Mamarias"
        assert lecture.relative_path == (
            "2026/1-semestre/disciplina-a/topico-3/10012-neoplasias-mamarias"
        )
        assert {material.relative_path for material in lecture.materials} == {
            "apostila_completa.pdf",
            "simulado.md",
            "video/aula.mp4",
        }

    def test_video_material_is_typed_and_labeled(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {"video/aula.mp4": b"x" * 1000},
        )

        lecture = read_catalog(tmp_path).lectures[0]

        video = lecture.video
        assert video is not None
        assert video.kind == "video"
        assert video.label == "Vídeo da aula"
        assert video.byte_size == 1000

    def test_known_material_names_get_a_pt_br_label(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {"apostila_completa.pdf": b"x"},
        )

        material = read_catalog(tmp_path).lectures[0].materials[0]

        assert material.label == "Apostila completa (PDF)"

    def test_a_recognized_but_unlabeled_extension_is_listed_under_its_file_name(
        self, tmp_path: Path
    ):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {"nota_do_professor.md": b"x"},
        )

        material = read_catalog(tmp_path).lectures[0].materials[0]

        assert material.label == "nota_do_professor.md"

    def test_pipeline_internal_files_never_reach_the_catalog(self, tmp_path: Path):
        """`.typ` is the pipeline's own Typst source for the .pdf sitting
        beside it, and `.bundle.json` is internal delivery bookkeeping --
        neither is a material a student asked for."""
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {
                "apostila_completa.pdf": b"x",
                "apostila_completa.typ": b"typst source",
                ".bundle.json": b"{}",
            },
        )

        lecture = read_catalog(tmp_path).lectures[0]

        assert {material.relative_path for material in lecture.materials} == {
            "apostila_completa.pdf"
        }

    def test_a_lecture_directory_with_no_files_is_dropped(self, tmp_path: Path):
        empty_dir = tmp_path / "2026" / "1-semestre" / "d" / "t" / "1-aula"
        empty_dir.mkdir(parents=True)

        assert read_catalog(tmp_path).lectures == ()

    def test_has_study_guide_reflects_the_full_apostila(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {"simulado.md": b"x"},
        )

        assert read_catalog(tmp_path).lectures[0].has_study_guide is False


class TestPendingLectures:
    def test_a_lecture_id_not_in_the_catalog_is_reported_pending(self, tmp_path: Path):
        _write_lecture(
            tmp_path, "2026", "1-semestre", "d", "t", "1-aula", {"simulado.md": b"x"}
        )
        catalog = read_catalog(tmp_path)

        pending = pending_lectures(catalog, ["1", "99999"])

        assert pending == ("99999",)

    def test_every_published_id_is_never_pending(self, tmp_path: Path):
        _write_lecture(
            tmp_path, "2026", "1-semestre", "d", "t", "1-aula", {"simulado.md": b"x"}
        )
        catalog = read_catalog(tmp_path)

        assert pending_lectures(catalog, ["1"]) == ()


class TestUpdatedAt:
    def test_reflects_the_most_recent_file_mtime_in_the_bundle(self, tmp_path: Path):
        lecture_dir = _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {"apostila_completa.pdf": b"x", "simulado.md": b"y"},
        )
        older = time.time() - 3600
        newer = time.time()
        os.utime(lecture_dir / "apostila_completa.pdf", (older, older))
        os.utime(lecture_dir / "simulado.md", (newer, newer))

        lecture = read_catalog(tmp_path).lectures[0]

        assert lecture.updated_at.timestamp() == pytest.approx(newer)


class TestRecentLectures:
    def test_orders_lectures_across_the_whole_catalog_newest_first(
        self, tmp_path: Path
    ):
        old_dir = _write_lecture(
            tmp_path, "2026", "1-semestre", "d", "t", "1-antiga", {"simulado.md": b"x"}
        )
        new_dir = _write_lecture(
            tmp_path, "2026", "1-semestre", "d", "t", "2-recente", {"simulado.md": b"x"}
        )
        old_time = time.time() - 3600
        new_time = time.time()
        os.utime(old_dir / "simulado.md", (old_time, old_time))
        os.utime(new_dir / "simulado.md", (new_time, new_time))
        catalog = read_catalog(tmp_path)

        recent = recent_lectures(catalog)

        assert [lecture.lecture_id for lecture in recent] == ["2", "1"]

    def test_caps_results_at_limit(self, tmp_path: Path):
        for index in range(3):
            _write_lecture(
                tmp_path,
                "2026",
                "1-semestre",
                "d",
                "t",
                f"{index}-aula",
                {"simulado.md": b"x"},
            )
        catalog = read_catalog(tmp_path)

        assert len(recent_lectures(catalog, limit=2)) == 2


class TestPreviousAndNext:
    def test_middle_lecture_has_both_neighbors(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-primeira",
            {"simulado.md": b"x"},
        )
        _write_lecture(
            tmp_path, "2026", "1-semestre", "d", "t", "2-meio", {"simulado.md": b"x"}
        )
        _write_lecture(
            tmp_path, "2026", "1-semestre", "d", "t", "3-ultima", {"simulado.md": b"x"}
        )
        topic = read_catalog(tmp_path).years[0].semesters[0].disciplines[0].topics[0]

        previous, following = previous_and_next(topic, "2")

        assert previous is not None and previous.lecture_id == "1"
        assert following is not None and following.lecture_id == "3"

    def test_first_lecture_has_no_previous(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-primeira",
            {"simulado.md": b"x"},
        )
        _write_lecture(
            tmp_path, "2026", "1-semestre", "d", "t", "2-meio", {"simulado.md": b"x"}
        )
        topic = read_catalog(tmp_path).years[0].semesters[0].disciplines[0].topics[0]

        previous, following = previous_and_next(topic, "1")

        assert previous is None
        assert following is not None and following.lecture_id == "2"

    def test_last_lecture_has_no_next(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-primeira",
            {"simulado.md": b"x"},
        )
        topic = read_catalog(tmp_path).years[0].semesters[0].disciplines[0].topics[0]

        previous, following = previous_and_next(topic, "1")

        assert previous is None
        assert following is None

    def test_unknown_lecture_id_yields_no_neighbors(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-primeira",
            {"simulado.md": b"x"},
        )
        topic = read_catalog(tmp_path).years[0].semesters[0].disciplines[0].topics[0]

        assert previous_and_next(topic, "99999") == (None, None)


class TestSearchIndex:
    def test_one_entry_per_lecture_with_its_hierarchy_and_url(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "disciplina-a",
            "topico-3",
            "10012-neoplasias-mamarias",
            {"apostila_completa.pdf": b"x"},
        )
        catalog = read_catalog(tmp_path)

        entries = search_index(catalog)

        assert entries == [
            {
                "id": "10012",
                "title": "Neoplasias Mamarias",
                "discipline": "Disciplina a",
                "topic": "Topico 3",
                "url": (
                    "2026/1-semestre/disciplina-a/topico-3/"
                    "10012-neoplasias-mamarias/index.html"
                ),
            }
        ]

    def test_empty_catalog_yields_an_empty_index(self, tmp_path: Path):
        catalog = read_catalog(tmp_path / "does-not-exist")

        assert search_index(catalog) == []


class TestMaterialCards:
    def test_figures_are_published_as_embedded_images_never_cards(self, tmp_path: Path):
        # Regression (2026-09-16): figuras/*.png were not publishable, so
        # the 27 markdown-preview references in a real lecture's pages
        # pointed at files the site never shipped. They must be discovered
        # (and copied by the generator) as image-kind materials, with no
        # card and no "Outros materiais" row of their own.
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {
                "apostila_completa.md": b"# apostila",
                "figuras/frame_0001.000.png": b"png-bytes",
            },
        )

        lecture = read_catalog(tmp_path).lectures[0]

        figure = next(
            material
            for material in lecture.materials
            if material.relative_path == "figuras/frame_0001.000.png"
        )
        assert figure.kind == "image"
        carded_paths = {
            material.relative_path
            for card in lecture.material_cards
            for material in card.formats
        }
        assert "figuras/frame_0001.000.png" not in carded_paths

    def test_a_pdf_md_twin_collapses_into_one_card(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {"apostila_completa.pdf": b"x", "apostila_completa.md": b"y"},
        )

        lecture = read_catalog(tmp_path).lectures[0]

        assert len(lecture.material_cards) == 1
        card = lecture.material_cards[0]
        assert card.kind == "apostila_completa"
        assert card.label == "Apostila completa"
        assert [material.relative_path for material in card.formats] == [
            "apostila_completa.pdf",
            "apostila_completa.md",
        ]

    def test_a_csv_bearing_card_orders_preview_formats_before_the_csv(
        self, tmp_path: Path
    ):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {
                "flashcards.csv": b"x",
                "flashcards.pdf": b"y",
                "flashcards.md": b"z",
            },
        )

        card = read_catalog(tmp_path).lectures[0].material_cards[0]

        assert card.kind == "flashcards"
        assert [material.relative_path for material in card.formats] == [
            "flashcards.pdf",
            "flashcards.md",
            "flashcards.csv",
        ]

    def test_podcast_audio_and_transcript_share_one_card_audio_first(
        self, tmp_path: Path
    ):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {
                "podcast_roteiro.pdf": b"x",
                "podcast_revisao.m4a": b"y",
                "podcast_roteiro.md": b"z",
            },
        )

        lecture = read_catalog(tmp_path).lectures[0]

        assert len(lecture.material_cards) == 1
        card = lecture.material_cards[0]
        assert card.kind == "podcast"
        assert card.label == "Podcast de revisão"
        assert [material.relative_path for material in card.formats] == [
            "podcast_revisao.m4a",
            "podcast_roteiro.pdf",
            "podcast_roteiro.md",
        ]

    def test_cards_render_in_the_briefs_display_order(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {
                "simulado.pdf": b"x",
                "apostila_completa.pdf": b"y",
                "flashcards.pdf": b"z",
            },
        )

        lecture = read_catalog(tmp_path).lectures[0]

        assert [card.kind for card in lecture.material_cards] == [
            "apostila_completa",
            "simulado",
            "flashcards",
        ]

    def test_video_and_unrecognized_files_get_no_card(self, tmp_path: Path):
        _write_lecture(
            tmp_path,
            "2026",
            "1-semestre",
            "d",
            "t",
            "1-aula",
            {"video/aula.mp4": b"x", "nota_do_professor.md": b"y"},
        )

        lecture = read_catalog(tmp_path).lectures[0]

        assert lecture.material_cards == ()
        assert len(lecture.materials) == 2
