"""Offline guard over student-facing generated text.

Two defects were observed in lecture 10002's first real mock exam: every
question addressed the student "according to the Evidence Pack" -- an
internal pipeline abstraction the student never hears of -- and eleven of
twelve questions were written in English instead of Brazilian Portuguese.
Both are mechanical and detectable without inference, so they are checked
here: the attempt that produced them is discarded and retried once through
the generator's existing bounded repair path, and a second defective
attempt fails the run rather than reaching a study bundle.

The checks are deliberately narrow. Internal names are matched from a
closed vocabulary of pipeline-only terms, and the language check counts
English function words that are not Portuguese words, so ordinary
clinical prose in pt-BR -- including English-origin surgical vocabulary --
is never flagged.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_INTERNAL_NAME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("evidence pack", re.compile(r"\bevidence\s+pack\b", re.IGNORECASE)),
    ("pacote de evidencias", re.compile(r"\bpacotes?\s+de\s+evid", re.IGNORECASE)),
    ("unit id", re.compile(r"\bunit[-_]\d+\b", re.IGNORECASE)),
    ("source unit field", re.compile(r"\bsource_unit_\w+\b", re.IGNORECASE)),
    ("learning unit", re.compile(r"\blearning\s+unit\b", re.IGNORECASE)),
    ("semantic ledger", re.compile(r"\bsemantic\s+ledger\b", re.IGNORECASE)),
    ("canonical document", re.compile(r"\bcanonical\s+document\b", re.IGNORECASE)),
    ("pack sequence", re.compile(r"\bpack[-_\s]sequence\b", re.IGNORECASE)),
)

# Referring constructions observed in the first post-fix run of lecture
# 10002 ("Qual tecnica e preferida segundo o material?"). Only an explicit
# referring head is matched -- never a bare preposition -- so ordinary
# surgical prose such as "a contaminacao do material cirurgico" or "a
# drenagem do conteudo purulento" is untouched.
_SOURCE_REFERENCE_PATTERN = re.compile(
    r"\b(segundo|conforme|de acordo com|com base n[oa]|mencionad[oa]s?\s+n[oa]|"
    r"apresentad[oa]s?\s+n[oa]|descrit[oa]s?\s+n[oa]|citad[oa]s?\s+n[oa]|"
    r"list[oa]d[oa]s?\s+n[oa]|indicad[oa]s?\s+n[oa])\s+"
    r"(o\s+|a\s+)?(material|texto|conte[uú]do|documento|aula|transcri\w+|"
    r"slides?)\b",
    re.IGNORECASE,
)

# English function words with no Portuguese homograph. A Portuguese word
# such as "no", "a", "e", "os" or "sem" is deliberately absent, so a
# correct pt-BR sentence cannot accumulate markers by coincidence.
_ENGLISH_MARKER_WORDS = frozenset(
    {
        "according",
        "and",
        "answer",
        "because",
        "been",
        "being",
        "between",
        "both",
        "correct",
        "following",
        "from",
        "has",
        "have",
        "how",
        "however",
        "if",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "only",
        "other",
        "should",
        "shows",
        "since",
        "such",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "while",
        "why",
        "with",
        "within",
        "would",
        "you",
        "your",
    }
)

# Two distinct markers, not one: an isolated English token can be a
# quoted term or a proper name, while two different English function
# words in one passage indicate English syntax. A short passage -- an
# exam option or an episode title -- has too few words to accumulate two,
# so one marker is enough there.
_MINIMUM_DISTINCT_ENGLISH_MARKERS = 2
_SHORT_PASSAGE_WORD_COUNT = 8
_MINIMUM_DISTINCT_ENGLISH_MARKERS_IN_A_SHORT_PASSAGE = 1

# A hyphenated compound is one token: "pull-through" is a surgical
# technique in pt-BR prose, not the English function word "through".
_WORD_PATTERN = re.compile(r"[a-zA-ZÀ-ÿ]+(?:-[a-zA-ZÀ-ÿ]+)*")
_WHITESPACE_RUN = re.compile(r"\s+")
_MAXIMUM_EVIDENCE_CHARACTERS = 80


@dataclass(frozen=True, slots=True)
class StudentFacingTextDefect:
    """One passage of generated text that must not reach a student."""

    location: str
    defect: str
    evidence: str


def student_facing_text_defects(
    passages: Iterable[tuple[str, str]],
) -> list[StudentFacingTextDefect]:
    """Report every defect across ``(location, text)`` passages.

    ``location`` names the passage for a human reader (for example
    ``"question 3 explanation"``) and appears verbatim in the report.
    """
    defects: list[StudentFacingTextDefect] = []
    for location, text in passages:
        defects.extend(_defects_in_passage(location, text))
    return defects


def describe_student_facing_text_defects(
    defects: Sequence[StudentFacingTextDefect],
) -> str:
    """Render defects as one line each, for a generation failure message."""
    return "; ".join(
        f"{defect.location}: {defect.defect} ({defect.evidence})" for defect in defects
    )


def _defects_in_passage(location: str, text: str) -> list[StudentFacingTextDefect]:
    defects = [
        StudentFacingTextDefect(
            location=location,
            defect="refers to an internal pipeline name",
            evidence=internal_name,
        )
        for internal_name in _matched_internal_names(text)
    ]
    source_reference = _SOURCE_REFERENCE_PATTERN.search(text)
    if source_reference is not None:
        defects.append(
            StudentFacingTextDefect(
                location=location,
                defect="refers to the source material",
                evidence=_bounded_evidence(source_reference.group()),
            )
        )
    english_markers = _distinct_english_markers(text)
    if len(english_markers) >= _required_english_markers(text):
        defects.append(
            StudentFacingTextDefect(
                location=location,
                defect="is not written in Brazilian Portuguese",
                evidence=", ".join(sorted(english_markers)),
            )
        )
    return defects


def _required_english_markers(text: str) -> int:
    word_count = len(_WORD_PATTERN.findall(text))
    if word_count <= _SHORT_PASSAGE_WORD_COUNT:
        return _MINIMUM_DISTINCT_ENGLISH_MARKERS_IN_A_SHORT_PASSAGE
    return _MINIMUM_DISTINCT_ENGLISH_MARKERS


def _bounded_evidence(matched_text: str) -> str:
    """Collapse and cap the one evidence drawn from model output.

    Every other evidence in this module comes from a closed vocabulary.
    This one is a substring of generated text, and it reaches a failure
    message and the run log, so it is collapsed to a single line and
    capped -- the module never lets model-controlled bytes through at
    their own length, following llm_response_contract's own rule.
    """
    collapsed = _WHITESPACE_RUN.sub(" ", matched_text).strip()
    if len(collapsed) <= _MAXIMUM_EVIDENCE_CHARACTERS:
        return collapsed
    return collapsed[:_MAXIMUM_EVIDENCE_CHARACTERS] + "..."


def _matched_internal_names(text: str) -> list[str]:
    return [name for name, pattern in _INTERNAL_NAME_PATTERNS if pattern.search(text)]


def _distinct_english_markers(text: str) -> set[str]:
    words = {match.group().lower() for match in _WORD_PATTERN.finditer(text)}
    return words & _ENGLISH_MARKER_WORDS
