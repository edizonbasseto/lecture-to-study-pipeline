"""Fail when the text on stdin contains a term from a private denylist.

Used by the git hooks (staged content, commit messages, pushed history) and
by the CI history scan. The denylist lives outside the repository: committing
it would publish the very terms it protects. For the same reason a match is
reported by denylist line number only, never by the term itself.

Both the terms and the text are compared in two normalized spellings, so a
term still matches when it differs in case, accents, compatibility forms
(full-width letters), invisible characters, or separators -- including a line
break that splits a multi-word term.
"""

import re
import sys
import unicodedata
from pathlib import Path

MATCH_EXIT_CODE = 1
UNAVAILABLE_EXIT_CODE = 2

_NON_ALPHANUMERIC = re.compile(r"[^0-9a-z]+")


class DenylistUnavailableError(RuntimeError):
    """Raised when the denylist is missing, unreadable, or has no terms."""


def load_terms(denylist_path: Path) -> list[str]:
    """Read one term per line, skipping blank lines and # comments."""
    try:
        lines = denylist_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise DenylistUnavailableError(
            f"denylist is not readable at {denylist_path}"
        ) from error
    terms = [line.strip() for line in lines]
    terms = [term for term in terms if term and not term.startswith("#")]
    if not terms:
        raise DenylistUnavailableError(f"denylist at {denylist_path} has no terms")
    return terms


def matching_entry_numbers(text: str, terms: list[str]) -> list[int]:
    """The 1-based positions of the terms that occur in the text."""
    folded_text = _fold(text)
    squashed_text = _squash(folded_text)
    return [
        position
        for position, term in enumerate(terms, start=1)
        if _occurs(_fold(term), folded_text, squashed_text)
    ]


def main(argv: list[str]) -> int:
    try:
        terms = load_terms(Path(argv[0]))
    except (DenylistUnavailableError, IndexError) as error:
        print(f"denylist scan refused: {error}", file=sys.stderr)
        return UNAVAILABLE_EXIT_CODE
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    matches = matching_entry_numbers(text, terms)
    if matches:
        entries = ", ".join(str(number) for number in matches)
        print(f"denylisted term found (denylist line {entries})", file=sys.stderr)
        return MATCH_EXIT_CODE
    return 0


def _occurs(folded_term: str, folded_text: str, squashed_text: str) -> bool:
    squashed_term = _squash(folded_term)
    return folded_term in folded_text or (
        bool(squashed_term) and squashed_term in squashed_text
    )


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text).casefold()
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )


def _squash(folded_text: str) -> str:
    return _NON_ALPHANUMERIC.sub("", folded_text)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
