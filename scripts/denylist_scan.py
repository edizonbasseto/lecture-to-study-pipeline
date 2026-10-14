"""Fail when the text on stdin contains a term from a private denylist.

Used by the git hooks (staged content, commit messages, pushed history) and
by the CI history scan. The denylist lives outside the repository: committing
it would publish the very terms it protects. For the same reason a match is
reported by the term's position among the denylist terms (comments and
blank lines are not counted), never by the term itself.

Terms and text are folded before comparison -- case, accents, compatibility
forms (full-width letters), and invisible format characters are ignored --
and the text is compared both as written and percent-decoded/HTML-unescaped.
A single-word term matches as a substring. A multi-word term also matches
when its words are joined by a short run of separators, optionally continued
on the next line after any indentation or diff marker, so it survives
hyphenation, URL encoding, and line wrapping; words of unrelated text are
never fused into a single-word term.

Knowingly not caught: separators inside a single word ("seg redo"),
double-encoded text, look-alike letters from other scripts, and encodings
such as base64. The gate targets accidental leaks, not a deliberate adversary.
"""

import html
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote

MATCH_EXIT_CODE = 1
UNAVAILABLE_EXIT_CODE = 2

_WORD = re.compile(r"[0-9a-z]+")
_SEPARATORS_BETWEEN_WORDS = r"[^0-9a-z\n]{0,8}(?:\n[^0-9a-z\n]{0,40})?"


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
    spellings = (_fold(text), _fold(html.unescape(unquote(text))))
    return [
        position
        for position, term in enumerate(terms, start=1)
        if any(_term_pattern(term).search(spelling) for spelling in spellings)
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
        print(f"denylisted term found (denylist term {entries})", file=sys.stderr)
        return MATCH_EXIT_CODE
    return 0


def _term_pattern(term: str) -> re.Pattern[str]:
    words = _WORD.findall(_fold(term))
    if not words:
        return re.compile(re.escape(_fold(term)))
    return re.compile(_SEPARATORS_BETWEEN_WORDS.join(map(re.escape, words)))


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text).casefold()
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
        and unicodedata.category(character) != "Cf"
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
