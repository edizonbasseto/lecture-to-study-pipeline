"""Semantic ledger groundedness diagnostic (Phase 5 audit).

Answers one narrow question, mechanically, over already-loaded
`SemanticLedger`/`EvidencePack`/`LectureTranscriptionResult` models: does a
referent a ledger fact names actually appear in the source it was extracted
from? It follows `exams/echo_scan.py`'s house shape for a pure diagnostic:
no filesystem access, no subprocess, no inference, and a module docstring
stating plainly what the checker does and does not establish. Do not import
this module from any production pipeline module -- wiring a diagnostic into
a pipeline entry point turns a reportable finding into a silent gate, which
is exactly the accommodation this phase's audit exists to avoid.

**What this module can show.** That a referent a fact names is, or is not,
mechanically traceable to the lecture's own transcript and slide OCR text.

**What this module cannot show.** That a traceable referent was correctly
understood, or that a fact the checker does not flag is therefore true. It
also cannot see Shape A of the plan's taxonomy at all -- a rhetorical
question the lecturer poses and then corrects, where every word is present
in the source and only the word's status as a rejected hypothetical is not.
A term-presence check has no way to represent "present, but rejected".

Three tiers, reported separately and never combined into one score, because
they carry different precision:

- **Term absence** (`find_term_absence_candidates`). A fact's `entity_name`
  is tokenized and every token is checked, independently, for at least one
  occurrence anywhere in the lecture's transcript segments or frame OCR
  text. A fact is a candidate only when at least one of its tokens occurs
  nowhere at all. This is deliberately a union-of-individual-words check,
  not a contiguous-phrase check: calibration against two adjudicated
  ledger facts showed that a strict phrase-contiguity rule would
  incorrectly flag "caneta reutilizável" -- its two words are real, but
  they are spoken many tokens apart, in different sentences -- while
  correctly catching "nylon três zeros", none of the pairwise adjacencies
  a contiguous-phrase rule would need actually hold in the transcript. A
  fact built entirely of individually real words that were never uttered
  together is exactly the shape this tier cannot catch by design; the
  numeric-collocation tier below exists because of that gap, not in spite
  of it.
- **Numeric collocation** (`find_numeric_collocation_candidates`). For each
  numeral token in `entity_name` (a digit literal or a Portuguese number
  word, per `is_numeral_token`), the token immediately adjacent to it
  within `entity_name` is treated as the numeral's paired content word, and
  the pair is searched for co-occurrence within a small bounded gap
  (`_MAX_COLLOCATION_TOKEN_GAP`) anywhere in the lecture's transcript. This
  catches "nylon três zeros": "três" occurs in the transcript (in a
  sentence unrelated to gauge size), but never within the bounded gap of
  "zeros" -- the nearest occurrence is 4 token positions away, outside the
  bound -- while "dois zeros" is never flagged, because "dois" and "zeros"
  are literally adjacent every time the lecturer says them. A bare-token
  check on "três" alone would miss this fact entirely, since the token
  does occur elsewhere in the lecture; the bound is what makes the
  difference. `um`/`uma` are Portuguese indefinite articles, not counted
  as numerals. `zero`/`zeros` are treated as content words, not numerals,
  in this domain: they denote a suture-gauge unit ("dois zeros" = a 2-0
  gauge), not a count, and excluding them from the numeral set is what
  lets a numeral like "três" pair with "zeros" as its adjacent content
  word instead of the two being compared to each other as two numerals.
- **Timestamp traceability** (`find_timestamp_traceability_candidates`). A
  fact whose `timestamp_ref` falls outside the half-open window
  `[pack.start_time, pack.end_time)` of its own evidence pack is reported.
  This is a traceability defect, not a groundedness one, and is never
  merged into the two tiers above.

**The known blind spot, locked as a test.** An affirmed-hypothetical
fact -- `entity_name` "margem", `clinical_context` "1
centímetro de margem se tiver eletroquimioterapia" -- is not reported by
either term-presence tier. "margem" is a single, ordinary word spoken
throughout the lecture; the fabricated content (a Socratic rhetorical
question the lecturer immediately corrects) lives entirely in
`clinical_context`, a field neither tier reads. Both tiers operate on
`entity_name` only, by design: this is exactly what makes the blind spot a
structural property of the design rather than an incidental gap, and is
why `test_ledger_groundedness.py` asserts the miss directly instead of only
describing it in prose.

**Normalization.** `normalize_text` folds case (`casefold`) and strips
Unicode accents (`unicodedata.normalize("NFKD", ...)` plus a combining-mark
filter) -- the same combining-mark-stripping technique
`exams/echo_scan.py._normalize` already uses, applied here without also
collapsing punctuation to a single vocabulary, since tokenization (not
whole-string collapsing) is this module's normalization boundary.
`tokenize` then extracts two kinds of token, preferring the numeric-literal
alternative at every position: a decimal or a range is kept as one literal
("86,5%", "24-48") rather than split at its internal comma or hyphen, and
every other punctuation character is discarded as a separator. This
differs from `metadata/aggregator.py._normalize_text`, which strips only
surrounding punctuation from a whole string for deduplication signatures;
this module needs token boundaries for position-based collocation search,
which a whole-string collapse cannot provide. Portuguese number words are
unified with their digit forms for numeral comparison only
(`canonical_numeral`): "dois" and a literal "2" are treated as the same
numeral when searching for a collocation, but a content word is always
compared by its literal normalized spelling.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from lecture_study.domain.models import (
    EvidencePack,
    LectureTranscriptionResult,
    SemanticFact,
    SemanticLedger,
)

# A collocation is grounded when a numeral occurrence and its paired
# content-word occurrence sit within this many token positions of each
# other (so a value of 2 accepts immediate adjacency, distance 1, and one
# intervening token, distance 2). Calibrated against the two adjudicated
# facts: "dois"/"zeros" sit at distance 1 every time they co-occur, and
# must be grounded; "três"/"zeros" sit at distance 4 at their nearest
# approach, and must not be. A tier's rule changes only by a stated, tested
# rule -- this bound is that rule, not a value tuned to a target count.
_MAX_COLLOCATION_TOKEN_GAP = 2

# Indefinite articles, not counts: "um sinal clínico comum" is not a
# numeral claim, so these are never treated as numerals.
_EXCLUDED_ARTICLE_WORDS = frozenset({"um", "uma"})

# "zero"/"zeros" denote a suture-gauge unit in this domain ("dois zeros" =
# a 2-0 gauge), not a count of something. Excluding them from the numeral
# set is what makes them available as the *content word* a real numeral
# (e.g. "dois", "três") pairs with, rather than being compared to that
# numeral as if the two were rival counts of the same thing.
_UNIT_WORDS_NOT_NUMERALS = frozenset({"zero", "zeros"})

_NUMBER_WORD_TO_DIGIT: dict[str, str] = {
    "dois": "2",
    "duas": "2",
    "tres": "3",
    "quatro": "4",
    "cinco": "5",
    "seis": "6",
    "sete": "7",
    "oito": "8",
    "nove": "9",
    "dez": "10",
    "onze": "11",
    "doze": "12",
    "treze": "13",
    "catorze": "14",
    "quatorze": "14",
    "quinze": "15",
    "dezesseis": "16",
    "dezessete": "17",
    "dezoito": "18",
    "dezenove": "19",
    "vinte": "20",
    "trinta": "30",
    "quarenta": "40",
    "cinquenta": "50",
    "sessenta": "60",
    "setenta": "70",
    "oitenta": "80",
    "noventa": "90",
    "cem": "100",
    "cento": "100",
    "mil": "1000",
}

# A numeric literal (digit run, optional decimal, optional hyphenated
# range, optional trailing percent sign) is tried before a plain word run
# at every position, so "86,5%" and "24-48" are each captured as one
# token instead of being split at their internal punctuation.
_TOKEN_PATTERN = re.compile(
    r"\d+(?:[.,]\d+)?(?:-\d+(?:[.,]\d+)?)?%?|[^\W\d_]+",
    re.UNICODE,
)


class GroundednessTier(StrEnum):
    """Which of the three independent checks reported a candidate."""

    TERM_ABSENCE = "term_absence"
    NUMERIC_COLLOCATION = "numeric_collocation"
    TIMESTAMP_TRACEABILITY = "timestamp_traceability"


@dataclass(frozen=True, slots=True)
class GroundednessCandidate:
    """One fact flagged by exactly one tier, with a checkable explanation.

    `detail` names what was searched and what was, or was not, found --
    never a verdict beyond that. Whether a candidate is a real defect is a
    reader's judgement against the taxonomy, not this dataclass's own.
    """

    tier: GroundednessTier
    lecture_id: str
    pack_sequence: int
    fact: SemanticFact
    detail: str


def normalize_text(text: str) -> str:
    """Fold case and strip Unicode accents, nothing else.

    Punctuation is left in place here; `tokenize` is this module's
    normalization boundary for punctuation and numeral-literal handling.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return without_accents.casefold()


def tokenize(text: str) -> tuple[str, ...]:
    """Split normalized text into word and numeric-literal tokens.

    A decimal or a hyphenated range is captured as one token; every other
    punctuation character is discarded as a separator.
    """
    normalized = normalize_text(text)
    return tuple(match.group(0) for match in _TOKEN_PATTERN.finditer(normalized))


def is_numeral_token(token: str) -> bool:
    """True for a digit literal or a Portuguese number word.

    `um`/`uma` (indefinite articles) and `zero`/`zeros` (a suture-gauge
    unit word in this domain, not a count) are excluded.
    """
    if token in _EXCLUDED_ARTICLE_WORDS or token in _UNIT_WORDS_NOT_NUMERALS:
        return False
    if token[:1].isdigit():
        return True
    return token in _NUMBER_WORD_TO_DIGIT


def canonical_numeral(token: str) -> str:
    """The digit-string form of a numeral token, for cross-spelling comparison.

    A digit literal is returned unchanged (including a decimal or a range,
    which is never split back apart here); a recognized number word maps
    to its digit form; anything else is returned unchanged as a fallback
    that only a caller who already confirmed `is_numeral_token` should
    reach.
    """
    if token[:1].isdigit():
        return token
    return _NUMBER_WORD_TO_DIGIT.get(token, token)


def find_term_absence_candidates(
    ledger: SemanticLedger,
    transcript: LectureTranscriptionResult,
) -> list[GroundednessCandidate]:
    """Facts whose entity_name contains a word absent from the whole lecture."""
    lecture_tokens = _lecture_token_set(transcript)
    candidates: list[GroundednessCandidate] = []
    for fact in ledger.facts:
        missing_tokens = tuple(
            token for token in tokenize(fact.entity_name) if token not in lecture_tokens
        )
        if not missing_tokens:
            continue
        candidates.append(
            GroundednessCandidate(
                tier=GroundednessTier.TERM_ABSENCE,
                lecture_id=ledger.lecture_id,
                pack_sequence=ledger.pack_sequence,
                fact=fact,
                detail=_term_absence_detail(missing_tokens, transcript),
            )
        )
    return candidates


def find_numeric_collocation_candidates(
    ledger: SemanticLedger,
    transcript: LectureTranscriptionResult,
) -> list[GroundednessCandidate]:
    """Facts whose entity_name pairs a numeral with a content word that
    never co-occurs with it, within the bounded gap, anywhere in the
    lecture's transcript."""
    word_positions, numeral_positions = _index_transcript_positions(transcript)
    candidates: list[GroundednessCandidate] = []
    for fact in ledger.facts:
        entity_tokens = tokenize(fact.entity_name)
        for index, token in enumerate(entity_tokens):
            if not is_numeral_token(token):
                continue
            content_word = _nearest_adjacent_token(entity_tokens, index)
            if content_word is None:
                continue
            grounded, nearest_gap = _collocation_grounded(
                token, content_word, word_positions, numeral_positions
            )
            if grounded:
                continue
            candidates.append(
                GroundednessCandidate(
                    tier=GroundednessTier.NUMERIC_COLLOCATION,
                    lecture_id=ledger.lecture_id,
                    pack_sequence=ledger.pack_sequence,
                    fact=fact,
                    detail=_numeric_collocation_detail(
                        token, content_word, nearest_gap
                    ),
                )
            )
    return candidates


def find_timestamp_traceability_candidates(
    ledger: SemanticLedger,
    pack: EvidencePack,
) -> list[GroundednessCandidate]:
    """Facts whose timestamp_ref falls outside their own pack's half-open window."""
    candidates: list[GroundednessCandidate] = []
    for fact in ledger.facts:
        if pack.start_time <= fact.timestamp_ref < pack.end_time:
            continue
        candidates.append(
            GroundednessCandidate(
                tier=GroundednessTier.TIMESTAMP_TRACEABILITY,
                lecture_id=ledger.lecture_id,
                pack_sequence=ledger.pack_sequence,
                fact=fact,
                detail=(
                    f"timestamp_ref {fact.timestamp_ref!r} falls outside its own "
                    f"pack's half-open window [{pack.start_time!r}, {pack.end_time!r})"
                ),
            )
        )
    return candidates


def _lecture_token_set(transcript: LectureTranscriptionResult) -> frozenset[str]:
    tokens: set[str] = set()
    for segment in transcript.segments:
        tokens.update(tokenize(segment.text))
    for frame in transcript.frames:
        tokens.update(tokenize(frame.ocr_text))
    return frozenset(tokens)


def _transcript_token_stream(transcript: LectureTranscriptionResult) -> tuple[str, ...]:
    tokens: list[str] = []
    for segment in transcript.segments:
        tokens.extend(tokenize(segment.text))
    return tuple(tokens)


def _index_transcript_positions(
    transcript: LectureTranscriptionResult,
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Position index over the transcript's token stream, in speaking order.

    `word_positions` indexes every token by its literal spelling, for
    content-word lookups. `numeral_positions` indexes only numeral tokens,
    by `canonical_numeral`, so "dois" and a literal "2" share one bucket.
    """
    word_positions: dict[str, list[int]] = {}
    numeral_positions: dict[str, list[int]] = {}
    for position, token in enumerate(_transcript_token_stream(transcript)):
        word_positions.setdefault(token, []).append(position)
        if is_numeral_token(token):
            numeral_positions.setdefault(canonical_numeral(token), []).append(position)
    return word_positions, numeral_positions


def _nearest_adjacent_token(tokens: tuple[str, ...], numeral_index: int) -> str | None:
    """The entity_name token immediately paired with a numeral at `numeral_index`.

    Prefers the next token (matching how this domain's gauge and dosage
    phrases order a numeral before its unit: "dois zeros", "3 centímetros");
    falls back to the previous token when the numeral is the last one.
    """
    if numeral_index + 1 < len(tokens):
        return tokens[numeral_index + 1]
    if numeral_index - 1 >= 0:
        return tokens[numeral_index - 1]
    return None


def _collocation_grounded(
    numeral_token: str,
    content_word: str,
    word_positions: dict[str, list[int]],
    numeral_positions: dict[str, list[int]],
) -> tuple[bool, int | None]:
    """Whether the pair co-occurs within the bounded gap, and the nearest
    gap observed (None when either side never occurs in the transcript at
    all)."""
    numeral_occurrences = numeral_positions.get(canonical_numeral(numeral_token), [])
    content_occurrences = word_positions.get(content_word, [])
    if not numeral_occurrences or not content_occurrences:
        return False, None
    nearest_gap = min(
        abs(numeral_position - content_position)
        for numeral_position in numeral_occurrences
        for content_position in content_occurrences
    )
    return nearest_gap <= _MAX_COLLOCATION_TOKEN_GAP, nearest_gap


def _term_absence_detail(
    missing_tokens: tuple[str, ...], transcript: LectureTranscriptionResult
) -> str:
    return (
        f"entity_name token(s) {missing_tokens!r} do not occur anywhere in the "
        f"{len(transcript.segments)} transcript segments or {len(transcript.frames)} "
        f"frame OCR texts searched for lecture {transcript.lecture_id!r}"
    )


def _numeric_collocation_detail(
    numeral_token: str, content_word: str, nearest_gap: int | None
) -> str:
    if nearest_gap is None:
        return (
            f"numeral {numeral_token!r} paired with {content_word!r} in entity_name: "
            f"{content_word!r} does not occur anywhere in the lecture's transcript"
        )
    return (
        f"numeral {numeral_token!r} paired with {content_word!r} in entity_name: "
        f"nearest co-occurrence is {nearest_gap} token position(s) apart, outside "
        f"the {_MAX_COLLOCATION_TOKEN_GAP}-position bound"
    )
