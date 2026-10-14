"""Domain vocabulary for the ASR pass, and the deterministic correction of
the specialist terms it still gets wrong.

Three defects were measured across all 25 transcripts in
`workspace/data/05_transcribed` and reproduced on cut audio with
whisper.cpp large-v3-turbo:

1. A truncated first syllable in "excisional", transcribed as "cisional"
   (4 occurrences, lecture 10002). Supplying the vocabulary below as
   whisper.cpp's initial prompt corrects it on the same audio, so nothing
   is rewritten afterwards for this one.
2. The same truncation in "histopatológico", transcribed as
   "estopatológico" -- 55 occurrences across 16 of the 25 lectures. The
   prompt corrects it on a new pass, and "estopatológico" is not a word in
   Portuguese, so it is also corrected deterministically here rather than
   left to the sampler.
3. "fascial" heard as "facial", in "camada/plano/margem facial de
   profundidade" (27 occurrences, lectures 10002 and 10007). The
   vocabulary prompt does not correct it: the same audio still yields
   "facial" with "plano fascial" in the prompt, while the speaker's own "a
   fáscia muscular" transcribes correctly, so the audio says "facial".
   The operator approved correcting it on the record -- study material
   must teach the correct term -- and it is corrected only in a lecture
   that discusses fascial depth, so the 23 lectures where "facial" is
   anatomically legitimate (nervo facial, paralisia facial, deformidade
   facial, veia linguofacial) are never touched.

The vocabulary prompt deliberately names no discipline and no
fascia term. It is carried into every decoding window of every lecture of
every discipline (ADR 0018 made the catalog multi-discipline), so a prompt
asserting an oncology context, or spelling "fascial", would bias the
decoder against the many lectures where "nervo facial" is the correct
reading. It lists only terms whose observed defect is a dropped first
syllable.

Every correction is reported alongside the corrected segments so the
operator can audit each rewrite against the audio.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from lecture_study.domain.models import TranscriptionSegment

DOMAIN_VOCABULARY_PROMPT = (
    "Aula de cirurgia veterinária de pequenos animais. "
    "Termos: biópsia excisional, biópsia incisional, exame histopatológico, "
    "margem lateral, margem profunda, mastocitoma, "
    "sarcoma de tecidos moles, carcinoma de células escamosas, "
    "exérese, transoperatório."
)

# The fascia reading is only correct under a depth context. "plano facial"
# is also a craniometric term and "margem facial" a dental one, so a
# lecture is rewritten only when it pairs the phrase with a depth context
# at least once -- see _lecture_discusses_fascial_depth, which takes that
# decision once for the whole lecture. The patterns below are that scan's
# vocabulary as well as the rewrite's.
_FASCIA_NOUN = r"(camada|camadas|plano|planos|margem|margens)"
_FASCIA_ADJECTIVE = r"(facial|faciais)"
_FASCIA_PHRASE_PATTERN = re.compile(
    rf"\b{_FASCIA_NOUN}(\s+){_FASCIA_ADJECTIVE}\b",
    re.IGNORECASE,
)
# The same phrase split across two segments: the noun ends the previous
# segment and the adjective opens this one. Measured on three real
# occurrences (10007 at 7378.9 and 7759.4, 10002 at 670.1).
_TRAILING_FASCIA_NOUN_PATTERN = re.compile(rf"\b{_FASCIA_NOUN}\s*$", re.IGNORECASE)
_LEADING_FASCIA_ADJECTIVE_PATTERN = re.compile(
    rf"^{_FASCIA_ADJECTIVE}\b", re.IGNORECASE
)
_DEPTH_CONTEXT_PATTERN = re.compile(
    r"\b(profundidade|abaixo|f[áa]scia|m[úu]sculo|musculatura|tumor)\b",
    re.IGNORECASE,
)
_CORRECTED_FASCIA_ADJECTIVE = {"facial": "fascial", "faciais": "fasciais"}
_CORRECTED_FASCIA_PATTERN = re.compile(r"\bfascia(?:l|is)\b", re.IGNORECASE)

# This speaker is segmented into fragments as short as two words, so the
# depth anchor can sit several segments away from the phrase it governs.
_CONTEXT_WINDOW_SEGMENTS = 3

# "estopatológico" is not a Portuguese word; the term is always
# "histopatológico". Corrected unconditionally, with no context needed.
_HISTOPATHOLOGY_PATTERN = re.compile(r"\bestopatol(ógic|ogia)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TerminologyCorrection:
    """One rewritten span, kept for the operator's audit against the audio."""

    start_time: float
    original_text: str
    corrected_text: str
    term: str


def correct_specialist_terminology(
    segments: Sequence[TranscriptionSegment],
) -> tuple[list[TranscriptionSegment], list[TerminologyCorrection]]:
    """Return the segments with specialist terminology corrected, and the
    report of every rewrite performed."""
    corrects_fascia = _lecture_discusses_fascial_depth(segments)
    corrected_segments: list[TranscriptionSegment] = []
    corrections: list[TerminologyCorrection] = []
    for index, segment in enumerate(segments):
        corrected_text = _correct_segment_text(
            segment.text,
            previous_text=segments[index - 1].text if index > 0 else "",
            corrects_fascia=corrects_fascia,
        )
        if corrected_text == segment.text:
            corrected_segments.append(segment)
            continue
        corrections.extend(
            TerminologyCorrection(
                start_time=segment.start_time,
                original_text=segment.text,
                corrected_text=corrected_text,
                term=term,
            )
            for term in _corrected_terms(segment.text, corrected_text)
        )
        corrected_segments.append(segment.model_copy(update={"text": corrected_text}))
    return corrected_segments, corrections


def _lecture_discusses_fascial_depth(
    segments: Sequence[TranscriptionSegment],
) -> bool:
    """True when the lecture uses the fascia phrase in a depth context at
    least once.

    The decision is taken once per lecture, not per occurrence: this
    speaker is segmented into fragments as short as "Camadas faciais.",
    which carry no anchor of their own and would otherwise be left
    inconsistent with the corrected sentence beside them. A lecture that
    never pairs the phrase with a depth context -- a dental "margem facial
    do dente", a craniometric "plano facial" -- is never touched.
    """
    for index, segment in enumerate(segments):
        if not _FASCIA_PHRASE_PATTERN.search(segment.text) and not _split_fascia_phrase(
            segments[index - 1].text if index > 0 else "", segment.text
        ):
            continue
        if _DEPTH_CONTEXT_PATTERN.search(_surrounding_text(segments, index)):
            return True
    return False


def _split_fascia_phrase(previous_text: str, text: str) -> bool:
    return bool(
        _TRAILING_FASCIA_NOUN_PATTERN.search(previous_text)
        and _LEADING_FASCIA_ADJECTIVE_PATTERN.search(text)
    )


def _surrounding_text(segments: Sequence[TranscriptionSegment], index: int) -> str:
    """The phrase plus `_CONTEXT_WINDOW_SEGMENTS` segments on each side --
    the window the depth anchor is looked for in."""
    window = segments[
        max(index - _CONTEXT_WINDOW_SEGMENTS, 0) : index + _CONTEXT_WINDOW_SEGMENTS + 1
    ]
    return " ".join(segment.text for segment in window)


def _correct_segment_text(text: str, previous_text: str, corrects_fascia: bool) -> str:
    corrected = _HISTOPATHOLOGY_PATTERN.sub(_rewrite_histopathology, text)
    if not corrects_fascia:
        return corrected
    corrected = _FASCIA_PHRASE_PATTERN.sub(_rewrite_fascia_phrase, corrected)
    if _TRAILING_FASCIA_NOUN_PATTERN.search(previous_text):
        corrected = _LEADING_FASCIA_ADJECTIVE_PATTERN.sub(
            _rewrite_fascia_adjective, corrected
        )
    return corrected


def _corrected_terms(original_text: str, corrected_text: str) -> list[str]:
    """Every term rewritten in this segment, so a segment carrying both
    defects is auditable under each of them."""
    terms: list[str] = []
    if _HISTOPATHOLOGY_PATTERN.search(original_text):
        terms.append("histopatológico")
    if _fascial_adjective_count(corrected_text) > _fascial_adjective_count(
        original_text
    ):
        terms.append("fascial")
    return terms


def _fascial_adjective_count(text: str) -> int:
    return len(_CORRECTED_FASCIA_PATTERN.findall(text))


def _rewrite_histopathology(match: re.Match[str]) -> str:
    return _matching_case(match.group(), f"histopatol{match.group(1)}")


def _rewrite_fascia_phrase(match: re.Match[str]) -> str:
    noun, separator, adjective = match.group(1), match.group(2), match.group(3)
    return f"{noun}{separator}{_corrected_adjective(adjective)}"


def _rewrite_fascia_adjective(match: re.Match[str]) -> str:
    return _corrected_adjective(match.group(1))


def _corrected_adjective(adjective: str) -> str:
    # A case-folded form outside the table (Unicode dotted capital I, which
    # IGNORECASE matches but lower() renders with a combining dot) leaves
    # the word as transcribed rather than failing the lecture.
    corrected = _CORRECTED_FASCIA_ADJECTIVE.get(adjective.lower())
    if corrected is None:
        return adjective
    return _matching_case(adjective, corrected)


def _matching_case(original: str, corrected: str) -> str:
    if original.isupper():
        return corrected.upper()
    if original[0].isupper():
        return corrected.capitalize()
    return corrected
