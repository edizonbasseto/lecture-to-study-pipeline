"""Podcast Audio Synthesis (Phase 15) tests: the local
`mlx_audio.tts.generate`/`ffmpeg` subprocess pipeline that turns one
lecture's Phase 14 PodcastScript into podcast_final.m4a. All subprocess
calls are mocked; no real TTS or `ffmpeg` invocation happens in this
suite."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lecture_study.domain.errors import (
    AudioGenerationError,
    LectureNotFoundError,
    ScriptNotFoundError,
)
from lecture_study.domain.models import (
    ArtifactExecutionMode,
    PodcastLine,
    PodcastScript,
)
from lecture_study.quality.derivative import DerivativeQualityError
from lecture_study.tts import generator as generator_module
from lecture_study.tts.generator import PodcastAudioGenerator


pytestmark = pytest.mark.usefixtures("legacy_certification_bypass")


def _line(
    speaker: str = "Host",
    text: str = "O que aconteceu com o paciente de 8 anos?",
    source_unit_id: str = "unit-001",
) -> PodcastLine:
    return PodcastLine(speaker=speaker, text=text, source_unit_id=source_unit_id)


def _make_script(
    lecture_id: str = "aula-001", dialogue: list[PodcastLine] | None = None
) -> PodcastScript:
    return PodcastScript(
        lecture_id=lecture_id,
        title="Bradicardia em caes idosos",
        dialogue=dialogue
        if dialogue is not None
        else [
            _line(speaker="Host"),
            _line(speaker="Expert", text="A bradicardia sinusal exige monitoramento."),
        ],
    )


def _write_script(tmp_path: Path, script: PodcastScript) -> None:
    lecture_dir = tmp_path / script.lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / "script.json").write_text(script.model_dump_json())


def _generated_chunk_path(command: list[str]) -> Path:
    """The exact path `mlx_audio.tts.generate --join_audio` writes to for
    a given command: `{output_path}/{file_prefix}.wav`, always a single
    file regardless of how many internal segments the text produced (see
    tts/generator.py's comment on why --join_audio is required)."""
    output_path = Path(command[command.index("--output_path") + 1])
    file_prefix = command[command.index("--file_prefix") + 1]
    return output_path / f"{file_prefix}.wav"


def _is_tts_command(command: list[str]) -> bool:
    """The TTS stage runs as `python -m mlx_audio.tts.generate` (mlx-audio
    0.5.x dropped the console script), so match the module flag, not
    argv[0]."""
    return len(command) >= 3 and command[1:3] == ["-m", "mlx_audio.tts.generate"]


def _matches_target_command(command: list[str], target: str) -> bool:
    if target == "mlx_audio.tts.generate":
        return _is_tts_command(command)
    return Path(command[0]).name == target


def _fake_run_writing_outputs(
    *args: Any, **kwargs: Any
) -> subprocess.CompletedProcess[bytes]:
    """Stand-in for bounded execution that behaves like
    `python -m mlx_audio.tts.generate`/`ffmpeg` would: writes an (empty,
    content-irrelevant) file at whichever path the real command would
    have produced, so tests can assert on the resulting filesystem
    state."""
    command = args[0]
    if _is_tts_command(command):
        _generated_chunk_path(command).write_bytes(b"fake-wav")
    elif Path(command[0]).name == "ffmpeg":
        cwd = Path(kwargs["cwd"])
        output_name = command[-1]
        (cwd / output_name).write_bytes(b"fake-m4a")
    return subprocess.CompletedProcess(command, 0)


def _patch_run(monkeypatch: pytest.MonkeyPatch, side_effect: Any) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(args[0]))
        return side_effect(*args, **kwargs)

    monkeypatch.setattr(generator_module, "run_bounded_process", fake_run)
    return calls


def _patch_run_failing_on(
    monkeypatch: pytest.MonkeyPatch, target_command: str, error: Exception
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(args[0]))
        if _matches_target_command(args[0], target_command):
            raise error
        return _fake_run_writing_outputs(*args, **kwargs)

    monkeypatch.setattr(generator_module, "run_bounded_process", fake_run)
    return calls


@pytest.fixture(autouse=True)
def _patch_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    scripts_dir = tmp_path / "scripts"
    audio_dir = tmp_path / "audio"
    scripts_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(generator_module, "PODCAST_SCRIPTS_INPUT_DIR", scripts_dir)
    monkeypatch.setattr(generator_module, "AUDIO_OUTPUT_DIR", audio_dir)
    monkeypatch.setattr(
        generator_module,
        "resolve_trusted_executable",
        lambda executable, **kwargs: f"/trusted/{executable}",
    )
    return scripts_dir, audio_dir


class TestScriptNotFoundError:
    def test_is_a_lecture_not_found_error_subclass(self):
        assert issubclass(ScriptNotFoundError, LectureNotFoundError)


class TestPodcastAudioGeneratorGeneratePodcast:
    def test_returns_final_audio_path_and_creates_output_directory(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, audio_dir = _patch_dirs
        _write_script(scripts_dir, _make_script())
        _patch_run(monkeypatch, _fake_run_writing_outputs)

        result = PodcastAudioGenerator().generate_podcast("aula-001")

        assert result == str(audio_dir / "aula-001" / "podcast_final.m4a")
        assert Path(result).exists()

    def test_raises_script_not_found_when_script_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        with pytest.raises(ScriptNotFoundError, match="aula-001"):
            PodcastAudioGenerator().generate_podcast("aula-001")

    def test_rejects_malformed_lecture_id_before_touching_disk(self, _patch_dirs):
        scripts_dir, audio_dir = _patch_dirs

        with pytest.raises(ValidationError):
            PodcastAudioGenerator().generate_podcast("../etc/passwd")

        assert not any(audio_dir.iterdir())

    def test_maps_host_and_expert_lines_to_pf_dora_and_pm_alex_voices(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, _ = _patch_dirs
        _write_script(
            scripts_dir,
            _make_script(
                dialogue=[
                    _line(speaker="Host", text="Pergunta do host."),
                    _line(speaker="Expert", text="Resposta do expert."),
                ]
            ),
        )
        calls = _patch_run(monkeypatch, _fake_run_writing_outputs)

        PodcastAudioGenerator().generate_podcast("aula-001")

        tts_calls = [call for call in calls if _is_tts_command(call)]
        assert tts_calls[0][tts_calls[0].index("--voice") + 1] == "pf_dora"
        assert tts_calls[1][tts_calls[1].index("--voice") + 1] == "pm_alex"

    def test_builds_strict_tts_command_per_chunk(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, audio_dir = _patch_dirs
        _write_script(scripts_dir, _make_script(dialogue=[_line(text="Ola mundo.")]))
        calls = _patch_run(monkeypatch, _fake_run_writing_outputs)

        PodcastAudioGenerator().generate_podcast("aula-001")

        chunk_dir = audio_dir / "aula-001"
        assert calls[0] == [
            sys.executable,
            "-m",
            "mlx_audio.tts.generate",
            "--model",
            "mlx-community/Kokoro-82M-bf16",
            "--lang_code",
            "p",
            "--voice",
            "pf_dora",
            "--text",
            "Ola mundo.",
            "--output_path",
            str(chunk_dir),
            "--file_prefix",
            "chunk_001",
            "--join_audio",
        ]

    def test_passes_join_audio_for_a_line_containing_an_embedded_newline(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        """Regression: `PodcastLine.text` is untrusted LLM output with no
        guard against an embedded newline. Without `--join_audio`,
        Kokoro's own pipeline splits internally on "\\n+" and writes one
        file per resulting segment ("chunk_001_000.wav",
        "chunk_001_001.wav", ...) -- this code only ever reads
        "chunk_001.wav" (the --join_audio output name), so every segment
        past the first would be silently dropped from the final episode
        without `--join_audio` merging them back into one file first.
        Confirmed against the real `mlx_audio.tts.generate` binary before
        adoption (see ADR 0032) that `--join_audio` always produces
        exactly one file, "{file_prefix}.wav", regardless of how many
        internal segments the text produced."""
        scripts_dir, audio_dir = _patch_dirs
        _write_script(
            scripts_dir,
            _make_script(
                dialogue=[_line(text="Primeira linha.\nSegunda linha após quebra.")]
            ),
        )
        calls = _patch_run(monkeypatch, _fake_run_writing_outputs)

        result = PodcastAudioGenerator().generate_podcast("aula-001")

        assert "--join_audio" in calls[0]
        assert Path(result).exists()

    def test_passes_dangerous_text_as_a_single_safe_argument_never_a_shell_string(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        """P0 critical test: a dialogue line containing quotes, a
        semicolon, and a backtick must reach the TTS engine as one intact
        list element -- proving the argument-list form (shell=False,
        never overridden) is actually used, not string-built and handed
        to a shell."""
        scripts_dir, _ = _patch_dirs
        dangerous_text = 'O tutor disse "pare!" ontem; rm -rf / ; `echo pwned`'
        _write_script(scripts_dir, _make_script(dialogue=[_line(text=dangerous_text)]))
        calls = _patch_run(monkeypatch, _fake_run_writing_outputs)

        PodcastAudioGenerator().generate_podcast("aula-001")

        tts_call = calls[0]
        assert tts_call[tts_call.index("--text") + 1] == dangerous_text
        assert len(tts_call) == 16

    def test_builds_strict_ffmpeg_command(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, _ = _patch_dirs
        _write_script(scripts_dir, _make_script())
        calls = _patch_run(monkeypatch, _fake_run_writing_outputs)

        PodcastAudioGenerator().generate_podcast("aula-001")

        ffmpeg_call = calls[-1]
        assert ffmpeg_call == [
            "/trusted/ffmpeg",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            "concat_list.txt",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            ".podcast_final.partial.m4a",
        ]

    def test_all_subprocesses_have_finite_time_and_output_budgets(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _patch_dirs,
    ) -> None:
        scripts_dir, _ = _patch_dirs
        _write_script(scripts_dir, _make_script())
        invocations: list[dict[str, Any]] = []

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            invocations.append(kwargs)
            return _fake_run_writing_outputs(*args, **kwargs)

        monkeypatch.setattr(generator_module, "run_bounded_process", fake_run)

        PodcastAudioGenerator().generate_podcast("aula-001")

        assert invocations
        assert all(invocation["timeout_seconds"] > 0 for invocation in invocations)
        assert all(invocation["maximum_stdout_bytes"] > 0 for invocation in invocations)
        assert all(invocation["maximum_stderr_bytes"] > 0 for invocation in invocations)

    def test_concat_list_lists_chunks_in_speaking_order(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, audio_dir = _patch_dirs
        _write_script(
            scripts_dir,
            _make_script(
                dialogue=[
                    _line(speaker="Host", text="Um."),
                    _line(speaker="Expert", text="Dois."),
                    _line(speaker="Host", text="Tres."),
                ]
            ),
        )
        written_concat_list = ""

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            nonlocal written_concat_list
            if Path(args[0][0]).name == "ffmpeg":
                written_concat_list = (
                    Path(kwargs["cwd"]) / "concat_list.txt"
                ).read_text()
            return _fake_run_writing_outputs(*args, **kwargs)

        monkeypatch.setattr(generator_module, "run_bounded_process", fake_run)

        PodcastAudioGenerator().generate_podcast("aula-001")

        assert written_concat_list == (
            "file 'chunk_001.wav'\nfile 'chunk_002.wav'\nfile 'chunk_003.wav'\n"
        )

    def test_deletes_only_wav_and_txt_temp_files_after_success(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, audio_dir = _patch_dirs
        _write_script(scripts_dir, _make_script())
        _patch_run(monkeypatch, _fake_run_writing_outputs)

        PodcastAudioGenerator().generate_podcast("aula-001")

        remaining = sorted(path.name for path in (audio_dir / "aula-001").iterdir())
        assert remaining == ["podcast_final.m4a"]

    def test_raises_audio_generation_error_when_tts_spawn_fails(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, _ = _patch_dirs
        _write_script(scripts_dir, _make_script())
        _patch_run_failing_on(
            monkeypatch,
            "mlx_audio.tts.generate",
            FileNotFoundError("no such file: mlx_audio.tts.generate"),
        )

        with pytest.raises(
            AudioGenerationError, match="mlx_audio TTS synthesis failed"
        ):
            PodcastAudioGenerator().generate_podcast("aula-001")

    def test_raises_audio_generation_error_when_tts_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, _ = _patch_dirs
        _write_script(scripts_dir, _make_script())
        _patch_run_failing_on(
            monkeypatch,
            "mlx_audio.tts.generate",
            subprocess.CalledProcessError(1, ["mlx_audio.tts.generate"]),
        )

        with pytest.raises(
            AudioGenerationError, match="mlx_audio TTS synthesis failed"
        ):
            PodcastAudioGenerator().generate_podcast("aula-001")

    def test_raises_audio_generation_error_when_ffmpeg_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, _ = _patch_dirs
        _write_script(scripts_dir, _make_script())
        _patch_run_failing_on(
            monkeypatch, "ffmpeg", FileNotFoundError("no such file: ffmpeg")
        )

        with pytest.raises(AudioGenerationError, match="ffmpeg concatenation failed"):
            PodcastAudioGenerator().generate_podcast("aula-001")

    def test_raises_audio_generation_error_when_ffmpeg_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, _ = _patch_dirs
        _write_script(scripts_dir, _make_script())
        _patch_run_failing_on(
            monkeypatch,
            "ffmpeg",
            subprocess.CalledProcessError(1, ["ffmpeg"]),
        )

        with pytest.raises(AudioGenerationError, match="ffmpeg concatenation failed"):
            PodcastAudioGenerator().generate_podcast("aula-001")

    def test_cleans_up_already_synthesized_chunks_when_a_later_chunk_fails(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        # Regression: chunk_paths is mutated in place by _synthesize_chunks
        # so a mid-loop failure still leaves every earlier, successfully
        # written chunk visible to cleanup -- a plain return-value
        # assignment would lose that list entirely once the call raises.
        scripts_dir, audio_dir = _patch_dirs
        _write_script(
            scripts_dir,
            _make_script(
                dialogue=[
                    _line(speaker="Host", text="Primeira linha."),
                    _line(speaker="Expert", text="Segunda linha."),
                ]
            ),
        )
        calls_seen = 0

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            nonlocal calls_seen
            calls_seen += 1
            if calls_seen == 2:
                # The TTS engine crashing after already writing a partial
                # chunk_002.wav (engine timeout, disk full mid-write) --
                # the file exists on disk even though the subprocess
                # itself reports failure.
                _generated_chunk_path(args[0]).write_bytes(b"partial-wav")
                raise subprocess.CalledProcessError(1, args[0])
            return _fake_run_writing_outputs(*args, **kwargs)

        monkeypatch.setattr(generator_module, "run_bounded_process", fake_run)

        with pytest.raises(AudioGenerationError):
            PodcastAudioGenerator().generate_podcast("aula-001")

        lecture_dir = audio_dir / "aula-001"
        assert not (lecture_dir / "chunk_001.wav").exists()
        assert not (lecture_dir / "chunk_002.wav").exists()

    def test_preserves_previous_final_audio_until_atomic_replacement(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, audio_dir = _patch_dirs
        _write_script(scripts_dir, _make_script())
        lecture_dir = audio_dir / "aula-001"
        lecture_dir.mkdir(parents=True)
        final_path = lecture_dir / "podcast_final.m4a"
        final_path.write_bytes(b"stale-content")
        final_content_when_ffmpeg_ran: bytes | None = None

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            nonlocal final_content_when_ffmpeg_ran
            if Path(args[0][0]).name == "ffmpeg":
                final_content_when_ffmpeg_ran = final_path.read_bytes()
            return _fake_run_writing_outputs(*args, **kwargs)

        monkeypatch.setattr(generator_module, "run_bounded_process", fake_run)

        PodcastAudioGenerator().generate_podcast("aula-001")

        assert final_content_when_ffmpeg_ran == b"stale-content"
        assert final_path.read_bytes() == b"fake-m4a"
        assert not (lecture_dir / ".podcast_final.partial.m4a").exists()

    def test_cleans_up_chunks_and_concat_list_when_ffmpeg_fails(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, audio_dir = _patch_dirs
        _write_script(scripts_dir, _make_script())
        _patch_run_failing_on(
            monkeypatch,
            "ffmpeg",
            subprocess.CalledProcessError(1, ["ffmpeg"]),
        )

        with pytest.raises(AudioGenerationError):
            PodcastAudioGenerator().generate_podcast("aula-001")

        lecture_dir = audio_dir / "aula-001"
        assert not any(lecture_dir.glob("*.wav"))
        assert not (lecture_dir / "concat_list.txt").exists()
        assert not (lecture_dir / "podcast_final.m4a").exists()


class TestGeneratePodcastPersonalStudyBypass:
    """ADR 0024: `generate_podcast` skips the derivative readiness call
    when and only when the loaded script carries the uncertified marker,
    and still performs it -- and still fails closed -- for every other
    value. This class overrides the module's `legacy_certification_bypass`
    no-op with a call-observing/raising double per test, since that
    fixture's blanket no-op would otherwise hide exactly the branch being
    asserted here."""

    def test_skips_derivative_readiness_call_for_uncertified_marker(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, audio_dir = _patch_dirs

        def reject_call(_script: PodcastScript) -> None:
            raise AssertionError(
                "require_podcast_script_ready must not be called for an "
                "uncertified personal-study script"
            )

        monkeypatch.setattr(
            generator_module, "require_podcast_script_ready", reject_call
        )
        script = _make_script().model_copy(
            update={"execution_mode": ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY}
        )
        _write_script(scripts_dir, script)
        _patch_run(monkeypatch, _fake_run_writing_outputs)

        result = PodcastAudioGenerator().generate_podcast("aula-001")

        assert Path(result).exists()

    def test_still_reaches_derivative_readiness_call_and_fails_closed_without_marker(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        scripts_dir, _ = _patch_dirs

        def reject_call(_script: PodcastScript) -> None:
            raise DerivativeQualityError("podcast certificate rejected")

        monkeypatch.setattr(
            generator_module, "require_podcast_script_ready", reject_call
        )
        _write_script(scripts_dir, _make_script())

        with pytest.raises(DerivativeQualityError, match="certificate rejected"):
            PodcastAudioGenerator().generate_podcast("aula-001")

    def test_cleans_up_already_synthesized_chunks_on_a_bypassed_uncertified_run(
        self, monkeypatch: pytest.MonkeyPatch, _patch_dirs
    ):
        # Proves the partial-chunk cleanup discipline is unaffected by the
        # bypass branch -- a mid-loop failure on an uncertified script still
        # leaves no stray per-line audio file behind.
        scripts_dir, audio_dir = _patch_dirs
        script = _make_script(
            dialogue=[
                _line(speaker="Host", text="Primeira linha."),
                _line(speaker="Expert", text="Segunda linha."),
            ]
        ).model_copy(
            update={"execution_mode": ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY}
        )
        _write_script(scripts_dir, script)

        def reject_call(_script: PodcastScript) -> None:
            raise AssertionError("must not be called for the uncertified marker")

        monkeypatch.setattr(
            generator_module, "require_podcast_script_ready", reject_call
        )
        calls_seen = 0

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            nonlocal calls_seen
            calls_seen += 1
            if calls_seen == 2:
                _generated_chunk_path(args[0]).write_bytes(b"partial-wav")
                raise subprocess.CalledProcessError(1, args[0])
            return _fake_run_writing_outputs(*args, **kwargs)

        monkeypatch.setattr(generator_module, "run_bounded_process", fake_run)

        with pytest.raises(AudioGenerationError):
            PodcastAudioGenerator().generate_podcast("aula-001")

        lecture_dir = audio_dir / "aula-001"
        assert not (lecture_dir / "chunk_001.wav").exists()
        assert not (lecture_dir / "chunk_002.wav").exists()
