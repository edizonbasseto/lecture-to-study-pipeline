"""Word error rate by a stdlib word-level edit distance (ADR 0047, D-06).

The rate is (substitutions + deletions + insertions) divided by the number
of reference words. Both texts are reduced to NFC-normalized, casefolded
word tokens first, so punctuation and capitalization never count as errors.
The backtrace order is fixed, which makes the three counts deterministic.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["WordErrorCounts", "normalized_words", "word_error_counts"]

_WORD_PATTERN = re.compile(r"\w+(?:[-']\w+)*")


class WordErrorCounts(BaseModel):
    """Edit operations that turn the hypothesis into the reference."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    reference_words: int = Field(ge=1)
    substitutions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    insertions: int = Field(ge=0)

    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    def rate(self) -> float:
        return self.errors() / self.reference_words


def normalized_words(text: str) -> tuple[str, ...]:
    """Casefolded NFC word tokens; hyphenated and apostrophed words stay whole."""
    folded = unicodedata.normalize("NFC", text).casefold()
    return tuple(_WORD_PATTERN.findall(folded))


def word_error_counts(
    reference: Sequence[str], hypothesis: Sequence[str]
) -> WordErrorCounts:
    """Count substitutions, deletions and insertions between two token sequences."""
    if not reference:
        raise ValueError("the reference must contain at least one word")
    distances = _edit_distance_table(reference, hypothesis)
    substitutions, deletions, insertions = _backtrace(distances, reference, hypothesis)
    return WordErrorCounts(
        reference_words=len(reference),
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
    )


def _edit_distance_table(
    reference: Sequence[str], hypothesis: Sequence[str]
) -> list[list[int]]:
    table = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for row in range(1, len(reference) + 1):
        table[row][0] = row
    for column in range(1, len(hypothesis) + 1):
        table[0][column] = column
    for row in range(1, len(reference) + 1):
        for column in range(1, len(hypothesis) + 1):
            mismatch = reference[row - 1] != hypothesis[column - 1]
            table[row][column] = min(
                table[row - 1][column - 1] + mismatch,
                table[row - 1][column] + 1,
                table[row][column - 1] + 1,
            )
    return table


def _backtrace(
    table: list[list[int]], reference: Sequence[str], hypothesis: Sequence[str]
) -> tuple[int, int, int]:
    """Walk back preferring the diagonal, then a deletion, then an insertion."""
    substitutions = deletions = insertions = 0
    row, column = len(reference), len(hypothesis)
    while row > 0 or column > 0:
        if row > 0 and column > 0:
            mismatch = reference[row - 1] != hypothesis[column - 1]
            if table[row][column] == table[row - 1][column - 1] + mismatch:
                substitutions += mismatch
                row, column = row - 1, column - 1
                continue
        if row > 0 and table[row][column] == table[row - 1][column] + 1:
            deletions += 1
            row -= 1
            continue
        insertions += 1
        column -= 1
    return substitutions, deletions, insertions
