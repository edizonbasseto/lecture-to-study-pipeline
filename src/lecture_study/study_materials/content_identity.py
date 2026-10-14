"""Build semantic text identities without discarding clinical notation."""

from __future__ import annotations

import re
from typing import Final
from unicodedata import category, normalize

_IDENTIFIER_HYPHENS: Final = frozenset("-‐‑‒–—")
_IDENTIFIER_NUMERIC_SUFFIXES: Final = frozenset("+-−⁺⁻")
_NUMERIC_RANGE_DASH_TRANSLATION: Final = str.maketrans(
    {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"}
)
_CONTENT_IDENTITY_TRANSLATION: Final = str.maketrans(
    {
        "﹣": "-",
        "－": "-",
        "−": "-",
        "⁻": "-",
        "⁺": "+",
        "℃": "°C",
        "℉": "°F",
        "¼": " 1/4 ",
        "½": " 1/2 ",
        "¾": " 3/4 ",
        "⅐": " 1/7 ",
        "⅑": " 1/9 ",
        "⅒": " 1/10 ",
        "⅓": " 1/3 ",
        "⅔": " 2/3 ",
        "⅕": " 1/5 ",
        "⅖": " 2/5 ",
        "⅗": " 3/5 ",
        "⅘": " 4/5 ",
        "⅙": " 1/6 ",
        "⅚": " 5/6 ",
        "⅛": " 1/8 ",
        "⅜": " 3/8 ",
        "⅝": " 5/8 ",
        "⅞": " 7/8 ",
    }
)
_CURRENCY_EXPRESSION: Final = r"(?:(?<!\w)r\s*\$|[$€£¥])"
_COMPARATOR_EXPRESSION: Final = r"(?:<=|>=|!=|==|[=<>≤≥≠~≈])"
_NUMERIC_EXPRESSION = re.compile(
    rf"(?:"
    rf"(?:(?:"
    rf"{_CURRENCY_EXPRESSION}\s*(?:{_COMPARATOR_EXPRESSION}\s*)?"
    rf"|{_COMPARATOR_EXPRESSION}\s*(?:{_CURRENCY_EXPRESSION}\s*)?"
    rf")(?:[+\-±]\s*)?)"
    rf"|(?<!\w)(?:[+\-±]\s*)"
    rf"|(?<!\w)"
    rf")"
    rf"\d+(?:(?:[.,;]\d+)|(?:\s*[/:\-‐‑‒–—^+*×÷±]\s*\d+))*"
    rf"(?:\s*(?:[%‰‱°]|[+\-](?!\d)))?",
    flags=re.IGNORECASE,
)


def content_identity(value: str) -> str:
    """Normalize typographic variants while preserving clinical operators."""

    folded_value = (
        normalize("NFC", value).translate(_CONTENT_IDENTITY_TRANSLATION).casefold()
    )
    tokens: list[str] = []
    cursor = 0
    for numeric_match in _NUMERIC_EXPRESSION.finditer(folded_value):
        segment = folded_value[cursor : numeric_match.start()]
        tokens.extend(_word_identity_tokens(segment))
        numeric_token = "".join(numeric_match.group().split()).translate(
            _NUMERIC_RANGE_DASH_TRANSLATION
        )
        if _belongs_to_hyphenated_identifier(
            folded_value,
            numeric_match.start(),
            numeric_token,
        ):
            tokens[-1] = f"{tokens[-1]}-{numeric_token}"
        else:
            tokens.append(numeric_token)
        cursor = numeric_match.end()
    tokens.extend(_word_identity_tokens(folded_value[cursor:]))
    return " ".join(tokens)


def _belongs_to_hyphenated_identifier(
    value: str,
    numeric_start: int,
    numeric_token: str,
) -> bool:
    return (
        bool(numeric_token)
        and numeric_token[0].isdigit()
        and numeric_start >= 2
        and value[numeric_start - 1] in _IDENTIFIER_HYPHENS
        and value[numeric_start - 2].isalnum()
    )


def _word_identity_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    current_token: list[str] = []
    current_token_has_digit = False
    for index, character in enumerate(value):
        if character.isalnum() or category(character).startswith("M"):
            current_token.append(character)
            current_token_has_digit = current_token_has_digit or character.isdigit()
            continue
        if (
            character in _IDENTIFIER_HYPHENS
            and current_token
            and index < len(value) - 1
            and value[index + 1].isalnum()
        ):
            current_token.append("-")
            continue
        if (
            character in _IDENTIFIER_NUMERIC_SUFFIXES
            and current_token
            and current_token_has_digit
        ):
            current_token.append(character)
            continue
        if category(character).startswith("S"):
            if current_token:
                tokens.append("".join(current_token))
                current_token = []
                current_token_has_digit = False
            tokens.append(character)
            continue
        if current_token:
            tokens.append("".join(current_token))
            current_token = []
            current_token_has_digit = False
    if current_token:
        tokens.append("".join(current_token))
    return tuple(tokens)
