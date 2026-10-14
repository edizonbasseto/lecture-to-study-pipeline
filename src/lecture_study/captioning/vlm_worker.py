"""Async llama.cpp (Metal) vision captioning worker.

Runs the vendored multimodal binary (`llama-mtmd-cli`, per the recorded
capability verdict) as an async subprocess, the same way the
retired semantic text-LLM worker ran its own binary (module deleted
under ADR 0038): the same trusted-
executable resolution, the same sanitized child environment, the same
bounded-communication helper, and the same response-contract parsing with
a single feedback-steered retry. No subprocess reader, JSON extractor, or
retry pattern is reimplemented here, per the research's do-not-hand-roll
table.

Captioning is strictly serial (D-11): `caption_lecture_figures` awaits one
frame at a time, never concurrently, because Metal-bound inference has no
cross-process lock and this codebase's own convention keeps it globally
serialized rather than trading that safety away for throughput.
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter

from lecture_study.captioning.catalog_store import (
    EXTRACTED_SLIDES_INPUT_DIR,
    discover_lecture_frames,
    frame_timestamp_seconds,
)
from lecture_study.domain.models import (
    ArtifactExecutionMode,
    FigureCaption,
    LectureFigureCatalog,
)
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    build_bounded_retry_prompt_section,
    build_llm_retry_feedback,
    llm_output_diagnostic,
    parse_llm_json_response,
)
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import communicate_bounded_and_reap

VLM_BINARY = "engines/llama.cpp/build/bin/llama-mtmd-cli"
_MAXIMUM_STDOUT_BYTES = 4 * 1024 * 1024
_MAXIMUM_STDERR_BYTES = 1024 * 1024
_CAPTION_TIMEOUT_SECONDS = 10 * 60.0
# Matches the context window the capability probe confirmed the pinned
# llama-mtmd-cli build accepts for this actor (01-CAPABILITIES.md).
_CAPTION_CONTEXT_TOKENS = 4096
_CAPTION_OUTPUT_TOKENS = 512
# A frame wider than the context window (observed: a 2880x1800 screen
# recording needs ~5000 image tokens) makes llama-mtmd-cli exit 1 before
# any output. The cap leaves room for the instruction and the generated
# caption, and sits above the ~2040 tokens a 1920x1080 frame needs, so
# frames that already fit are captioned exactly as before.
_CAPTION_IMAGE_MAX_TOKENS = 3072

_CAPTION_INSTRUCTION = (
    "Descreva em portugues do Brasil (pt-BR), de forma objetiva e literal, "
    "o que esta imagem mostra: estruturas anatomicas visiveis, instrumentos, "
    "texto de slide, ou o cenario. Use APENAS o que a imagem, por si so, "
    "exibe -- nunca conhecimento clinico externo. Nao afirme um diagnostico, "
    "uma indicacao, uma contraindicacao ou qualquer outra alegacao clinica "
    "que a imagem nao demonstre visualmente. "
    "Responda com um objeto JSON no formato exato "
    '{"caption": "<descricao>"}, sem markdown, sem explicacao adicional.'
)


class _CaptionResponse(BaseModel):
    """The vision model's only permitted response shape: a bare caption
    string. No timestamp field exists here -- a returned frame's timestamp
    always comes from its own filename, never from the model."""

    model_config = ConfigDict(strict=True)

    caption: str


_CAPTION_RESPONSE_ADAPTER: TypeAdapter[_CaptionResponse] = TypeAdapter(_CaptionResponse)


class VlmCaptioningError(RuntimeError):
    """Raised when a vision-model invocation fails or produces unusable output."""


async def caption_frame(
    frame_path: Path,
    lecture_id: str,
    model_path: Path,
    mmproj_path: Path,
) -> FigureCaption:
    """Caption one extracted frame, with a single feedback-steered retry
    if the first attempt fails to parse."""
    timestamp_seconds = frame_timestamp_seconds(frame_path)
    try:
        caption_text = await _attempt_caption_frame(
            frame_path,
            model_path,
            mmproj_path,
            feedback=None,
        )
    except VlmCaptioningError as first_error:
        try:
            caption_text = await _attempt_caption_frame(
                frame_path,
                model_path,
                mmproj_path,
                feedback=build_llm_retry_feedback(first_error),
            )
        except VlmCaptioningError as second_error:
            raise VlmCaptioningError(
                f"captioning frame {frame_path.name!r} failed on both the "
                f"original attempt and its retry: {second_error}"
            ) from second_error
    return FigureCaption(
        lecture_id=lecture_id,
        frame_filename=frame_path.name,
        timestamp_seconds=timestamp_seconds,
        caption=caption_text,
        execution_mode=ArtifactExecutionMode.PIPELINE_DEFAULT,
    )


async def caption_lecture_figures(
    lecture_id: str,
    model_path: Path,
    mmproj_path: Path,
    frames_root: Path = EXTRACTED_SLIDES_INPUT_DIR,
) -> LectureFigureCatalog:
    """Caption every discovered frame of one lecture, strictly one at a
    time, in timestamp order. A frame whose captioning fails is reported
    with its own filename and aborts the lecture -- it is never silently
    dropped from the catalog and the lecture never continues with a
    quietly incomplete result."""
    frame_paths = discover_lecture_frames(lecture_id, frames_root=frames_root)
    captions: list[FigureCaption] = []
    for frame_path in frame_paths:
        try:
            captions.append(
                await caption_frame(frame_path, lecture_id, model_path, mmproj_path)
            )
        except VlmCaptioningError as error:
            raise VlmCaptioningError(
                f"captioning failed for frame {frame_path.name!r} of lecture "
                f"{lecture_id!r}: {error}"
            ) from error
    return LectureFigureCatalog(
        lecture_id=lecture_id,
        figures=captions,
        captioned_at=time.time(),
    )


async def _attempt_caption_frame(
    frame_path: Path,
    model_path: Path,
    mmproj_path: Path,
    feedback: str | None,
) -> str:
    """Single vision-model invocation attempt underlying caption_frame.

    The prompt is written to a temporary file -- matching Phase 3/5's
    ephemeral-artifact cleanup discipline -- and always removed on exit,
    success or failure alike.
    """
    prompt_path: Path | None = None
    try:
        prompt_path = _write_temp_file(_build_prompt(feedback), suffix=".txt")
        process = await _start_vlm_process(
            model_path,
            mmproj_path,
            frame_path,
            prompt_path,
        )
        stdout, stderr = await communicate_bounded_and_reap(
            process,
            maximum_stdout_bytes=_MAXIMUM_STDOUT_BYTES,
            maximum_stderr_bytes=_MAXIMUM_STDERR_BYTES,
            timeout_seconds=_CAPTION_TIMEOUT_SECONDS,
        )
        if process.returncode != 0:
            raise VlmCaptioningError(
                f"llama-mtmd-cli exited with code {process.returncode}: "
                f"{llm_output_diagnostic(stderr)}"
            )
        return _parse_caption(stdout)
    finally:
        if prompt_path is not None:
            prompt_path.unlink(missing_ok=True)


def _build_prompt(feedback: str | None) -> str:
    schema = json.dumps(_CAPTION_RESPONSE_ADAPTER.json_schema())
    prompt = (
        f"{_CAPTION_INSTRUCTION}\n\n"
        f"JSON Schema (the response MUST validate against this schema "
        f"exactly):\n{schema}\n\n"
        "Respond with ONLY the JSON object matching the schema above -- "
        "no explanation, no markdown fences, no extra text."
    )
    if feedback is not None:
        prompt += build_bounded_retry_prompt_section(
            heading=(
                "A previous attempt did not produce a valid JSON object "
                "matching the schema -- correct this in the new attempt:"
            ),
            feedback=feedback,
        )
    return prompt


def _write_temp_file(content: str, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(suffix=suffix)
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(content)
    except BaseException:
        # mkstemp already created the file on disk; a write failure must
        # not leave it orphaned, since the caller never learns this path
        # to clean it up itself.
        path.unlink(missing_ok=True)
        raise
    return path


async def _start_vlm_process(
    model_path: Path,
    mmproj_path: Path,
    frame_path: Path,
    prompt_path: Path,
) -> asyncio.subprocess.Process:
    try:
        environment = sanitized_child_process_environment()
        executable = resolve_trusted_executable(
            VLM_BINARY,
            search_path=environment.get("PATH"),
        )
        return await asyncio.create_subprocess_exec(
            executable,
            "-m",
            str(model_path),
            "--mmproj",
            str(mmproj_path),
            "--image",
            str(frame_path),
            "-c",
            str(_CAPTION_CONTEXT_TOKENS),
            "--temp",
            "0.0",
            "-n",
            str(_CAPTION_OUTPUT_TOKENS),
            "--image-max-tokens",
            str(_CAPTION_IMAGE_MAX_TOKENS),
            "-f",
            str(prompt_path),
            env=sanitized_child_process_environment(environment),
            start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise VlmCaptioningError(
            f"vision model executable is missing or untrusted at {VLM_BINARY}"
        ) from error


def _parse_caption(stdout: bytes) -> str:
    try:
        response = parse_llm_json_response(
            stdout,
            expected_kind="object",
            response_adapter=_CAPTION_RESPONSE_ADAPTER,
            declared_type=_CaptionResponse,
        )
    except LlmResponseContractError as error:
        raise VlmCaptioningError(f"llama-mtmd-cli produced {error}") from error
    return response.caption
