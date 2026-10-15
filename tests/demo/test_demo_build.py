"""Demo video build: pure command builders and the real slide compilation."""

import shutil
import struct
from pathlib import Path

import pytest

from lecture_study.demo.media_build import (
    MAXIMUM_VIDEO_SECONDS,
    MINIMUM_VIDEO_SECONDS,
    SLIDE_TAIL_PAD_SECONDS,
    SLIDES_SOURCE_PATH,
    BuildSummary,
    DemoBuildError,
    SlideSegment,
    concat_command,
    concat_list_text,
    duration_probe_command,
    midpoint_slide_number,
    missing_headings,
    parse_probed_duration,
    require_duration_in_band,
    slide_images_command,
    slide_segment_command,
)
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import run_bounded_process

SLIDE_COUNT = 6
SLIDE_WIDTH = 1920
SLIDE_HEIGHT = 1080
PNG_IHDR_SIZE_OFFSET = 16
TYPST_TEST_TIMEOUT_SECONDS = 120.0


def _segment() -> SlideSegment:
    return SlideSegment(
        slide_image=Path("build/slide-1.png"),
        narration_wav=Path("build/narration-01.wav"),
        output=Path("build/segment-01.mp4"),
        narration_seconds=40.0,
    )


def test_slide_images_command_is_an_argument_list_with_inputs_in_place() -> None:
    command = slide_images_command(
        "/bin/typst", Path("demo/slides.typ"), Path("out/slide-{p}.png")
    )

    assert command[:3] == ["/bin/typst", "compile", "demo/slides.typ"]
    assert "out/slide-{p}.png" in command
    assert command[command.index("--ppi") + 1] == "72"


def test_slide_images_command_rejects_a_pattern_without_the_page_placeholder() -> None:
    with pytest.raises(DemoBuildError, match=r"\{p\}"):
        slide_images_command("typst", Path("slides.typ"), Path("out/slide.png"))


def test_duration_probe_command_asks_for_the_container_duration() -> None:
    command = duration_probe_command("/bin/ffprobe", Path("media.wav"))

    assert command[0] == "/bin/ffprobe"
    assert command[command.index("-show_entries") + 1] == "format=duration"
    assert command[command.index("-of") + 1] == "csv=p=0"
    assert command[-1] == "media.wav"


def test_parse_probed_duration_accepts_a_positive_number() -> None:
    assert parse_probed_duration("241.37\n") == pytest.approx(241.37)


@pytest.mark.parametrize("stdout", ["", "N/A", "0", "-3.5"])
def test_parse_probed_duration_rejects_unusable_output(stdout: str) -> None:
    with pytest.raises(DemoBuildError):
        parse_probed_duration(stdout)


def test_slide_segment_command_lasts_narration_plus_the_tail_pad() -> None:
    command = slide_segment_command("/bin/ffmpeg", _segment())

    expected_seconds = 40.0 + SLIDE_TAIL_PAD_SECONDS
    assert float(command[command.index("-t") + 1]) == pytest.approx(expected_seconds)


def test_slide_segment_command_pairs_a_still_image_with_padded_narration() -> None:
    command = slide_segment_command("/bin/ffmpeg", _segment())

    assert command[0] == "/bin/ffmpeg"
    assert command[command.index("-loop") + 1] == "1"
    assert command[command.index("-af") + 1] == "apad"
    assert command[command.index("-tune") + 1] == "stillimage"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command.index("build/slide-1.png") < command.index("build/narration-01.wav")
    assert command[-1] == "build/segment-01.mp4"


def test_concat_list_text_has_one_file_line_per_segment() -> None:
    text = concat_list_text(("segment-01.mp4", "segment-02.mp4"))

    assert text == "file 'segment-01.mp4'\nfile 'segment-02.mp4'\n"


@pytest.mark.parametrize("name", ["a'b.mp4", "dir/a.mp4", "dir\\a.mp4"])
def test_concat_list_text_rejects_names_that_are_not_bare_file_names(
    name: str,
) -> None:
    with pytest.raises(DemoBuildError):
        concat_list_text((name,))


def test_concat_command_copies_streams_from_the_list() -> None:
    command = concat_command("/bin/ffmpeg", "segments.txt", "out.mp4")

    assert command[command.index("-f") + 1] == "concat"
    assert command[command.index("-i") + 1] == "segments.txt"
    assert command[command.index("-c") + 1] == "copy"
    assert command[command.index("-movflags") + 1] == "+faststart"
    assert command[-1] == "out.mp4"


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[PNG_IHDR_SIZE_OFFSET : PNG_IHDR_SIZE_OFFSET + 8]
    width, height = struct.unpack(">II", header)
    return width, height


@pytest.mark.skipif(shutil.which("typst") is None, reason="typst is not installed")
def test_real_slides_compile_to_six_full_hd_images(tmp_path: Path) -> None:
    environment = sanitized_child_process_environment()
    typst = resolve_trusted_executable("typst", search_path=environment["PATH"])
    command = slide_images_command(
        typst, SLIDES_SOURCE_PATH, tmp_path / "slide-{p}.png"
    )

    run_bounded_process(
        command,
        env=environment,
        timeout_seconds=TYPST_TEST_TIMEOUT_SECONDS,
        maximum_stdout_bytes=1024 * 1024,
        maximum_stderr_bytes=256 * 1024,
        check=True,
    )

    images = sorted(tmp_path.glob("slide-*.png"))
    assert len(images) == SLIDE_COUNT
    assert {_png_size(image) for image in images} == {(SLIDE_WIDTH, SLIDE_HEIGHT)}


def test_missing_headings_is_empty_when_each_heading_is_in_some_text() -> None:
    headings = ("Objetivos da aula", "Planejamento das margens")
    ocr_texts = ["OBJETIVOS  da\naula\nIndicações", "Planejamento das margens"]

    assert missing_headings(headings, ocr_texts) == ()


def test_missing_headings_reports_the_headings_absent_from_every_text() -> None:
    headings = ("Objetivos da aula", "Revisão dos pontos principais")

    assert missing_headings(headings, ["Objetivos da aula"]) == (
        "Revisão dos pontos principais",
    )


def test_missing_headings_reports_every_heading_when_there_is_no_text() -> None:
    assert missing_headings(("A", "B"), []) == ("A", "B")


@pytest.mark.parametrize(
    "seconds", [MINIMUM_VIDEO_SECONDS - 0.1, MAXIMUM_VIDEO_SECONDS + 0.1]
)
def test_require_duration_in_band_rejects_outside_the_band(seconds: float) -> None:
    with pytest.raises(DemoBuildError, match="seconds"):
        require_duration_in_band(seconds)


@pytest.mark.parametrize(
    "seconds", [MINIMUM_VIDEO_SECONDS, 243.6, MAXIMUM_VIDEO_SECONDS]
)
def test_require_duration_in_band_accepts_inside_the_band(seconds: float) -> None:
    assert require_duration_in_band(seconds) is None


def test_midpoint_slide_number_is_the_slide_containing_half_the_duration() -> None:
    assert midpoint_slide_number((10.0, 10.0, 30.0, 20.0, 10.0, 10.0)) == 3
    assert midpoint_slide_number((10.0, 10.0, 10.0, 10.0, 10.0, 10.0)) == 4


def test_midpoint_slide_number_of_one_slide_is_that_slide() -> None:
    assert midpoint_slide_number((42.0,)) == 1


def test_build_summary_round_trips_through_json() -> None:
    summary = BuildSummary(
        video_seconds=243.6,
        slide_seconds=(30.0, 40.0, 50.0),
        frames_extracted=7,
        headings_found=3,
        heading_count=3,
        midpoint_slide_number=2,
    )

    assert BuildSummary.model_validate_json(summary.model_dump_json()) == summary
