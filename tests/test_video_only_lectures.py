"""Video-only local lectures (ADR 0043): kind, gates, compile and site ingestion."""

from datetime import date
import importlib.util
from pathlib import Path
import sys

import pytest
import yaml

from lecture_study.site.catalog import read_catalog
from lecture_study.site.generator import generate_site
from lecture_study.study_materials import local_lectures
from lecture_study.study_materials.local_lectures import (
    LocalLecture,
    load_local_lectures,
)
from lecture_study.study_materials.video_only_bundle import VIDEO_ONLY_NOTICE

SCRIPTS = Path(__file__).parent.parent / "scripts"
CLIP_ID = "925101"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _clip(lecture_id: str = CLIP_ID, **overrides) -> LocalLecture:
    fields = {
        "lecture_id": lecture_id,
        "title": "Bailarina",
        "discipline": "Disciplina B",
        "section": "Tópico 1",
        "scheduled_date": date(2025, 1, 1),
        "kind": "video-only",
    }
    return LocalLecture(**{**fields, **overrides})


def test_kind_defaults_to_study_for_rows_without_the_field(tmp_path: Path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(
        "lectures:\n- lecture_id: '925209'\n  title: T\n  discipline: D\n"
        "  section: S\n  scheduled_date: 2025-07-01\n"
    )

    assert load_local_lectures(path)[0].kind == "study"


def test_unknown_kind_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(
        "lectures:\n- lecture_id: '925209'\n  title: T\n  discipline: D\n"
        "  section: S\n  scheduled_date: 2025-07-01\n  kind: podcast\n"
    )

    with pytest.raises(local_lectures.LocalLectureError):
        load_local_lectures(path)


def test_exam_channel_withholds_published_video_only_but_not_published_study() -> None:
    lectures = [
        _clip(publish=True),
        _clip("925102", kind="study", publish=True),
    ]

    exam_withheld = local_lectures.withheld_exam_lecture_ids(lectures)
    guide_withheld = local_lectures.withheld_lecture_ids(lectures)

    assert CLIP_ID in exam_withheld
    assert "925102" not in exam_withheld
    assert CLIP_ID not in guide_withheld


def test_list_withheld_script_keeps_video_only_off_the_exam_channel(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _load_script("list_withheld_lecture_ids")
    metadata = tmp_path / "local.yaml"
    metadata.write_text(
        yaml.safe_dump({"lectures": [_clip(publish=True).model_dump(mode="json")]})
    )

    for channel, expected in (("exams", True), ("guides", False)):
        monkeypatch.setattr(
            "sys.argv", ["x", channel, "--local-lectures-path", str(metadata)]
        )
        module.main()
        listed = capsys.readouterr().out
        assert (f"/{CLIP_ID}/" in listed or f"{CLIP_ID}-*/" in listed) is expected


def test_cli_registers_video_only_kind(tmp_path: Path, monkeypatch) -> None:
    module = _load_script("register_local_lecture")
    targets, schedule = tmp_path / "t.yaml", tmp_path / "s.yaml"
    targets.write_text(yaml.safe_dump({"disciplines": []}))
    schedule.write_text(yaml.safe_dump({"entries": []}))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    metadata = tmp_path / "cfg" / "local.yaml"
    monkeypatch.setattr(
        "sys.argv",
        [
            "register",
            CLIP_ID,
            str(video),
            "--title",
            "Bailarina",
            "--discipline",
            "Disciplina B",
            "--semester",
            "2025.1",
            "--targets-path",
            str(targets),
            "--schedule-path",
            str(schedule),
            "--local-lectures-path",
            str(metadata),
            "--video-only",
            "--publish",
        ],
    )
    captured: list[LocalLecture] = []
    monkeypatch.setattr(
        module,
        "register_local_lecture",
        lambda lecture, *_a, **_k: captured.append(lecture) or Path("raw"),
    )

    assert module.main() == 0

    assert captured[0].kind == "video-only" and captured[0].publish is True


@pytest.fixture
def compile_environment(tmp_path: Path, monkeypatch):
    module = _load_script("generate_personal_study_guides")

    def forbidden(*_a, **_k):
        raise AssertionError("video-only compile must not need study tooling")

    monkeypatch.setattr(module, "_resolve_executables", forbidden)
    monkeypatch.setattr(module, "_load_lecture_titles", forbidden)
    targets, schedule, queue = (
        tmp_path / "t.yaml",
        tmp_path / "s.yaml",
        tmp_path / "queue.json",
    )
    targets.write_text(yaml.safe_dump({"disciplines": []}))
    schedule.write_text(yaml.safe_dump({"entries": []}))
    videos = tmp_path / "raw"
    videos.mkdir()
    source = tmp_path / "inbox" / "BAILARINA - YouTube.mp4"
    source.parent.mkdir()
    source.write_bytes(b"clip-bytes")
    (videos / f"{CLIP_ID}.mp4").symlink_to(source)
    public, private = tmp_path / "study-guides", tmp_path / "study-guides-local"

    def compile_with(
        lectures: list[LocalLecture], lecture_ids: tuple[str, ...] = (CLIP_ID,)
    ) -> None:
        metadata = tmp_path / "local.yaml"
        metadata.write_text(
            yaml.safe_dump(
                {"lectures": [item.model_dump(mode="json") for item in lectures]}
            )
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "compile",
                *lecture_ids,
                "--targets-path",
                str(targets),
                "--schedule-path",
                str(schedule),
                "--queue-path",
                str(queue),
                "--videos-root",
                str(videos),
                "--local-lectures-path",
                str(metadata),
                "--output-root",
                str(public),
                "--private-output-root",
                str(private),
            ],
        )
        module.main()

    return {
        "compile": compile_with,
        "public": public,
        "private": private,
        "module": module,
        "source": source,
    }


def test_published_video_only_compiles_a_minimal_public_bundle(
    compile_environment,
) -> None:
    compile_environment["compile"]([_clip(publish=True)])

    bundle = (
        compile_environment["public"]
        / "2025"
        / "1-semestre"
        / "disciplina-b"
        / "topico-1"
        / f"{CLIP_ID}-bailarina"
    )
    files = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    assert files == {"indice.md", ".bundle.json", "video/aula.mp4"}
    assert (bundle / "video" / "aula.mp4").read_bytes() == b"clip-bytes"
    assert not (bundle / "video" / "aula.mp4").is_symlink()
    index = (bundle / "indice.md").read_text(encoding="utf-8")
    assert "# Bailarina" in index
    assert VIDEO_ONLY_NOTICE in index
    assert "(video/aula.mp4)" in index


def test_unpublished_video_only_stays_in_the_private_root(compile_environment) -> None:
    compile_environment["compile"]([_clip(publish=False)])

    assert not list(compile_environment["public"].rglob(f"{CLIP_ID}-*"))
    assert list(compile_environment["private"].rglob(f"{CLIP_ID}-bailarina"))


def _public_bundle(environment) -> Path:
    return next(environment["public"].rglob(f"{CLIP_ID}-bailarina"))


def _file_set(bundle: Path) -> set[str]:
    return {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}


def test_recompiling_a_video_only_bundle_is_idempotent(compile_environment) -> None:
    compile_environment["compile"]([_clip(publish=True)])
    bundle = _public_bundle(compile_environment)
    first = {name: (bundle / name).read_bytes() for name in _file_set(bundle)}

    compile_environment["compile"]([_clip(publish=True)])

    bundle = _public_bundle(compile_environment)
    assert _file_set(bundle) == {"indice.md", ".bundle.json", "video/aula.mp4"}
    assert {name: (bundle / name).read_bytes() for name in _file_set(bundle)} == first
    assert len(list(compile_environment["public"].rglob(f"{CLIP_ID}-*"))) == 1
    hidden = [
        path.name
        for path in compile_environment["public"].iterdir()
        if path.name.startswith(f".{CLIP_ID}.")
    ]
    assert hidden == []


def test_bundle_video_is_a_hard_link_of_the_source(compile_environment) -> None:
    compile_environment["compile"]([_clip(publish=True)])

    video = _public_bundle(compile_environment) / "video" / "aula.mp4"

    assert video.samefile(compile_environment["source"])
    assert video.stat().st_nlink >= 2


def test_require_exact_files_rejects_extra_or_missing_files(tmp_path: Path) -> None:
    module = _load_script("generate_personal_study_guides")
    (tmp_path / "indice.md").write_text("x")
    (tmp_path / "extra.pdf").write_text("x")

    with pytest.raises(RuntimeError, match="pacote de vídeo incompleto"):
        module._require_exact_files(
            tmp_path, frozenset({"indice.md", "video/aula.mp4"})
        )


def test_cross_filesystem_link_failure_has_a_clear_message(
    compile_environment, monkeypatch
) -> None:
    def exdev(*_a, **_k):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(compile_environment["module"].os, "link", exdev)

    with pytest.raises(RuntimeError, match="mesmo sistema de arquivos"):
        compile_environment["compile"]([_clip(publish=True)])


def test_registering_video_only_does_not_enqueue(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")
    queue = tmp_path / "queue.json"

    local_lectures.register_local_lecture(
        _clip(publish=True),
        video,
        portal_ids=frozenset(),
        local_lectures_path=tmp_path / "local.yaml",
        raw_video_dir=tmp_path / "raw",
        queue_path=queue,
        video_probe=lambda _p: None,
    )

    assert not queue.exists()
    assert (tmp_path / "raw" / f"{CLIP_ID}.mp4").is_symlink()


def test_reregistering_with_a_different_kind_is_refused(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")

    def register(lecture: LocalLecture) -> None:
        local_lectures.register_local_lecture(
            lecture,
            video,
            portal_ids=frozenset(),
            local_lectures_path=tmp_path / "local.yaml",
            raw_video_dir=tmp_path / "raw",
            queue_path=tmp_path / "queue.json",
            video_probe=lambda _p: None,
        )

    register(_clip())

    with pytest.raises(local_lectures.LocalLectureError, match="já em uso"):
        register(_clip(kind="study"))


def test_site_publishes_a_video_only_bundle_with_a_playable_video(
    compile_environment, tmp_path: Path
) -> None:
    compile_environment["compile"]([_clip(publish=True)])
    site, videos = tmp_path / "site", tmp_path / "site-videos"

    catalog = generate_site((), compile_environment["public"], site, videos)

    lecture = read_catalog(compile_environment["public"]).lectures[0]
    assert lecture.lecture_id == CLIP_ID
    assert lecture.video is not None and not lecture.has_study_guide
    page = (site / lecture.relative_path / "index.html").read_text(encoding="utf-8")
    assert f'src="/videos/{lecture.relative_path}/video/aula.mp4"' in page
    assert "Vídeo sem material de estudo gerado" in page
    assert 'class="card-grid"' not in page
    assert (videos / lecture.relative_path / "video" / "aula.mp4").is_file()
    assert (site / lecture.relative_path / "assistir.html").is_file()
    assert catalog.lectures[0].lecture_id == CLIP_ID


def test_non_exdev_link_errors_propagate_unchanged(compile_environment) -> None:
    compile_environment["source"].unlink()

    with pytest.raises(FileNotFoundError, match="vídeo de origem não encontrado"):
        compile_environment["compile"]([_clip(publish=True)])


@pytest.mark.parametrize("lecture_ids", [(CLIP_ID, "925102"), ("925102",)])
def test_study_ids_still_load_titles_and_executables(
    compile_environment, monkeypatch, lecture_ids
) -> None:
    module = compile_environment["module"]
    calls: list[str] = []
    monkeypatch.setattr(
        module, "_load_lecture_titles", lambda _p: calls.append("titles") or {}
    )
    monkeypatch.setattr(
        module, "_resolve_executables", lambda _e: calls.append("executables") or {}
    )
    lectures = [_clip(publish=True), _clip("925102", kind="study", title="Estudo")]

    with pytest.raises(ValueError, match="não encontrada na fila"):
        compile_environment["compile"](lectures, lecture_ids)

    assert sorted(calls) == ["executables", "titles"]
    assert bool(list(compile_environment["public"].rglob(f"{CLIP_ID}-*"))) == (
        CLIP_ID in lecture_ids
    )
