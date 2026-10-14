"""Podcast Audio Synthesis (Phase 15): a fully local, native text-to-speech
pass that turns one lecture's Phase 14 PodcastScript into a finished
podcast_final.m4a, using the Kokoro-82M model through `mlx-audio`
(Apple Silicon-native, MLX/Metal inference, no network call once the
model is cached) and `ffmpeg` for concatenation/transcode.

Every dialogue line's `text` originates in Phase 14's LLM pass, so it is
untrusted, unpredictably-punctuated input. Both subprocess invocations
below (`mlx_audio.tts.generate`, `ffmpeg`) are therefore built as argument
lists, never a shell string. A line like `O tutor disse "pare!" ontem;
rm -rf /` is passed to the TTS engine as one opaque positional argument,
never interpreted by a shell. See
docs/decisions/0015-local-tts-audio-generation.md for the subprocess-
isolation rationale and
docs/decisions/0032-kokoro-mlx-podcast-tts-engine.md for why `say` was
replaced by Kokoro/`mlx-audio`.
"""

import subprocess
import sys
from pathlib import Path

from pydantic import TypeAdapter

from lecture_study.domain.errors import AudioGenerationError, ScriptNotFoundError
from lecture_study.domain.models import (
    ArtifactExecutionMode,
    PodcastLine,
    PodcastScript,
    SafeIdentifier,
)
from lecture_study.quality import require_podcast_script_ready

from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    run_bounded_process,
)

PODCAST_SCRIPTS_INPUT_DIR = Path("workspace/data/15_podcast_scripts")
AUDIO_OUTPUT_DIR = Path("workspace/data/16_podcast_audio")

# Strict PT-BR voice mapping (ADR 0032): the source material is Portuguese,
# so only Kokoro's two Brazilian-Portuguese voices are used, never a
# language- or accent-mismatched default. Literal["Host", "Expert"] on
# PodcastLine.speaker (domain/models.py) makes this dict exhaustive -- no
# third speaker value can ever reach it.
#
# Replaces the macOS `say` mapping ADR 0015 introduced ("Luciana"/"Reed"):
# operator feedback (2026-09-15) called `say`'s output "robótico"; Kokoro
# is a real neural TTS model instead of a formant/concatenative system.
# "pf_dora" and "pm_alex" are confirmed present in the model's own voice
# list (`mlx-community/Kokoro-82M-bf16`, checked via `list_repo_files`)
# and resolve through `lang_code="p"` to `EspeakG2P(language='pt-br')`,
# not European Portuguese.
_VOICE_BY_SPEAKER: dict[str, str] = {"Host": "pf_dora", "Expert": "pm_alex"}

_KOKORO_MODEL_ID = "mlx-community/Kokoro-82M-bf16"
_KOKORO_LANGUAGE_CODE = "p"

_CHUNK_FILENAME_TEMPLATE = "chunk_{:03d}.wav"
_CONCAT_LIST_FILENAME = "concat_list.txt"
_FINAL_AUDIO_FILENAME = "podcast_final.m4a"
# 300s per line comfortably covers Kokoro's own measured latency (roughly
# 1-2s per line including process startup and cached-model load) with a
# wide margin for a colder cache or a slow first Hugging Face fetch.
_TTS_TIMEOUT_SECONDS = 300.0
_FFMPEG_TIMEOUT_SECONDS = 1_800.0
_MAXIMUM_STDOUT_BYTES = 1024 * 1024
_MAXIMUM_STDERR_BYTES = 8 * 1024 * 1024

_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)


class PodcastAudioGenerator:
    """Synthesizes one lecture's Phase 14 PodcastScript into a single
    podcast_final.m4a: one `mlx_audio.tts.generate` (Kokoro-82M) subprocess
    call per dialogue line (PT-BR voice selected by speaker), concatenated
    and transcoded to AAC by one `ffmpeg` subprocess call."""

    def generate_podcast(self, lecture_id: str) -> str:
        lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
        script = self._load_script(lecture_id)
        # ADR 0024: the derivative readiness call is bypassed only for a
        # script carrying the uncertified personal-study marker -- the
        # marker travels inside the script this stage already loads from
        # disk, so no new parameter threading or _run_phase_15 change is
        # needed. Every other execution mode still fails closed here,
        # exactly as today.
        if (
            script.execution_mode
            is not ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY
        ):
            require_podcast_script_ready(script)
        workspace_dir = AUDIO_OUTPUT_DIR / lecture_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        concat_list_path = workspace_dir / _CONCAT_LIST_FILENAME
        # Mutated in place by _synthesize_chunks (rather than returned)
        # so that a chunk that fails partway through the loop still
        # leaves every already-synthesized chunk's path visible to the
        # `finally` cleanup below -- a plain `chunk_paths = self.
        # _synthesize_chunks(...)` assignment would never run if that
        # call raises, silently leaking every earlier chunk's .wav file.
        chunk_paths: list[Path] = []
        try:
            self._synthesize_chunks(
                script.dialogue, workspace_dir, lecture_id, chunk_paths
            )
            final_path = self._concatenate_chunks(
                chunk_paths, concat_list_path, workspace_dir, lecture_id
            )
        finally:
            self._delete_temp_files(chunk_paths, concat_list_path)
        return str(final_path)

    @staticmethod
    def _load_script(lecture_id: str) -> PodcastScript:
        script_path = PODCAST_SCRIPTS_INPUT_DIR / lecture_id / "script.json"
        if not script_path.exists():
            raise ScriptNotFoundError(
                f"no {script_path} file for lecture {lecture_id!r}: Phase "
                "14 has not produced a podcast script for this lecture"
            )
        return PodcastScript.model_validate_json(script_path.read_text())

    @staticmethod
    def _synthesize_chunks(
        dialogue: list[PodcastLine],
        workspace_dir: Path,
        lecture_id: str,
        chunk_paths: list[Path],
    ) -> None:
        environment = sanitized_child_process_environment()
        # Invoked as a module (`python -m mlx_audio.tts.generate`), not a
        # PATH console script: the console script exists in the venv's
        # bin, but resolving it through the sanitized environment's PATH
        # fails whenever that PATH does not include the venv's bin
        # directory -- exactly how lecture 10004's audio pass failed
        # (2026-09-17). The module form needs no PATH at all.
        # sys.executable is this process's own interpreter, already
        # trusted by construction.
        tts_command_prefix = [sys.executable, "-m", "mlx_audio.tts.generate"]
        for index, line in enumerate(dialogue, start=1):
            chunk_path = workspace_dir / _CHUNK_FILENAME_TEMPLATE.format(index)
            # Appended before starting the TTS subprocess, not after: it
            # can exit non-zero after already having written a partial
            # output file (engine crash, disk full mid-write), and
            # unlink(missing_ok=True) in _delete_temp_files no-ops
            # harmlessly if the file was never created at all -- so
            # recording the path unconditionally is the only way a
            # failing chunk is ever cleaned up, not just every chunk
            # before it.
            chunk_paths.append(chunk_path)
            voice = _VOICE_BY_SPEAKER[line.speaker]
            try:
                run_bounded_process(
                    [
                        *tts_command_prefix,
                        "--model",
                        _KOKORO_MODEL_ID,
                        "--lang_code",
                        _KOKORO_LANGUAGE_CODE,
                        "--voice",
                        voice,
                        "--text",
                        line.text,
                        "--output_path",
                        str(workspace_dir),
                        "--file_prefix",
                        chunk_path.stem,
                        # `line.text` is untrusted LLM output and may
                        # contain an embedded newline; without
                        # --join_audio, Kokoro's own pipeline splits on
                        # "\n+" internally and writes one file per
                        # resulting segment (chunk_001_000.wav,
                        # chunk_001_001.wav, ...), silently dropping every
                        # segment past the first from this pipeline's
                        # output since only "_000" was ever read.
                        # --join_audio merges every internal segment back
                        # into one file, always named exactly
                        # "{file_prefix}.wav" (== chunk_path here) with no
                        # numeric suffix regardless of how many internal
                        # segments the text produced -- confirmed by
                        # direct invocation with a "\n"-containing line
                        # before adoption.
                        "--join_audio",
                    ],
                    env=environment,
                    timeout_seconds=_TTS_TIMEOUT_SECONDS,
                    maximum_stdout_bytes=_MAXIMUM_STDOUT_BYTES,
                    maximum_stderr_bytes=_MAXIMUM_STDERR_BYTES,
                    check=True,
                )
                if not chunk_path.exists():
                    raise AudioGenerationError(
                        f"mlx_audio TTS synthesis failed for lecture "
                        f"{lecture_id!r} dialogue line {index} (voice "
                        f"{voice!r}): expected output file {chunk_path} "
                        "was not created"
                    )
            except (
                OSError,
                ProcessOutputLimitExceeded,
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ) as error:
                raise AudioGenerationError(
                    f"mlx_audio TTS synthesis failed for lecture {lecture_id!r} "
                    f"dialogue line {index} (voice {voice!r}): {error}"
                ) from error

    @staticmethod
    def _concatenate_chunks(
        chunk_paths: list[Path],
        concat_list_path: Path,
        workspace_dir: Path,
        lecture_id: str,
    ) -> Path:
        concat_list_path.write_text(
            "".join(f"file '{chunk_path.name}'\n" for chunk_path in chunk_paths)
        )
        final_path = workspace_dir / _FINAL_AUDIO_FILENAME
        temporary_filename = ".podcast_final.partial.m4a"
        temporary_path = workspace_dir / temporary_filename
        temporary_path.unlink(missing_ok=True)
        environment = sanitized_child_process_environment()
        try:
            ffmpeg_executable = resolve_trusted_executable(
                "ffmpeg",
                search_path=environment.get("PATH"),
            )
            run_bounded_process(
                [
                    ffmpeg_executable,
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    _CONCAT_LIST_FILENAME,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    temporary_filename,
                ],
                cwd=workspace_dir,
                env=environment,
                timeout_seconds=_FFMPEG_TIMEOUT_SECONDS,
                maximum_stdout_bytes=_MAXIMUM_STDOUT_BYTES,
                maximum_stderr_bytes=_MAXIMUM_STDERR_BYTES,
                check=True,
            )
            temporary_path.replace(final_path)
        except (
            OSError,
            ProcessOutputLimitExceeded,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as error:
            temporary_path.unlink(missing_ok=True)
            raise AudioGenerationError(
                f"ffmpeg concatenation failed for lecture {lecture_id!r}: {error}"
            ) from error
        return final_path

    @staticmethod
    def _delete_temp_files(chunk_paths: list[Path], concat_list_path: Path) -> None:
        for chunk_path in chunk_paths:
            chunk_path.unlink(missing_ok=True)
        concat_list_path.unlink(missing_ok=True)
