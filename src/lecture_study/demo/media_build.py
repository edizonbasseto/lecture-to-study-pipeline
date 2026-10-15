"""Pure builders for the demo lecture video (ADR 0032, ADR 0047).

Everything here constructs argument lists or parses text; no process is
started and no file is touched. ``scripts/build_demo_lecture.py`` runs the
commands through the repository's bounded process runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = [
    "BUILD_DIR",
    "DEMO_SCRIPT_PATH",
    "DEMO_VIDEO_PATH",
    "SLIDES_SOURCE_PATH",
    "SLIDE_TAIL_PAD_SECONDS",
    "BuildSummary",
    "DemoBuildError",
    "MAXIMUM_VIDEO_SECONDS",
    "MINIMUM_VIDEO_SECONDS",
    "SlideSegment",
    "concat_command",
    "concat_list_text",
    "duration_probe_command",
    "midpoint_slide_number",
    "missing_headings",
    "parse_probed_duration",
    "require_duration_in_band",
    "slide_images_command",
    "slide_segment_command",
]

DEMO_SCRIPT_PATH = Path("demo/lecture/script.yaml")
SLIDES_SOURCE_PATH = Path("demo/lecture/slides.typ")
BUILD_DIR = Path("workspace/demo/build")
BUILD_SUMMARY_PATH = BUILD_DIR / "build-summary.json"
DEMO_VIDEO_PATH = Path("workspace/demo/aula-demo.mp4")
SLIDE_TAIL_PAD_SECONDS = 0.6
MINIMUM_VIDEO_SECONDS = 200.0
MAXIMUM_VIDEO_SECONDS = 290.0

_PAGE_PLACEHOLDER = "{p}"
_SLIDE_FRAME_RATE = "25"
_NARRATION_SAMPLE_RATE = "24000"
_SLIDE_RENDER_PPI = "72"
_FORBIDDEN_SEGMENT_NAME_CHARACTERS = ("'", "/", "\\", "\n")


class DemoBuildError(RuntimeError):
    """The demo video build produced an unusable intermediate or result."""


@dataclass(frozen=True, slots=True)
class SlideSegment:
    """One slide image shown for the length of its narration plus a pad."""

    slide_image: Path
    narration_wav: Path
    output: Path
    narration_seconds: float

    @property
    def segment_seconds(self) -> float:
        return self.narration_seconds + SLIDE_TAIL_PAD_SECONDS


def slide_images_command(typst: str, source: Path, output_pattern: Path) -> list[str]:
    """Render every page of ``source`` to a PNG named by ``output_pattern``."""
    if _PAGE_PLACEHOLDER not in str(output_pattern):
        raise DemoBuildError(
            f"slide output pattern must contain {_PAGE_PLACEHOLDER}: {output_pattern}"
        )
    return [
        typst,
        "compile",
        str(source),
        str(output_pattern),
        "--ppi",
        _SLIDE_RENDER_PPI,
    ]


def duration_probe_command(ffprobe: str, media: Path) -> list[str]:
    """Ask ffprobe for the container duration in seconds."""
    return [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(media),
    ]


def parse_probed_duration(stdout: str) -> float:
    """The positive duration printed by ``duration_probe_command``."""
    try:
        seconds = float(stdout.strip())
    except ValueError:
        raise DemoBuildError("ffprobe did not report a numeric duration") from None
    if not seconds > 0.0:
        raise DemoBuildError("ffprobe reported a non-positive duration")
    return seconds


def slide_segment_command(ffmpeg: str, segment: SlideSegment) -> list[str]:
    """Encode one still slide with its narration as an H.264 plus AAC file."""
    return [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-framerate",
        _SLIDE_FRAME_RATE,
        "-i",
        str(segment.slide_image),
        "-i",
        str(segment.narration_wav),
        "-af",
        "apad",
        "-t",
        f"{segment.segment_seconds:.3f}",
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-r",
        _SLIDE_FRAME_RATE,
        "-c:a",
        "aac",
        "-ar",
        _NARRATION_SAMPLE_RATE,
        str(segment.output),
    ]


def concat_list_text(segment_names: tuple[str, ...]) -> str:
    """The ffmpeg concat demuxer list: one ``file`` line per bare file name."""
    for name in segment_names:
        if any(character in name for character in _FORBIDDEN_SEGMENT_NAME_CHARACTERS):
            raise DemoBuildError(f"segment name is not a bare file name: {name!r}")
    return "".join(f"file '{name}'\n" for name in segment_names)


def concat_command(ffmpeg: str, list_name: str, output_name: str) -> list[str]:
    """Join the listed segments without re-encoding; run inside the build dir."""
    return [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_name,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        output_name,
    ]


class BuildSummary(BaseModel):
    """What the build measured on the finished video, written as JSON."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    video_seconds: float
    slide_seconds: tuple[float, ...]
    frames_extracted: int
    headings_found: int
    heading_count: int
    midpoint_slide_number: int


def missing_headings(
    headings: tuple[str, ...], ocr_texts: list[str]
) -> tuple[str, ...]:
    return headings


def require_duration_in_band(seconds: float) -> None:
    return None


def midpoint_slide_number(slide_seconds: tuple[float, ...]) -> int:
    return 0
