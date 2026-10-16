#!/usr/bin/env python3
"""Rebuild the synthetic demo lecture video from its versioned sources.

Typst renders the slides from ``demo/lecture/slides.typ``, Kokoro narrates
every slide from ``demo/lecture/script.yaml`` through the shared helper, and
ffmpeg composes one segment per slide and joins them (ADR 0032, ADR 0047).
Every child process is an argument list run through the bounded process
runner with a resolved executable and the sanitized environment. Outputs
live under ``workspace/demo/`` and are never versioned.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lecture_study.demo.media_build import (
    BUILD_DIR,
    BUILD_SUMMARY_PATH,
    DEMO_SCRIPT_PATH,
    DEMO_VIDEO_PATH,
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
from lecture_study.demo.script import (
    DemoScript,
    DemoScriptError,
    load_demo_script,
    require_clean_narration,
)
from lecture_study.domain.errors import AudioGenerationError
from lecture_study.extraction.media_workers import (
    MediaExtractionError,
    extract_frames,
)
from lecture_study.extraction.ocr_supervisor import extract_text_supervised
from lecture_study.extraction.ocr_worker import OcrError
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    run_bounded_process,
)
from lecture_study.tts.generator import synthesize_kokoro_wav

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEMO_WORKSPACE = Path("workspace/demo")

TYPST_TIMEOUT_SECONDS = 120.0
FFPROBE_TIMEOUT_SECONDS = 30.0
FFMPEG_TIMEOUT_SECONDS = 600.0
MAXIMUM_STDOUT_BYTES = 1024 * 1024
MAXIMUM_STDERR_BYTES = 256 * 1024

KEY_TERM_SLIDE_NUMBERS = (3, 4)
CONCAT_LIST_NAME = "segments.txt"
PARTIAL_VIDEO_NAME = "aula-demo.partial.mp4"

BUILD_FAILURES = (
    DemoScriptError,
    DemoBuildError,
    AudioGenerationError,
    OcrError,
    MediaExtractionError,
    subprocess.CalledProcessError,
    subprocess.TimeoutExpired,
    ProcessOutputLimitExceeded,
    OSError,
)


@dataclass(frozen=True, slots=True)
class Toolchain:
    """The resolved executables and the environment they run under."""

    typst: str
    ffmpeg: str
    ffprobe: str
    environment: dict[str, str]


def main() -> int:
    os.chdir(REPOSITORY_ROOT)
    try:
        summary = build_demo_video()
    except BUILD_FAILURES as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"video={DEMO_VIDEO_PATH}")
    print(f"frames={summary.frames_extracted}")
    print(f"headings_found={summary.headings_found}/{summary.heading_count}")
    print(f"duration_seconds={summary.video_seconds:.1f}")
    print(f"midpoint_slide={summary.midpoint_slide_number}")
    if summary.midpoint_slide_number not in KEY_TERM_SLIDE_NUMBERS:
        print(
            f"warning: the midpoint falls on slide {summary.midpoint_slide_number}, "
            f"not on a key term slide {KEY_TERM_SLIDE_NUMBERS}",
            file=sys.stderr,
        )
    return 0


def build_demo_video() -> BuildSummary:
    script = load_demo_script(DEMO_SCRIPT_PATH)
    require_clean_narration(script)
    toolchain = resolve_toolchain()
    reset_build_dir()
    slide_images = render_slide_images(toolchain, len(script.slides))
    segments = narrate_and_encode(toolchain, script, slide_images)
    compose_video(toolchain, segments)
    summary = accept_video(toolchain, script, segments, video=partial_video_path())
    promote_video(partial_video_path())
    return summary


def resolve_toolchain() -> Toolchain:
    environment = sanitized_child_process_environment()
    search_path = environment["PATH"]
    return Toolchain(
        typst=resolve_trusted_executable("typst", search_path=search_path),
        ffmpeg=resolve_trusted_executable("ffmpeg", search_path=search_path),
        ffprobe=resolve_trusted_executable("ffprobe", search_path=search_path),
        environment=environment,
    )


def reset_build_dir() -> None:
    """Recreate BUILD_DIR, refusing any path outside the demo workspace."""
    demo_workspace = DEMO_WORKSPACE.resolve()
    if not BUILD_DIR.resolve().is_relative_to(demo_workspace):
        raise DemoBuildError(f"build directory is outside {DEMO_WORKSPACE}")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True)


def run_tool(
    toolchain: Toolchain,
    command: Sequence[str],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_bounded_process(
        command,
        env=toolchain.environment,
        timeout_seconds=timeout_seconds,
        maximum_stdout_bytes=MAXIMUM_STDOUT_BYTES,
        maximum_stderr_bytes=MAXIMUM_STDERR_BYTES,
        cwd=cwd,
        check=True,
    )


def render_slide_images(toolchain: Toolchain, slide_count: int) -> list[Path]:
    run_tool(
        toolchain,
        slide_images_command(
            toolchain.typst, SLIDES_SOURCE_PATH, BUILD_DIR / "slide-{p}.png"
        ),
        timeout_seconds=TYPST_TIMEOUT_SECONDS,
    )
    rendered = sorted(BUILD_DIR.glob("slide-*.png"))
    if len(rendered) != slide_count:
        raise DemoBuildError(
            f"typst rendered {len(rendered)} slide images, expected {slide_count}"
        )
    return [BUILD_DIR / f"slide-{number}.png" for number in range(1, slide_count + 1)]


def narrate_and_encode(
    toolchain: Toolchain, script: DemoScript, slide_images: list[Path]
) -> list[SlideSegment]:
    segments: list[SlideSegment] = []
    for number, (slide, image) in enumerate(zip(script.slides, slide_images), start=1):
        narration_wav = BUILD_DIR / f"narration-{number:02d}.wav"
        synthesize_kokoro_wav(
            slide.narration_text(),
            voice=script.narrator_voice,
            wav_path=narration_wav,
            failure_subject=f"demo slide {number}",
        )
        segment = SlideSegment(
            slide_image=image,
            narration_wav=narration_wav,
            output=BUILD_DIR / f"segment-{number:02d}.mp4",
            narration_seconds=probe_duration(toolchain, narration_wav),
        )
        run_tool(
            toolchain,
            slide_segment_command(toolchain.ffmpeg, segment),
            timeout_seconds=FFMPEG_TIMEOUT_SECONDS,
        )
        segments.append(segment)
    return segments


def probe_duration(toolchain: Toolchain, media: Path) -> float:
    completed = run_tool(
        toolchain,
        duration_probe_command(toolchain.ffprobe, media),
        timeout_seconds=FFPROBE_TIMEOUT_SECONDS,
    )
    return parse_probed_duration(completed.stdout)


def compose_video(toolchain: Toolchain, segments: list[SlideSegment]) -> None:
    segment_names = tuple(segment.output.name for segment in segments)
    (BUILD_DIR / CONCAT_LIST_NAME).write_text(
        concat_list_text(segment_names), encoding="utf-8"
    )
    run_tool(
        toolchain,
        concat_command(toolchain.ffmpeg, CONCAT_LIST_NAME, PARTIAL_VIDEO_NAME),
        timeout_seconds=FFMPEG_TIMEOUT_SECONDS,
        cwd=BUILD_DIR,
    )


def partial_video_path() -> Path:
    return BUILD_DIR / PARTIAL_VIDEO_NAME


def promote_video(accepted_video: Path) -> None:
    """Publish an accepted video where the pipeline consumes it."""
    DEMO_VIDEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    accepted_video.replace(DEMO_VIDEO_PATH)


def accept_video(
    toolchain: Toolchain,
    script: DemoScript,
    segments: list[SlideSegment],
    *,
    video: Path,
) -> BuildSummary:
    """Accept the video only when the pipeline's own extraction and OCR
    recover every slide heading and the duration is in band."""
    frames = asyncio.run(extract_frames(video, BUILD_DIR / "frames"))
    if len(frames) < len(script.slides):
        raise DemoBuildError(
            f"frame extraction found {len(frames)} frames for "
            f"{len(script.slides)} slides"
        )
    ocr_texts = [extract_text_supervised(frame_path) for _, frame_path in frames]
    headings = tuple(slide.heading for slide in script.slides)
    absent = missing_headings(headings, ocr_texts)
    if absent:
        raise DemoBuildError(f"OCR did not recover the slide headings: {absent}")
    video_seconds = probe_duration(toolchain, video)
    require_duration_in_band(video_seconds)
    slide_seconds = tuple(segment.segment_seconds for segment in segments)
    summary = BuildSummary(
        video_seconds=video_seconds,
        slide_seconds=slide_seconds,
        frames_extracted=len(frames),
        headings_found=len(headings) - len(absent),
        heading_count=len(headings),
        midpoint_slide_number=midpoint_slide_number(slide_seconds),
    )
    BUILD_SUMMARY_PATH.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
