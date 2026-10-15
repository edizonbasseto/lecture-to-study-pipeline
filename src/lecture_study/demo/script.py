"""Typed source of the synthetic demo lecture.

``demo/lecture/script.yaml`` is the single versioned source of the demo
lecture: the slide text, the Kokoro narration and the word error rate
reference are all read from it through the models below (ADR 0047).
The file is parsed with the repository's strict YAML loader, so aliases and
explicit tags are refused before validation.
"""

from __future__ import annotations

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
    "load_demo_script",
]

_HEX_COLOUR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
_NARRATION_LINE_SEPARATOR = "\n"
_REFERENCE_WORD_SEPARATOR = " "

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
