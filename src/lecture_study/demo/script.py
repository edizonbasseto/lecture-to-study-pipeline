"""Typed source of the synthetic demo lecture.

``demo/lecture/script.yaml`` is the single versioned source of the demo
lecture: the slide text, the Kokoro narration and the word error rate
reference are all read from it through the models below (ADR 0047).
The file is parsed with the repository's strict YAML loader, so aliases and
explicit tags are refused before validation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from lecture_study.ops.strict_yaml import (
    StrictYamlError,
    load_strict_yaml,
    masked_location,
)
from lecture_study.tts.generator import KokoroVoice

__all__ = [
    "DemoScript",
    "DemoScriptError",
    "DemoSlide",
    "NarrationViolation",
    "load_demo_script",
    "narration_violations",
    "require_clean_narration",
]

_HEX_COLOUR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
_NARRATION_LINE_SEPARATOR = "\n"
_REFERENCE_WORD_SEPARATOR = " "

_MAXIMUM_NARRATION_LINE_CHARACTERS = 200
_MAXIMUM_EXCERPT_CHARACTERS = 40
_TERMINAL_PUNCTUATION = (".", "!", "?")

HexColour = Annotated[str, StringConstraints(pattern=_HEX_COLOUR_PATTERN)]


class DemoScriptError(ValueError):
    """The demo lecture source is unreadable, malformed or inconsistent."""


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class DemoSlide(_FrozenStrictModel):
    """One slide: its text, its colours and the narration spoken over it."""

    heading: str = Field(min_length=1)
    bullets: tuple[str, ...] = Field(min_length=1)
    background: HexColour
    foreground: HexColour
    narration: tuple[str, ...] = Field(min_length=1)

    def narration_text(self) -> str:
        """The narration as one text, one sentence per line; Kokoro splits on
        newlines."""
        return _NARRATION_LINE_SEPARATOR.join(self.narration)


class DemoScript(_FrozenStrictModel):
    """The whole lecture: title, narrator, planted terms and ordered slides."""

    title: str = Field(min_length=1)
    narrator_voice: KokoroVoice
    planted_terms: tuple[str, ...]
    slides: tuple[DemoSlide, ...] = Field(min_length=1)

    @field_validator("slides")
    @classmethod
    def _require_distinct_backgrounds(
        cls, slides: tuple[DemoSlide, ...]
    ) -> tuple[DemoSlide, ...]:
        backgrounds = [slide.background.casefold() for slide in slides]
        if len(set(backgrounds)) != len(backgrounds):
            raise ValueError("slides must have pairwise distinct backgrounds")
        return slides

    def reference_text(self) -> str:
        """Every narration line of every slide, in order: the reference for
        the word error rate."""
        lines = (line for slide in self.slides for line in slide.narration)
        return _REFERENCE_WORD_SEPARATOR.join(lines)


def load_demo_script(path: Path) -> DemoScript:
    """Read and validate the demo lecture source at ``path``.

    Raises ``DemoScriptError`` naming the path and, for invalid content, only
    the field locations; no document text is echoed.
    """
    try:
        payload = load_strict_yaml(path.read_text(encoding="utf-8"))
        return DemoScript.model_validate(payload, strict=False)
    except StrictYamlError as error:
        raise DemoScriptError(f"{path}: {error}") from None
    except (OSError, UnicodeDecodeError) as error:
        raise DemoScriptError(f"{path}: unreadable ({type(error).__name__})") from None
    except ValidationError as error:
        raise DemoScriptError(f"{path}: {_invalid_locations(error)}") from None


def _invalid_locations(error: ValidationError) -> str:
    locations = sorted({masked_location(item["loc"]) for item in error.errors()})
    return "invalid fields: " + ", ".join(locations)


@dataclass(frozen=True, slots=True)
class NarrationViolation:
    """One narration line that breaks a rule written to keep the speech
    recognition reference predictable."""

    slide_number: int
    line_number: int
    rule: str
    excerpt: str


def narration_violations(script: DemoScript) -> tuple[NarrationViolation, ...]:
    """Every narration line that breaks a rule, in reading order.

    Whisper writes numerals and abbreviations unpredictably, and the study
    guide flags dosage words, so the narration avoids all of them.
    """
    return tuple(
        NarrationViolation(
            slide_number, line_number, rule, line[:_MAXIMUM_EXCERPT_CHARACTERS]
        )
        for slide_number, slide in enumerate(script.slides, start=1)
        for line_number, line in enumerate(slide.narration, start=1)
        for rule, is_broken_by in _NARRATION_RULES
        if is_broken_by(line)
    )


def require_clean_narration(script: DemoScript) -> None:
    """Raise ``DemoScriptError`` listing every narration violation."""
    violations = narration_violations(script)
    if violations:
        listing = "; ".join(
            f"slide {v.slide_number} line {v.line_number}: {v.rule} ({v.excerpt!r})"
            for v in violations
        )
        raise DemoScriptError(f"narration breaks its rules: {listing}")


def _matches(pattern: str) -> Callable[[str], bool]:
    compiled = re.compile(pattern, re.IGNORECASE)
    return lambda line: compiled.search(line) is not None


def _has_abbreviation(line: str) -> bool:
    words = re.findall(r"[^\W\d_]+", line)
    return any(len(word) >= 2 and word.isupper() for word in words)


def _lacks_terminal_punctuation(line: str) -> bool:
    return not line.endswith(_TERMINAL_PUNCTUATION)


def _exceeds_line_length(line: str) -> bool:
    return len(line) > _MAXIMUM_NARRATION_LINE_CHARACTERS


_NARRATION_RULES: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("digit", _matches(r"\d")),
    ("abbreviation", _has_abbreviation),
    ("unit", _matches(r"\b(mg|ml|kg|cm|mm)\b")),
    ("inner-period", _matches(r"\w\.\w")),
    ("hyphenated-compound", _matches(r"\w-\w")),
    ("dosage-word", _matches(r"\b(dose|doses|dosagem)\b")),
    ("multiple-sentences", _matches(r"[.!?]\s+\S")),
    ("missing-terminal-punctuation", _lacks_terminal_punctuation),
    ("line-too-long", _exceeds_line_length),
)
