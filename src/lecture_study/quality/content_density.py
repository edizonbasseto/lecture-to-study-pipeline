"""Proportionality gate for generated long-form learning material."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from lecture_study.quality.evidence_auditor import (
    ContentQualityError,
    QualityFinding,
)

_WORD_PATTERN = re.compile(r"\b[\wÀ-ÖØ-öø-ÿ]+\b", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ContentDensityPolicy:
    """Reference-corpus-calibrated minimum material density."""

    minimum_words_per_source_minute: float
    minimum_source_to_material_word_ratio: float

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.minimum_words_per_source_minute)
            or self.minimum_words_per_source_minute <= 0
        ):
            raise ValueError(
                "minimum_words_per_source_minute must be finite and positive"
            )
        if not (
            0 < self.minimum_source_to_material_word_ratio <= 1
            and math.isfinite(self.minimum_source_to_material_word_ratio)
        ):
            raise ValueError(
                "minimum_source_to_material_word_ratio must be within (0, 1]"
            )


@dataclass(frozen=True, slots=True)
class ContentDensityReport:
    """Observed and required material size for one lecture."""

    source_word_count: int
    source_duration_seconds: float
    material_word_count: int
    minimum_material_word_count: int
    findings: tuple[QualityFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def require_passed(self) -> None:
        if self.passed:
            return
        raise ContentQualityError(
            "Generated content failed the calibrated proportionality gate."
        )


class ContentDensityAuditor:
    """Rejects material too small for its source duration and information volume."""

    def audit(
        self,
        *,
        source_text: str,
        source_duration_seconds: float,
        material_text: str,
        policy: ContentDensityPolicy,
    ) -> ContentDensityReport:
        if not math.isfinite(source_duration_seconds) or source_duration_seconds <= 0:
            raise ValueError("source_duration_seconds must be finite and positive")

        source_word_count = self._count_words(source_text)
        material_word_count = self._count_words(material_text)
        if source_word_count == 0:
            raise ValueError("source_text must contain words")

        duration_minimum = math.ceil(
            source_duration_seconds / 60 * policy.minimum_words_per_source_minute
        )
        ratio_minimum = math.ceil(
            source_word_count * policy.minimum_source_to_material_word_ratio
        )
        minimum_material_word_count = max(duration_minimum, ratio_minimum)

        findings: list[QualityFinding] = []
        if material_word_count < minimum_material_word_count:
            findings.append(
                QualityFinding(
                    code="insufficient_material_density",
                    message=(
                        f"Generated material contains {material_word_count} words; the "
                        f"calibrated minimum is {minimum_material_word_count}."
                    ),
                )
            )

        return ContentDensityReport(
            source_word_count=source_word_count,
            source_duration_seconds=source_duration_seconds,
            material_word_count=material_word_count,
            minimum_material_word_count=minimum_material_word_count,
            findings=tuple(findings),
        )

    @staticmethod
    def _count_words(text: str) -> int:
        return len(_WORD_PATTERN.findall(text))
