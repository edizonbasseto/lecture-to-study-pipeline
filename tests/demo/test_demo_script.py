"""Demo lecture source: the typed model, its strict loader and the tracer path
from a YAML slide to a Kokoro wav through the shared helper."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lecture_study.demo.script import (
    DemoScript,
    DemoScriptError,
    DemoSlide,
    load_demo_script,
)
from lecture_study.tts import generator as generator_module
from lecture_study.tts.generator import synthesize_kokoro_wav

ONE_SLIDE_YAML = """\
title: Aula de teste
narrator_voice: pm_alex
planted_terms:
  - histopatológico
slides:
  - heading: Objetivos da aula
    bullets:
      - Primeiro ponto
    background: "#102030"
    foreground: "#F0F0F0"
    narration:
      - Esta é a primeira frase.
      - Esta é a segunda frase.
"""


def _write_yaml(tmp_path: Path, text: str = ONE_SLIDE_YAML) -> Path:
    path = tmp_path / "script.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _slide(
    background: str = "#102030",
    narration: tuple[str, ...] = ("Uma frase.",),
) -> DemoSlide:
    return DemoSlide(
        heading="Titulo",
        bullets=("Ponto",),
        background=background,
        foreground="#FFFFFF",
        narration=narration,
    )


class TestLoadDemoScript:
    def test_loads_a_one_slide_file(self, tmp_path: Path):
        script = load_demo_script(_write_yaml(tmp_path))

        assert script.narrator_voice == "pm_alex"
        assert [slide.heading for slide in script.slides] == ["Objetivos da aula"]
        assert script.slides[0].bullets == ("Primeiro ponto",)

    @pytest.mark.parametrize(
        "replacement",
        [
            ("title: Aula de teste", "title: Aula de teste\nunknown_key: 1"),
            ('background: "#102030"', 'background: "azul"'),
            ("narrator_voice: pm_alex", "narrator_voice: pm_santa"),
            (
                "narrator_voice: pm_alex",
                "narrator_voice: &voice pm_alex\nextra_title: *voice",
            ),
        ],
        ids=["unknown-key", "bad-colour", "unknown-voice", "yaml-alias"],
    )
    def test_rejects_invalid_source(self, tmp_path: Path, replacement: tuple[str, str]):
        path = _write_yaml(tmp_path, ONE_SLIDE_YAML.replace(*replacement))

        with pytest.raises(DemoScriptError, match="script.yaml"):
            load_demo_script(path)

    def test_rejects_duplicate_backgrounds(self, tmp_path: Path):
        second_slide = ONE_SLIDE_YAML.split("slides:\n")[1]
        text = ONE_SLIDE_YAML + second_slide

        with pytest.raises(DemoScriptError, match="slides"):
            load_demo_script(_write_yaml(tmp_path, text))

    def test_error_names_field_locations_without_echoing_content(self, tmp_path: Path):
        text = ONE_SLIDE_YAML.replace('"#F0F0F0"', "segredo-no-campo")

        with pytest.raises(DemoScriptError) as raised:
            load_demo_script(_write_yaml(tmp_path, text))

        assert "slides.0.foreground" in str(raised.value)
        assert "segredo-no-campo" not in str(raised.value)

    def test_missing_file_raises_demo_script_error(self, tmp_path: Path):
        with pytest.raises(DemoScriptError, match="unreadable"):
            load_demo_script(tmp_path / "absent.yaml")


class TestTextViews:
    def test_narration_text_joins_lines_with_newlines(self):
        slide = _slide(narration=("Primeira.", "Segunda."))

        assert slide.narration_text() == "Primeira.\nSegunda."

    def test_reference_text_joins_every_line_in_order_with_spaces(self):
        script = DemoScript(
            title="T",
            narrator_voice="pf_dora",
            planted_terms=(),
            slides=(
                _slide("#111111", ("Um.", "Dois.")),
                _slide("#222222", ("Tres.",)),
            ),
        )

        assert script.reference_text() == "Um. Dois. Tres."


def test_tracer_slide_from_yaml_reaches_kokoro_argv_through_shared_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured: list[list[str]] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = list(args[0])
        captured.append(command)
        output_path = Path(command[command.index("--output_path") + 1])
        file_prefix = command[command.index("--file_prefix") + 1]
        (output_path / f"{file_prefix}.wav").write_bytes(b"fake-wav")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(generator_module, "run_bounded_process", fake_run)
    script = load_demo_script(_write_yaml(tmp_path))
    slide = script.slides[0]
    wav_path = tmp_path / "narration-01.wav"

    synthesize_kokoro_wav(
        slide.narration_text(),
        voice=script.narrator_voice,
        wav_path=wav_path,
        failure_subject="demo slide 1",
    )

    command = captured[0]
    assert wav_path.exists()
    assert command[:3] == [sys.executable, "-m", "mlx_audio.tts.generate"]
    assert command[command.index("--voice") + 1] == "pm_alex"
    assert command[command.index("--text") + 1] == slide.narration_text()
    assert "--join_audio" in command
