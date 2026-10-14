"""Async whisper.cpp (Metal) transcription worker.

Runs the compute-bound whisper.cpp binary via asyncio.create_subprocess_exec
so the event loop is never blocked waiting on the external process.
"""

import asyncio
import json
from pathlib import Path

from lecture_study.domain.models import TranscriptionSegment

from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.transcription.terminology import DOMAIN_VOCABULARY_PROMPT

from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    communicate_bounded_and_reap,
)

WHISPER_BINARY = "engines/whisper.cpp/build/bin/whisper-cli"
_WHISPER_TIMEOUT_SECONDS = 14_400.0
_MAXIMUM_WHISPER_STDOUT_BYTES = 1024 * 1024
_MAXIMUM_WHISPER_STDERR_BYTES = 16 * 1024 * 1024


class WhisperTranscriptionError(RuntimeError):
    """Raised when a whisper.cpp invocation fails or produces unusable output."""


async def run_transcription(
    audio_path: Path,
    model_path: Path,
    initial_prompt: str = DOMAIN_VOCABULARY_PROMPT,
) -> list[TranscriptionSegment]:
    """Transcribe audio_path with whisper.cpp's model_path model, returning
    one TranscriptionSegment per segment whisper.cpp detected.

    initial_prompt states the lecture's domain vocabulary and is carried
    into every decoding window, which is what stops the model truncating a
    specialist term's first syllable ("cisional" for excisional). See
    transcription/terminology.py for the measured evidence.

    whisper.cpp's -oj flag writes segments as {audio_path}.json next to the
    input file. That file is ephemeral, not part of this pipeline's output
    contract, so it is deleted on every exit path here (success, a
    non-zero whisper.cpp exit that still wrote a partial file, or a parse
    failure) rather than only after a successful parse.
    """
    json_output_path = audio_path.parent / f"{audio_path.name}.json"
    try:
        process = await _start_whisper_process(audio_path, model_path, initial_prompt)
        try:
            _, stderr = await communicate_bounded_and_reap(
                process,
                maximum_stdout_bytes=_MAXIMUM_WHISPER_STDOUT_BYTES,
                maximum_stderr_bytes=_MAXIMUM_WHISPER_STDERR_BYTES,
                timeout_seconds=_WHISPER_TIMEOUT_SECONDS,
            )
        except (ProcessOutputLimitExceeded, TimeoutError) as error:
            raise WhisperTranscriptionError(
                f"whisper.cpp execution exceeded its limit: {error}"
            ) from error
        if process.returncode != 0:
            raise WhisperTranscriptionError(
                f"whisper.cpp exited with code {process.returncode}: "
                f"{stderr.decode(errors='replace')}"
            )
        return _parse_whisper_json(json_output_path)
    finally:
        json_output_path.unlink(missing_ok=True)


async def _start_whisper_process(
    audio_path: Path, model_path: Path, initial_prompt: str
) -> asyncio.subprocess.Process:
    try:
        executable = resolve_trusted_executable(WHISPER_BINARY)
        return await asyncio.create_subprocess_exec(
            executable,
            "-m",
            str(model_path),
            "-f",
            str(audio_path),
            "-oj",
            "-l",
            "pt",
            "--prompt",
            initial_prompt,
            "--carry-initial-prompt",
            env=sanitized_child_process_environment(),
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise WhisperTranscriptionError(
            f"whisper.cpp executable is missing or untrusted at {WHISPER_BINARY}"
        ) from error


def _parse_whisper_json(json_output_path: Path) -> list[TranscriptionSegment]:
    if not json_output_path.exists():
        raise WhisperTranscriptionError(
            f"whisper.cpp reported success but produced no json output at "
            f"{json_output_path}"
        )
    try:
        payload = json.loads(json_output_path.read_text())
        segments = [
            TranscriptionSegment(
                start_time=entry["offsets"]["from"] / 1000.0,
                end_time=entry["offsets"]["to"] / 1000.0,
                text=entry["text"].strip(),
            )
            for entry in payload["transcription"]
        ]
        return _normalize_boundary_point_segments(segments)
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
        raise WhisperTranscriptionError(
            f"whisper.cpp produced malformed json output at {json_output_path}: {error}"
        ) from error


MAXIMUM_TRAILING_REMARK_GAP_SECONDS = 10.0


def _normalize_boundary_point_segments(
    segments: list[TranscriptionSegment],
) -> list[TranscriptionSegment]:
    normalized: list[TranscriptionSegment] = []
    pending_prefix = ""
    for index, segment in enumerate(segments):
        if segment.end_time > segment.start_time:
            if pending_prefix:
                segment = segment.model_copy(
                    update={"text": f"{pending_prefix} {segment.text}"}
                )
                pending_prefix = ""
            normalized.append(segment)
            continue
        if normalized and segment.start_time == normalized[-1].end_time:
            previous = normalized[-1]
            if previous.text.endswith(segment.text):
                continue
            normalized[-1] = _append_text(previous, segment.text)
            continue
        next_segment = segments[index + 1] if index + 1 < len(segments) else None
        if (
            next_segment is not None
            and next_segment.end_time > next_segment.start_time
            and segment.start_time == next_segment.start_time
        ):
            pending_prefix = " ".join(
                part for part in (pending_prefix, segment.text) if part
            )
            continue
        if (
            next_segment is not None
            and next_segment.start_time > segment.start_time
            and (not normalized or normalized[-1].end_time <= segment.start_time)
        ):
            normalized.append(
                segment.model_copy(update={"end_time": next_segment.start_time})
            )
            continue
        if next_segment is None and _follows_previous_segment(segment, normalized):
            if not normalized[-1].text.endswith(segment.text):
                normalized[-1] = _append_text(normalized[-1], segment.text)
            continue
        raise WhisperTranscriptionError(
            "whisper.cpp produced a zero-duration segment outside an adjacent boundary"
        )
    if pending_prefix:
        raise WhisperTranscriptionError(
            "whisper.cpp produced a terminal zero-duration segment without a boundary"
        )
    return normalized


def _follows_previous_segment(
    segment: TranscriptionSegment, normalized: list[TranscriptionSegment]
) -> bool:
    """True for a trailing point segment that starts at or after the end of the
    last kept segment, i.e. whisper.cpp's closing remark after a silent gap."""
    if not normalized:
        return False
    gap = segment.start_time - normalized[-1].end_time
    return 0 <= gap <= MAXIMUM_TRAILING_REMARK_GAP_SECONDS


def _append_text(previous: TranscriptionSegment, text: str) -> TranscriptionSegment:
    return previous.model_copy(update={"text": f"{previous.text} {text}"})
