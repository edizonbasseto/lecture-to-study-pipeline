"""Tests for local (portal-less) lecture registration, merge and publication gate."""

from datetime import date
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml

from lecture_study.study_materials import local_lectures
from lecture_study.study_materials.academic_catalog import (
    AcademicCatalog,
    AcademicDiscipline,
    AcademicLesson,
    AcademicScheduleEntry,
    academic_lecture_paths,
)
from lecture_study.study_materials.local_lectures import (
    LocalLecture,
    LocalLectureError,
    default_scheduled_date,
    load_local_lectures,
    merge_local_lectures,
    register_local_lecture,
    select_output_root,
    unpublished_lecture_ids,
)

SCRIPTS = Path(__file__).parent.parent / "scripts"


def _lecture(lecture_id: str = "925209", **overrides) -> LocalLecture:
    fields = {
        "lecture_id": lecture_id,
        "title": "Princípios da cirurgia reconstrutiva",
        "discipline": "Disciplina A",
        "section": "Videoaulas locais",
        "scheduled_date": date(2025, 9, 1),
    }
    return LocalLecture(**{**fields, **overrides})


def _no_probe(_path: Path) -> None:
    return None


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "inbox" / "09-aula.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    return {
        "source": source,
        "metadata": tmp_path / "config" / "local.yaml",
        "raw": tmp_path / "raw",
        "queue": tmp_path / "queue.json",
    }


def _register(paths, lecture=None, portal=frozenset(), source=None, probe=_no_probe):
    return register_local_lecture(
        lecture or _lecture(),
        source or paths["source"],
        portal_ids=portal,
        local_lectures_path=paths["metadata"],
        raw_video_dir=paths["raw"],
        queue_path=paths["queue"],
        video_probe=probe,
    )


def _assert_nothing_written(paths) -> None:
    assert not paths["queue"].exists()
    assert not paths["metadata"].exists()
    assert not paths["raw"].exists() or not list(paths["raw"].iterdir())


def test_register_links_video_enqueues_and_records_metadata(paths) -> None:
    link = _register(paths)

    assert link == paths["raw"] / "925209.mp4"
    assert link.is_symlink() and link.resolve() == paths["source"].resolve()
    assert paths["source"].read_bytes() == b"video"
    queue = json.loads(paths["queue"].read_text())
    assert queue[0]["id"] == "925209"
    assert queue[0]["subject"] == "Disciplina A"
    assert queue[0]["url"] == "local://mod/supervideo/925209"
    assert load_local_lectures(paths["metadata"]) == [_lecture()]


def test_rerun_with_same_id_and_source_is_idempotent(paths) -> None:
    _register(paths)
    before = (paths["metadata"].read_text(), paths["queue"].read_text())

    _register(paths)

    assert (paths["metadata"].read_text(), paths["queue"].read_text()) == before


def test_rerun_completes_a_registration_missing_its_queue_row(paths) -> None:
    _register(paths)
    paths["queue"].write_text("[]")

    _register(paths)

    assert json.loads(paths["queue"].read_text())[0]["id"] == "925209"
    assert len(load_local_lectures(paths["metadata"])) == 1


def test_rerun_with_another_source_is_rejected(paths, tmp_path) -> None:
    _register(paths)
    other = tmp_path / "other.mp4"
    other.write_bytes(b"x")

    with pytest.raises(LocalLectureError, match="outra origem"):
        _register(paths, source=other)


@pytest.mark.parametrize(
    "bad_id",
    [
        "92a209",
        "925300",
        "12345",
        "925099",
        "925209 ",
        "0925209",
        "0925100",
        "00925299",
    ],
)
def test_lecture_rejects_ids_outside_reserved_numeric_ranges(bad_id) -> None:
    with pytest.raises(ValueError):
        _lecture(bad_id)


@pytest.mark.parametrize("field", ["title", "discipline", "section"])
@pytest.mark.parametrize("bad_text", ["", "   ", "line\nbreak", "!!!", "tab\there"])
def test_lecture_rejects_unusable_text(field, bad_text) -> None:
    with pytest.raises(ValueError):
        _lecture(**{field: bad_text})


def test_lecture_collapses_whitespace() -> None:
    assert _lecture(title="  A   B  ").title == "A B"


@pytest.mark.parametrize("origin", ["portal", "different-local", "queue"])
def test_register_rejects_ids_already_in_use(paths, origin) -> None:
    portal = frozenset({"925209"}) if origin == "portal" else frozenset()
    if origin == "different-local":
        _register(paths, _lecture(title="Outro título"))
        paths["raw"].joinpath("925209.mp4").unlink()
    if origin == "queue":
        paths["queue"].write_text(
            json.dumps([{"id": "925209", "url": "https://portal/x"}])
        )

    with pytest.raises(LocalLectureError, match="já em uso"):
        _register(paths, portal=portal)


def test_missing_source_leaves_every_artifact_untouched(paths) -> None:
    paths["source"].unlink()

    with pytest.raises(FileNotFoundError):
        _register(paths)

    _assert_nothing_written(paths)


def test_unsupported_extension_and_failed_probe_leave_no_trace(paths, tmp_path) -> None:
    text_file = tmp_path / "notes.txt"
    text_file.write_text("x")

    def failing_probe(_path: Path) -> None:
        raise LocalLectureError("sem vídeo")

    with pytest.raises(LocalLectureError, match="extensão"):
        _register(paths, source=text_file)
    with pytest.raises(LocalLectureError, match="sem vídeo"):
        _register(paths, probe=failing_probe)

    _assert_nothing_written(paths)


def test_queue_failure_rolls_back_link_and_metadata(paths, monkeypatch) -> None:
    paths["metadata"].parent.mkdir()
    paths["metadata"].write_text(yaml.safe_dump({"lectures": []}))
    original_metadata = paths["metadata"].read_text()

    def exploding_upsert(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(local_lectures, "upsert_lecture_in_queue", exploding_upsert)

    with pytest.raises(OSError, match="disk full"):
        _register(paths)

    assert not (paths["raw"] / "925209.mp4").exists()
    assert not (paths["raw"] / "925209.mp4").is_symlink()
    assert paths["metadata"].read_text() == original_metadata
    assert not paths["queue"].exists()


def test_metadata_failure_rolls_back_link(paths, monkeypatch) -> None:
    def exploding_write(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(local_lectures, "_atomic_write_text", exploding_write)

    with pytest.raises(OSError, match="read-only"):
        _register(paths)

    assert not (paths["raw"] / "925209.mp4").is_symlink()
    assert not paths["queue"].exists()


def test_malformed_queue_is_reported_cleanly(paths) -> None:
    paths["queue"].write_text("{not json")

    with pytest.raises(LocalLectureError, match="fila inválida"):
        _register(paths)

    assert not (paths["raw"] / "925209.mp4").is_symlink()


@pytest.mark.parametrize(
    "content", ["lectures: [", "- just\n- a list\n", "lectures:\n- lecture_id: '1'\n"]
)
def test_malformed_metadata_yaml_is_reported_cleanly(paths, content) -> None:
    paths["metadata"].parent.mkdir()
    paths["metadata"].write_text(content)

    with pytest.raises(LocalLectureError, match="aulas locais inválidas"):
        load_local_lectures(paths["metadata"])


@pytest.mark.parametrize("flags", [(True, False), (False, True), (False, False)])
def test_duplicate_lecture_ids_are_rejected_by_the_loader(
    paths, flags: tuple[bool, bool]
) -> None:
    rows = [
        {
            "lecture_id": "925101",
            "title": "Aula",
            "discipline": "Disciplina A",
            "section": "Videoaulas locais",
            "scheduled_date": "2025-09-01",
            "remote_llm_allowed": flag,
        }
        for flag in flags
    ]
    paths["metadata"].parent.mkdir()
    paths["metadata"].write_text(yaml.safe_dump({"lectures": rows}))

    with pytest.raises(LocalLectureError, match="repetidos.*925101"):
        load_local_lectures(paths["metadata"])


_ROW_PREFIX = (
    "lectures:\n- lecture_id: '925101'\n  title: T\n  discipline: D\n"
    "  section: S\n  scheduled_date: 2025-07-01\n"
)
_ROW_BODY = _ROW_PREFIX.split("lectures:\n", 1)[1]


@pytest.mark.parametrize(
    ("text", "repeated_path"),
    [
        (
            _ROW_PREFIX + "  remote_llm_allowed: false\n  remote_llm_allowed: true\n",
            "lectures.0.remote_llm_allowed",
        ),
        (
            _ROW_PREFIX + "  remote_llm_allowed: true\n  remote_llm_allowed: false\n",
            "lectures.0.remote_llm_allowed",
        ),
        (_ROW_PREFIX + "  publish: false\n  publish: true\n", "lectures.0.publish"),
        (_ROW_PREFIX + "lectures:\n" + _ROW_BODY, "lectures"),
    ],
)
def test_repeated_registry_keys_are_rejected_naming_only_the_key_path(
    tmp_path: Path, text: str, repeated_path: str
) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(text)

    with pytest.raises(LocalLectureError, match="chaves repetidas") as raised:
        load_local_lectures(path)

    assert repeated_path in str(raised.value)
    assert "925101" not in str(raised.value)


@pytest.mark.parametrize(
    ("text", "type_name"),
    [
        (_ROW_PREFIX + "  note: 2001-13-45\n", "ValueError"),
        ("lectures: " + "[" * 5000 + "]" * 5000 + "\n", "RecursionError"),
        (_ROW_PREFIX + "  note: !!bool .nan\n", "KeyError"),
        (_ROW_PREFIX + "  note: !!timestamp 0x\n", "AttributeError"),
    ],
    ids=[
        "calendar-invalid-timestamp",
        "deep-nesting",
        "invalid-bool-tag",
        "invalid-timestamp-tag",
    ],
)
def test_unparseable_registry_yaml_is_a_typed_error_without_content(
    tmp_path: Path, text: str, type_name: str
) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(text)

    with pytest.raises(LocalLectureError, match=type_name) as raised:
        load_local_lectures(path)

    assert "2001-13-45" not in str(raised.value)
    assert "925101" not in str(raised.value)


def test_unreadable_registry_is_a_typed_error_not_an_empty_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local.yaml"
    path.mkdir()

    with pytest.raises(LocalLectureError, match="ilegível.*IsADirectoryError"):
        load_local_lectures(path)


def test_load_returns_empty_when_file_absent(tmp_path: Path) -> None:
    assert load_local_lectures(tmp_path / "missing.yaml") == []


def test_existing_rows_without_publish_field_load_unpublished(tmp_path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(
        "lectures:\n- lecture_id: '925209'\n  title: T\n  discipline: D\n"
        "  section: S\n  scheduled_date: 2025-07-01\n"
    )

    lectures = load_local_lectures(path)

    assert lectures[0].publish is False
    assert unpublished_lecture_ids(lectures) == {"925209"}


def test_existing_rows_without_remote_llm_field_load_refused(tmp_path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(
        "lectures:\n- lecture_id: '925209'\n  title: T\n  discipline: D\n"
        "  section: S\n  scheduled_date: 2025-07-01\n  publish: true\n"
    )

    assert load_local_lectures(path)[0].remote_llm_allowed is False


def test_default_scheduled_date_is_first_day_of_semester() -> None:
    assert default_scheduled_date("2025.1") == date(2025, 1, 1)
    assert default_scheduled_date("2025.2") == date(2025, 7, 1)
    with pytest.raises(ValueError, match="AAAA.S"):
        default_scheduled_date("2025-2")


def test_merge_makes_lecture_resolvable_into_academic_path() -> None:
    catalog, schedule = merge_local_lectures(
        AcademicCatalog(disciplines=[]), {}, [_lecture()]
    )

    resolved = academic_lecture_paths(catalog, schedule, lecture_ids={"925209"})

    assert resolved["925209"].parts[:2] == ("2025", "2-semestre")
    assert resolved["925209"].name.startswith("925209-")


def test_merge_without_local_lectures_returns_inputs_unchanged() -> None:
    catalog = AcademicCatalog(disciplines=[])

    assert merge_local_lectures(catalog, {}, []) == (catalog, {})


def _portal_catalog_with(lecture_id: str) -> AcademicCatalog:
    lesson = AcademicLesson(
        activity_id=lecture_id,
        moodle_order=1,
        section="S",
        display_title="T",
        url="https://p/mod/supervideo/view.php?id=1",
        enabled=True,
        discovery_timestamp="x",
    )
    return AcademicCatalog(
        disciplines=[
            AcademicDiscipline(
                title="D", url="https://p", program="P", lessons=[lesson]
            )
        ]
    )


def test_merge_rejects_collision_with_portal_catalog() -> None:
    with pytest.raises(LocalLectureError, match="colide"):
        merge_local_lectures(_portal_catalog_with("925209"), {}, [_lecture()])


def test_merge_rejects_collision_with_portal_schedule() -> None:
    schedule = {
        "925209": AcademicScheduleEntry(
            lecture_id="925209", scheduled_date=date(2025, 1, 1), schedule_topic="x"
        )
    }

    with pytest.raises(LocalLectureError, match="colide"):
        merge_local_lectures(AcademicCatalog(disciplines=[]), schedule, [_lecture()])


def test_unpublished_lecture_bundle_root_is_outside_the_deployable_tree() -> None:
    public, private = (
        Path("workspace/study-guides"),
        Path("workspace/study-guides-local"),
    )

    root = select_output_root("925209", frozenset({"925209"}), public, private)

    assert root == private
    assert public.absolute() not in root.absolute().parents
    assert root.absolute() != public.absolute()


def test_published_and_portal_lectures_use_the_public_root() -> None:
    public, private = (
        Path("workspace/study-guides"),
        Path("workspace/study-guides-local"),
    )

    assert select_output_root("925209", frozenset(), public, private) == public
    assert select_output_root("10001", frozenset({"925209"}), public, private) == public


def test_private_root_nested_in_public_root_fails_closed() -> None:
    public = Path("workspace/study-guides")

    with pytest.raises(LocalLectureError, match="dentro da raiz pública"):
        select_output_root("925209", frozenset({"925209"}), public, public / "local")


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "script", ["generate_personal_study_guides", "generate_academic_catalog"]
)
def test_compile_scripts_wire_the_local_lectures_path(script, monkeypatch) -> None:
    module = _load_script(script)
    parser = (
        module._build_argument_parser()
        if script == "generate_personal_study_guides"
        else module._argument_parser()
    )
    arguments = ["925209"] if script == "generate_personal_study_guides" else []

    default = parser.parse_args(arguments)
    custom = parser.parse_args([*arguments, "--local-lectures-path", "x.yaml"])

    assert default.local_lectures_path == local_lectures.LOCAL_LECTURES_PATH
    assert custom.local_lectures_path == Path("x.yaml")


def test_personal_guides_parser_defaults_private_root_outside_public_root() -> None:
    module = _load_script("generate_personal_study_guides")

    args = module._build_argument_parser().parse_args(["925209"])

    assert args.private_output_root == local_lectures.PRIVATE_STUDY_GUIDES_ROOT
    assert (
        args.output_root.absolute() not in args.private_output_root.absolute().parents
    )


def test_academic_catalog_script_lists_only_published_local_lectures(
    tmp_path, monkeypatch
) -> None:
    module = _load_script("generate_academic_catalog")
    targets, schedule, local, output = (
        tmp_path / "t.yaml",
        tmp_path / "s.yaml",
        tmp_path / "l.yaml",
        tmp_path / "out" / "catalogo.md",
    )
    targets.write_text(yaml.safe_dump({"disciplines": []}))
    schedule.write_text(yaml.safe_dump({"entries": []}))
    rows = [
        {
            "lecture_id": lecture_id,
            "title": title,
            "discipline": "Disc",
            "section": "Sec",
            "scheduled_date": date(2025, 9, 1),
            "publish": publish,
        }
        for lecture_id, title, publish in (
            ("925201", "Publicada", True),
            ("925202", "Privada", False),
        )
    ]
    local.write_text(yaml.safe_dump({"lectures": rows}))
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--targets-path",
            str(targets),
            "--schedule-path",
            str(schedule),
            "--local-lectures-path",
            str(local),
            "--output-path",
            str(output),
        ],
    )

    module.main()

    rendered = output.read_text()
    assert "925201" in rendered
    assert "925202" not in rendered


def _tiny_video(path: Path) -> Path:
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg indisponível")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.2",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def cli(tmp_path, monkeypatch):
    module = _load_script("register_local_lecture")
    targets, schedule = tmp_path / "t.yaml", tmp_path / "s.yaml"
    targets.write_text(yaml.safe_dump({"disciplines": []}))
    schedule.write_text(yaml.safe_dump({"entries": []}))
    video = _tiny_video(tmp_path / "aula.mp4")
    state = {
        "metadata": tmp_path / "cfg" / "local.yaml",
        "queue": tmp_path / "data" / "queue.json",
        "raw": tmp_path / "data" / "raw",
        "public": tmp_path / "study-guides",
    }

    def run(*extra: str, lecture_id: str = "925209", source: Path = video) -> int:
        argv = [
            "register",
            lecture_id,
            str(source),
            "--title",
            "Aula",
            "--discipline",
            "Disc",
            "--semester",
            "2025.2",
            "--targets-path",
            str(targets),
            "--schedule-path",
            str(schedule),
            "--local-lectures-path",
            str(state["metadata"]),
            "--queue-path",
            str(state["queue"]),
            "--raw-video-dir",
            str(state["raw"]),
            "--public-root",
            str(state["public"]),
            *extra,
        ]
        monkeypatch.setattr("sys.argv", argv)
        return module.main()

    state["run"] = run
    state["video"] = video
    return state


def test_cli_registers_private_by_default(cli) -> None:
    assert cli["run"]() == 0

    assert load_local_lectures(cli["metadata"])[0].publish is False
    assert (cli["raw"] / "925209.mp4").is_symlink()
    assert json.loads(cli["queue"].read_text())[0]["id"] == "925209"


def test_cli_publish_flag_registers_published(cli) -> None:
    assert cli["run"]("--publish") == 0

    assert load_local_lectures(cli["metadata"])[0].publish is True


def test_cli_reregister_may_change_only_the_publish_flag(cli) -> None:
    cli["run"]()

    assert cli["run"]("--publish") == 0

    rows = load_local_lectures(cli["metadata"])
    assert [row.publish for row in rows] == [True]
    assert len(json.loads(cli["queue"].read_text())) == 1


def test_cli_registers_without_remote_llm_flag_by_default(cli) -> None:
    assert cli["run"]() == 0

    assert load_local_lectures(cli["metadata"])[0].remote_llm_allowed is False


def test_cli_remote_llm_allowed_flag_is_recorded(cli) -> None:
    assert cli["run"]("--remote-llm-allowed") == 0

    assert load_local_lectures(cli["metadata"])[0].remote_llm_allowed is True


def test_cli_rerun_may_toggle_remote_llm_flag_in_both_directions(cli) -> None:
    cli["run"]()

    assert cli["run"]("--remote-llm-allowed") == 0
    assert load_local_lectures(cli["metadata"])[0].remote_llm_allowed is True

    assert cli["run"]() == 0
    assert load_local_lectures(cli["metadata"])[0].remote_llm_allowed is False
    assert len(json.loads(cli["queue"].read_text())) == 1


def test_cli_demotion_is_refused_while_a_public_bundle_exists(cli, capsys) -> None:
    cli["run"]("--publish")
    bundle = cli["public"] / "2025" / "2-semestre" / "d" / "s" / "925209-aula"
    bundle.mkdir(parents=True)

    assert cli["run"]() == 1

    assert "pacote público" in capsys.readouterr().err
    assert load_local_lectures(cli["metadata"])[0].publish is True


def test_cli_demotion_allowed_without_public_bundle(cli) -> None:
    cli["run"]("--publish")

    assert cli["run"]() == 0
    assert load_local_lectures(cli["metadata"])[0].publish is False


def test_cli_reports_expected_errors_with_exit_code(cli, tmp_path, capsys) -> None:
    assert cli["run"](lecture_id="12345") == 1
    assert cli["run"](source=tmp_path / "missing.mp4") == 1
    assert "erro:" in capsys.readouterr().err


def test_ffprobe_timeout_is_a_clean_error(monkeypatch, tmp_path) -> None:
    import subprocess

    def timing_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 60)

    monkeypatch.setattr(local_lectures, "_run_ffprobe", timing_out)

    with pytest.raises(LocalLectureError, match="excedeu"):
        local_lectures.probe_video_stream(tmp_path / "x.mp4")


def test_ffprobe_receives_an_absolute_path_never_a_dash_option(monkeypatch) -> None:
    captured = {}

    def fake_run(executable, absolute_path, environment):
        captured["path"] = absolute_path

        class Result:
            returncode = 0
            stdout = "video"

        return Result()

    monkeypatch.setattr(local_lectures, "_run_ffprobe", fake_run)

    local_lectures.probe_video_stream(Path("-malicious.mp4"))

    assert captured["path"].is_absolute()
    assert not str(captured["path"]).startswith("-")


@pytest.mark.parametrize(
    "private", ["workspace/study-guides/../study-guides/x", "WORKSPACE/Study-Guides/x"]
)
def test_nesting_check_normalizes_dotdot_and_case(private) -> None:
    with pytest.raises(LocalLectureError, match="dentro da raiz pública"):
        select_output_root(
            "925209",
            frozenset({"925209"}),
            Path("workspace/study-guides"),
            Path(private),
        )


def test_withheld_ids_cover_reserved_ranges_except_published() -> None:
    published = [_lecture("925209", publish=True)]

    withheld = local_lectures.withheld_lecture_ids(published)

    assert "925209" not in withheld
    assert {"925100", "925199", "925200", "925299", "925208"} <= set(withheld)
    assert len(withheld) == 199


def test_exclusion_lists_keep_unpublished_exams_out_of_a_real_rsync(
    tmp_path, monkeypatch
) -> None:
    import shutil
    import subprocess

    if shutil.which("rsync") is None:
        pytest.skip("rsync indisponível")
    module = _load_script("list_withheld_lecture_ids")
    metadata = tmp_path / "local.yaml"
    metadata.write_text(
        yaml.safe_dump({"lectures": [_lecture("925201", publish=True).model_dump()]})
    )
    exams, destination = tmp_path / "exams", tmp_path / "server"
    for lecture_id in ("10001", "925201", "925209"):
        (exams / lecture_id).mkdir(parents=True)
        (exams / lecture_id / "exam.json").write_text("{}")
    (destination / "925209").mkdir(parents=True)
    (destination / "925209" / "exam.json").write_text("{}")
    exclude_file = tmp_path / "exclude.txt"
    monkeypatch.setattr(
        "sys.argv",
        ["x", "exams", "--local-lectures-path", str(metadata)],
    )
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main()
    exclude_file.write_text(buffer.getvalue())

    subprocess.run(
        [
            "rsync",
            "-az",
            "--delete",
            "--delete-excluded",
            f"--exclude-from={exclude_file}",
            f"{exams}/",
            f"{destination}/",
        ],
        check=True,
    )

    assert sorted(path.name for path in destination.iterdir()) == ["10001", "925201"]


# --- canonical ids and deploy ordering ---------------------------------


def test_padded_id_is_rejected_by_cli(cli, capsys) -> None:
    assert cli["run"](lecture_id="0925209") == 1
    assert "canônico" in capsys.readouterr().err


def test_padded_id_is_rejected_when_loading_yaml(tmp_path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(
        "lectures:\n- lecture_id: '0925209'\n  title: T\n  discipline: D\n"
        "  section: S\n  scheduled_date: 2025-07-01\n"
    )

    with pytest.raises(LocalLectureError, match="aulas locais inválidas"):
        load_local_lectures(path)


def test_withheld_patterns_match_every_id_the_validator_accepts() -> None:
    accepted = [
        str(value)
        for value in range(925000, 925400)
        if local_lectures.is_reserved_local_id(str(value))
    ]
    withheld = set(local_lectures.withheld_lecture_ids([]))

    assert set(accepted) == withheld
    for padded in ("0925100", "00925299"):
        assert not local_lectures.is_reserved_local_id(padded)


def test_guides_pattern_keeps_unpublished_nested_bundles_out_of_a_real_rsync(
    tmp_path, monkeypatch, capsys
) -> None:
    import shutil
    import subprocess

    if shutil.which("rsync") is None:
        pytest.skip("rsync indisponível")
    module = _load_script("list_withheld_lecture_ids")
    metadata = tmp_path / "local.yaml"
    metadata.write_text(
        yaml.safe_dump({"lectures": [_lecture("925201", publish=True).model_dump()]})
    )
    guides, server = tmp_path / "guides", tmp_path / "server"
    layout = "2025/2-semestre/disciplina/secao"
    for lecture_id in ("10001", "925201", "925209"):
        bundle = guides / layout / f"{lecture_id}-aula"
        bundle.mkdir(parents=True)
        (bundle / "indice.md").write_text("x")
    stale = server / layout / "925209-aula"
    stale.mkdir(parents=True)
    (stale / "indice.md").write_text("x")
    monkeypatch.setattr(
        "sys.argv", ["x", "guides", "--local-lectures-path", str(metadata)]
    )
    module.main()
    exclude_file = tmp_path / "exclude.txt"
    exclude_file.write_text(capsys.readouterr().out)

    subprocess.run(
        [
            "rsync",
            "-az",
            "--delete",
            "--delete-excluded",
            f"--exclude-from={exclude_file}",
            f"{guides}/",
            f"{server}/",
        ],
        check=True,
    )

    names = sorted(path.name for path in (server / layout).iterdir())
    assert names == ["10001-aula", "925201-aula"]
