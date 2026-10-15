"""Demo lecture source: the typed model, its strict loader and the tracer path
from a YAML slide to a Kokoro wav through the shared helper."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import re

from lecture_study.demo.script import (
    DemoScript,
    DemoScriptError,
    DemoSlide,
    load_demo_script,
    narration_violations,
    require_clean_narration,
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


LONG_LINE = ("palavra " * 26).strip() + "."


def _script_with_line(line: str) -> DemoScript:
    return DemoScript(
        title="T",
        narrator_voice="pm_alex",
        planted_terms=(),
        slides=(_slide(narration=(line,)),),
    )


class TestNarrationLint:
    @pytest.mark.parametrize(
        ("line", "rule"),
        [
            ("O paciente tem oito anos e pesa 8 quilos.", "digit"),
            ("A equipe de CCV conduz o caso.", "abbreviation"),
            ("O volume foi medido em ml no frasco.", "unit"),
            ("A margem foi marcada a.C da pele.", "inner-period"),
            ("O pós-operatório exige repouso.", "hyphenated-compound"),
            ("A dose depende do caso clínico.", "dosage-word"),
            ("A dosagem depende do caso clínico.", "dosage-word"),
            ("Primeira frase. Segunda frase.", "multiple-sentences"),
            ("Frase sem pontuação final", "missing-terminal-punctuation"),
            (LONG_LINE, "line-too-long"),
        ],
        ids=[
            "digit",
            "abbreviation",
            "unit",
            "inner-period",
            "hyphenated-compound",
            "dose",
            "dosagem",
            "multiple-sentences",
            "no-terminal-punctuation",
            "too-long",
        ],
    )
    def test_each_rule_yields_one_violation_naming_slide_line_and_rule(
        self, line: str, rule: str
    ):
        violations = narration_violations(_script_with_line(line))

        assert [(v.slide_number, v.line_number, v.rule) for v in violations] == [
            (1, 1, rule)
        ]

    def test_excerpt_is_shortened_to_forty_characters(self):
        violation = narration_violations(_script_with_line(LONG_LINE))[0]

        assert len(violation.excerpt) <= 40

    def test_clean_line_has_no_violation(self):
        assert narration_violations(_script_with_line("Uma frase limpa.")) == ()

    def test_question_mark_is_a_valid_terminal_punctuation(self):
        assert narration_violations(_script_with_line("Qual é a margem?")) == ()

    def test_require_clean_narration_returns_none_for_a_clean_script(self):
        assert require_clean_narration(_script_with_line("Uma frase limpa.")) is None

    def test_require_clean_narration_lists_every_violation(self):
        script = DemoScript(
            title="T",
            narrator_voice="pm_alex",
            planted_terms=(),
            slides=(
                _slide("#111111", ("Tem 8 anos.",)),
                _slide("#222222", ("A dose varia", "Pesa 5 kg.")),
            ),
        )

        with pytest.raises(DemoScriptError) as raised:
            require_clean_narration(script)

        message = str(raised.value)
        assert "slide 1 line 1: digit" in message
        assert "slide 2 line 1: dosage-word" in message
        assert "slide 2 line 1: missing-terminal-punctuation" in message
        assert "slide 2 line 2: unit" in message


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEMO_SCRIPT_PATH = REPOSITORY_ROOT / "demo" / "lecture" / "script.yaml"
EXPECTED_HEADINGS = [
    "Objetivos da aula",
    "Quando indicar a biópsia excisional",
    "Planejamento das margens",
    "Dissecção abaixo do tumor",
    "Exame histopatológico das margens",
    "Revisão dos pontos principais",
]
FASCIA_PHRASE = re.compile(r"\b(camada|plano|margem)\s+fascial\b", re.IGNORECASE)
DEPTH_ANCHOR = re.compile(
    r"\b(profundidade|abaixo|f[áa]scia|m[úu]sculo|musculatura|tumor)\b",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def real_script() -> DemoScript:
    return load_demo_script(DEMO_SCRIPT_PATH)


def _narration_lines(script: DemoScript) -> list[str]:
    return [line for slide in script.slides for line in slide.narration]


class TestVersionedDemoLecture:
    def test_has_six_slides_with_the_planned_headings(self, real_script: DemoScript):
        assert [slide.heading for slide in real_script.slides] == EXPECTED_HEADINGS

    def test_narration_is_clean(self, real_script: DemoScript):
        assert narration_violations(real_script) == ()
        assert require_clean_narration(real_script) is None

    def test_reference_text_is_about_four_minutes_of_speech(
        self, real_script: DemoScript
    ):
        assert 560 <= len(real_script.reference_text().split()) <= 700

    def test_plants_fifteen_terms_each_spoken_at_least_once(
        self, real_script: DemoScript
    ):
        reference = real_script.reference_text().casefold()

        assert len(real_script.planted_terms) == 15
        missing = [
            t for t in real_script.planted_terms if t.casefold() not in reference
        ]
        assert missing == []

    def test_plants_fascia_phrases_next_to_a_depth_anchor(
        self, real_script: DemoScript
    ):
        anchored = [
            line
            for line in _narration_lines(real_script)
            if FASCIA_PHRASE.search(line) and DEPTH_ANCHOR.search(line)
        ]

        assert len(anchored) >= 8

    def test_repeats_the_histopathology_term(self, real_script: DemoScript):
        reference = real_script.reference_text().casefold()

        assert reference.count("histopatológico") >= 4

    def test_has_exactly_one_negative_control_line(self, real_script: DemoScript):
        control_lines = [
            line for line in _narration_lines(real_script) if "nervo facial" in line
        ]

        assert len(control_lines) == 1
        assert not FASCIA_PHRASE.search(control_lines[0])
        assert not re.search(r"\b(camada|plano|margem)\s+facial", control_lines[0])

    def test_backgrounds_are_pairwise_distinct(self, real_script: DemoScript):
        backgrounds = [slide.background.casefold() for slide in real_script.slides]

        assert len(set(backgrounds)) == len(backgrounds)
