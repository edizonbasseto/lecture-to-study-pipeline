"""Async FFmpeg wrappers: Whisper-ready audio extraction and visual frame
extraction.

Both wrappers run FFmpeg via asyncio.create_subprocess_exec so the event
loop is never blocked waiting on the external process.
"""

import asyncio
import re
from pathlib import Path

# ADR 0021: the 0.3 scene threshold suppresses clock-overlay noise, unchanged
# by ADR 0025. ADR 0021 originally fixed the temporal safety interval at 150
# seconds -- half the minimum Learning Unit duration, preventing a qualifying
# unit from having no visual candidate solely because its video contains
# gradual or continuous motion. ADR 0025 lowers that interval to 37.5
# seconds, a quarter of the original value, after lecture 10002's
# continuous-motion recording produced only ten frames at 150 seconds.
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    communicate_bounded_and_reap,
)

_MAXIMUM_VISUAL_FRAME_GAP_SECONDS = 37.5
_VISUAL_FRAME_FILTER = (
    "select='isnan(prev_selected_t)+gt(scene,0.3)"
    f"+gte(t-prev_selected_t,{_MAXIMUM_VISUAL_FRAME_GAP_SECONDS})',showinfo"
)
_FRAME_LOG_PATTERN = re.compile(
    r"n:\s*(?P<index>\d+).*?pts_time:(?P<pts_time>\d+(?:\.\d+)?)"
)
_SEQUENTIAL_FRAME_GLOB = "frame_[0-9][0-9][0-9][0-9].png"
_FFMPEG_TIMEOUT_SECONDS = 7_200.0
_MAXIMUM_FFMPEG_STDOUT_BYTES = 1024 * 1024
_MAXIMUM_FFMPEG_STDERR_BYTES = 64 * 1024 * 1024


class MediaExtractionError(RuntimeError):
    """Raised when an FFmpeg invocation fails or produces unusable output."""


async def _run_ffmpeg(args: list[str]) -> bytes:
    """Run ffmpeg with args to completion, returning its stderr output."""
    environment = sanitized_child_process_environment()
    try:
        executable = resolve_trusted_executable(
            "ffmpeg",
            search_path=environment.get("PATH"),
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            env=sanitized_child_process_environment(),
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise MediaExtractionError(
            "ffmpeg executable is missing or is not a trusted regular file"
        ) from error

    try:
        _, stderr = await communicate_bounded_and_reap(
            process,
            maximum_stdout_bytes=_MAXIMUM_FFMPEG_STDOUT_BYTES,
            maximum_stderr_bytes=_MAXIMUM_FFMPEG_STDERR_BYTES,
            timeout_seconds=_FFMPEG_TIMEOUT_SECONDS,
        )
    except (ProcessOutputLimitExceeded, TimeoutError) as error:
        raise MediaExtractionError(
            f"ffmpeg execution exceeded its limit: {error}"
        ) from error
    if process.returncode != 0:
        raise MediaExtractionError(
            f"ffmpeg exited with code {process.returncode}: "
            f"{stderr.decode(errors='replace')}"
        )
    return stderr


async def extract_audio(video_path: Path, output_dir: Path) -> Path:
    """Extract mono 16kHz PCM16 audio from video_path into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{video_path.stem}.wav"
    await _run_ffmpeg(
        [
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output_path),
        ]
    )
    return output_path


async def extract_frames(
    video_path: Path, output_dir: Path
) -> list[tuple[float, Path]]:
    """Extract scene-change and temporal safety frames from a video.

    FFmpeg writes matching frames sequentially as frame_0001.png,
    frame_0002.png, ... in temporal order; it cannot embed the source
    media timestamp in the filename itself. This wrapper recovers each
    frame's index and exact timestamp from the showinfo filter's log
    (stderr) and renames each file to embed its timestamp, e.g.
    frame_0045.500.png, after checking that the log's frame indices match
    the files actually on disk and that no two timestamps would format to
    the same filename (which would silently overwrite one frame with
    another).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # -fps_mode (2026-09-16): ffmpeg 9.0.1 dropped the older -vsync flag
    # entirely ("Unrecognized option") rather than keeping it as a
    # deprecated alias -- -fps_mode vfr is its direct replacement, same
    # variable-frame-rate behavior this scene-change frame extraction
    # depends on.
    stderr = await _run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vf",
            _VISUAL_FRAME_FILTER,
            "-fps_mode",
            "vfr",
            "-y",
            str(output_dir / "frame_%04d.png"),
        ]
    )

    logged_frames = [
        (int(match["index"]), float(match["pts_time"]))
        for match in _FRAME_LOG_PATTERN.finditer(stderr.decode(errors="replace"))
    ]
    sequential_files = sorted(output_dir.glob(_SEQUENTIAL_FRAME_GLOB))

    if len(logged_frames) != len(sequential_files):
        raise MediaExtractionError(
            f"expected {len(logged_frames)} extracted frame(s) per the ffmpeg "
            f"visual frame log, found {len(sequential_files)} file(s) on disk"
        )

    expected_names = {f"frame_{index + 1:04d}.png" for index, _ in logged_frames}
    actual_names = {sequential_file.name for sequential_file in sequential_files}
    if expected_names != actual_names:
        raise MediaExtractionError(
            "ffmpeg visual frame log indices do not match the sequential files "
            f"on disk: expected {sorted(expected_names)}, found "
            f"{sorted(actual_names)}"
        )

    target_names = [f"frame_{timestamp:08.3f}.png" for _, timestamp in logged_frames]
    if len(set(target_names)) != len(target_names):
        raise MediaExtractionError(
            "extracted frame timestamps collide once formatted into "
            f"filenames, which would overwrite one frame with another: "
            f"{target_names}"
        )

    frames: list[tuple[float, Path]] = []
    for (index, timestamp), target_name in zip(logged_frames, target_names):
        sequential_file = output_dir / f"frame_{index + 1:04d}.png"
        timestamped_file = output_dir / target_name
        sequential_file.rename(timestamped_file)
        frames.append((timestamp, timestamped_file))
    return frames
