"""Domain entities for the lecture-to-study pipeline.

All models use strict Pydantic validation: no implicit type coercion.
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Pydantic implements Path fields internally as a str schema followed by a
# Path(...) conversion. Under model_config strict=True, that inner str check
# also becomes strict, so model_validate_json(text) succeeds (JSON has no
# Path type, so pydantic always feeds it a str) while model_validate(dict)
# on the exact same round-tripped payload fails, because a str is being
# rejected in favor of an already-a-Path check that the field never asks
# for. Field(strict=False) opts this field alone out of that inner
# strictness so both entry points behave identically; the model's own
# strict=True still governs every other field.
JsonRoundTrippablePath = Annotated[Path, Field(strict=False)]

# Lecture identifiers are spliced directly into filesystem glob patterns and
# output filenames (extraction/pipeline.py). A bare `str` would let a
# malformed id (e.g. containing "../") escape the intended data/ directory
# on both read and write, so every entry point that produces one is
# constrained to a safe filename-slug alphabet at the Pydantic boundary.
SafeIdentifier = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")]


class ArtifactExecutionMode(StrEnum):
    """Payload-embedded marker of the route that produced a derivative.

    Travels inside the artifact's own payload (not only an external
    manifest or sidecar), so it survives export, copy, and rename -- see
    ADR 0024's "Uncertified artifacts are always distinguishable from
    certified ones" enforced consequence. Neither member asserts
    certification: PIPELINE_DEFAULT is the certified pipeline's ordinary
    default, not an approval, and UNCERTIFIED_PERSONAL_STUDY is the
    explicit opt-in personal-study route.
    """

    PIPELINE_DEFAULT = "pipeline-default"
    UNCERTIFIED_PERSONAL_STUDY = "uncertified-personal-study"


class LectureMetadata(BaseModel):
    """Identifies a single source lecture to be processed."""

    model_config = ConfigDict(strict=True)

    id: SafeIdentifier
    title: str
    subject: str
    url: str
    estimated_duration_sec: float


class CanonicalFrame(BaseModel):
    """A single slide frame cross-referenced with its OCR text."""

    model_config = ConfigDict(strict=True)

    timestamp_sec: float
    ocr_text: str
    image_path: JsonRoundTrippablePath


class SemanticFact(BaseModel):
    """A single extracted clinical entity with traceability to its source."""

    model_config = ConfigDict(strict=True)

    entity_name: str
    category: str
    clinical_context: str
    timestamp_ref: float


class SemanticLedger(BaseModel):
    """Phase 5 handoff artifact: the SemanticFacts extracted by the local
    LLM from one Evidence Pack, keyed back to the lecture and pack it was
    extracted from. facts defaults to an empty list: a pack with no
    clinical content (e.g. an introduction or a joke) is a valid, not an
    error, outcome."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    pack_sequence: int = Field(ge=1)
    facts: list[SemanticFact] = Field(default_factory=list)


class LectureGlobalIndex(BaseModel):
    """Phase 9 handoff artifact: every SemanticFact of a lecture's
    SemanticLedgers, aggregated and deduplicated into one glossary and two
    clinically significant subsets. Built by deterministic text
    normalization (metadata/aggregator.py) -- no LLM inference -- so
    entity_name/clinical_context values that differ only in case,
    surrounding whitespace, or edge punctuation collapse into one entry
    instead of appearing as separate near-duplicates."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    glossary_terms: list[SemanticFact]
    dosages: list[SemanticFact]
    red_flags: list[SemanticFact]


class LectureExtractionResult(BaseModel):
    """Phase 2 handoff artifact: extracted audio and OCR'd slide frames for
    one lecture, consumed by the Phase 3 Whisper transcription step."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    audio_path: JsonRoundTrippablePath
    frames: list[CanonicalFrame]


class TranscriptionSegment(BaseModel):
    """A single Whisper-transcribed speech segment with its time bounds."""

    model_config = ConfigDict(strict=True)

    start_time: float
    end_time: float
    text: str

    @model_validator(mode="after")
    def _validate_non_negative_duration(self) -> "TranscriptionSegment":
        if self.start_time < 0:
            # Phase 4's chunker tiles Learning Unit windows over
            # [0, max(segment.end_time)); a negative start_time would fall
            # outside every window and be misassigned by
            # evidence/chunker.py's overlap-based allocation instead of
            # failing loud. Closed here, at the same domain choke point
            # SafeIdentifier already uses for path-splicing risks (ADR
            # 0003, decision 6), rather than downstream in the chunker.
            raise ValueError("start_time must not be negative")
        if self.end_time < self.start_time:
            raise ValueError("end_time must not be before start_time")
        return self


class LectureTranscriptionResult(BaseModel):
    """Phase 3 handoff artifact: Whisper-transcribed segments alongside the
    Phase 2 slide frames, both keyed to the same lecture, consumed by the
    Phase 4 Evidence Pack assembly step."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    frames: list[CanonicalFrame]
    segments: list[TranscriptionSegment]


class DurationExceptionReason(StrEnum):
    """Closed set of deliberate reasons a Learning Unit may leave 300-600 s."""

    LECTURE_SHORTER_THAN_MINIMUM_UNIT = "lecture_shorter_than_minimum_unit"


class DurationException(BaseModel):
    """Persisted marker: the unit's duration is deliberately outside policy."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    reason: DurationExceptionReason


class EvidencePack(BaseModel):
    """Phase 4 handoff artifact: one Frame-Driven Chunking Learning Unit,
    pairing an anchor slide frame with the transcript segments whose
    audio majority-overlaps its timestamp window.

    The Canonical Representation invariant: the window must be
    non-degenerate (end_time > start_time), and when anchor_frame is
    present, its timestamp must fall inside the half-open interval
    [start_time, end_time) of its Learning Unit. anchor_frame is None
    only for a pre-first-frame window (audio before any slide was shown)
    or a frame-less lecture. Half-open (not open on both ends) matches
    how Learning Units tile a lecture back to back: a frame exactly at
    start_time belongs to this pack, while a frame exactly at end_time
    belongs to the next one, so every timestamp maps to exactly one pack
    with no dead zone at the boundary.
    """

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    pack_sequence: int = Field(ge=1)
    start_time: float
    end_time: float
    anchor_frame: CanonicalFrame | None = None
    segments: list[TranscriptionSegment] = Field(default_factory=list)
    duration_exception: DurationException | None = None

    @model_validator(mode="after")
    def _validate_temporal_window(self) -> "EvidencePack":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        if self.anchor_frame is not None and not (
            self.start_time <= self.anchor_frame.timestamp_sec < self.end_time
        ):
            raise ValueError(
                f"anchor_frame timestamp {self.anchor_frame.timestamp_sec} is "
                f"outside the half-open interval [{self.start_time}, "
                f"{self.end_time})"
            )
        return self


class LearningUnitDraft(BaseModel):
    """Phase 6 handoff artifact: the first-draft study guide section
    synthesized by the local LLM from one Evidence Pack and its
    corresponding Semantic Ledger. clinical_warnings defaults to an empty
    list: a draft grounded in a pack with no extracted clinical facts is a
    valid, not an error, outcome, matching SemanticLedger.facts."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    pack_sequence: int = Field(ge=1)
    title: str
    body_markdown: str
    clinical_warnings: list[str] = Field(default_factory=list)


class CriticReview(BaseModel):
    """Phase 7 handoff artifact: the local LLM critic's verdict on one
    LearningUnitDraft, checked against its source EvidencePack and
    SemanticLedger. feedback always carries the reviewer's reasoning --
    the unsupported claim or ignored red flag on failure, or the
    grounding rationale on approval -- since the Repair Loop (Phase 7's
    CriticOrchestrator) feeds it straight back into generate_draft as the
    correction instruction for the next attempt."""

    model_config = ConfigDict(strict=True)

    passed: bool
    hallucination_score: float = Field(ge=0.0, le=1.0)
    feedback: str


class ValidatedLearningUnit(BaseModel):
    """Phase 7 handoff artifact: a LearningUnitDraft paired with the
    CriticReview that either approved it outright or was its last verdict
    once the Repair Loop exhausted its retries. review.passed is the
    caller's signal for which case applies; a failed review is still
    persisted (not discarded) so an exhausted-retries unit remains
    traceable for manual follow-up instead of silently vanishing."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    pack_sequence: int = Field(ge=1)
    content: LearningUnitDraft
    review: CriticReview


class CanonicalLectureDocument(BaseModel):
    """Phase 8 handoff artifact: every ValidatedLearningUnit of a lecture,
    assembled into a single coherent document in strict chronological
    (pack_sequence) order. This is the terminal artifact of the pipeline's
    validation chain -- clinical reasoning across a lecture depends on its
    units appearing in the order they were taught."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    total_units: int = Field(ge=0)
    units: list[ValidatedLearningUnit]
    assembled_at: float

    @model_validator(mode="after")
    def _validate_total_units_matches_units(self) -> "CanonicalLectureDocument":
        if self.total_units != len(self.units):
            raise ValueError(
                f"total_units ({self.total_units}) does not match the number "
                f"of units ({len(self.units)})"
            )
        return self


class Flashcard(BaseModel):
    """Phase 12 handoff artifact: one Anki-importable spaced-repetition
    card derived deterministically from a single glossary term, dosage, or
    red-flag SemanticFact of a lecture's LectureGlobalIndex -- no LLM
    inference, so the card's clinical content is a strict copy of
    already-vetted Phase 5 extraction, never a paraphrase."""

    model_config = ConfigDict(strict=True)

    front: str
    back: str
    tags: list[str]


class MockQuestion(BaseModel):
    """Phase 13 handoff artifact: one multiple-choice clinical exam
    question synthesized by a local LLM pass, grounded strictly in a
    lecture's CanonicalLectureDocument body_markdown -- unlike Phase 12's
    Flashcard, this is new LLM inference, not a copy of already-vetted
    Phase 5 extraction, so the JSON contract below is enforced at both the
    Pydantic boundary and by exams/generator.py's Critic/Repair retry."""

    model_config = ConfigDict(strict=True)

    question_text: str
    options: dict[str, str]
    correct_answer: str
    explanation: str
    # Learning-unit pack sequence this question is grounded in (2026-09-15,
    # ADR pending). Optional and defaulted so Phase 13's certified local
    # llama.cpp route -- which has no notion of a learning-unit split -- keeps
    # emitting a schema without this field; only the Codex-driven
    # personal-study exam generation route (scripts/) populates it, to let
    # the unified critical-review document interleave each question next to
    # the chapter it was written for.
    pack_sequence: int | None = None

    @model_validator(mode="after")
    def _validate_options_are_exactly_a_through_d(self) -> "MockQuestion":
        if set(self.options) != {"A", "B", "C", "D"}:
            raise ValueError(
                "options must have exactly the keys 'A', 'B', 'C', 'D', got "
                f"{sorted(self.options)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_correct_answer_is_a_declared_option(self) -> "MockQuestion":
        if self.correct_answer not in self.options:
            raise ValueError(
                f"correct_answer {self.correct_answer!r} is not one of the "
                f"declared options {sorted(self.options)}"
            )
        return self


class MockExam(BaseModel):
    """Phase 13 handoff artifact: a lecture's full clinical mock exam --
    every MockQuestion generated for it, in generation order."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    questions: list[MockQuestion]
    execution_mode: ArtifactExecutionMode = ArtifactExecutionMode.PIPELINE_DEFAULT


class PodcastLine(BaseModel):
    """Phase 14 handoff artifact: one line of a two-speaker "Deep Dive"
    podcast dialogue, synthesized by a local LLM pass grounded in one
    CanonicalLectureDocument unit. speaker is constrained to exactly the
    two fixed roles a future Phase 15 TTS pass would map to distinct
    voice profiles -- Literal["Host", "Expert"] instead of a bare str, so
    that mapping is a simple, exhaustive match rather than a fuzzy string
    comparison. source_unit_id preserves clinical traceability from the
    finished audio material back to the specific CanonicalLectureDocument
    unit a line's claim was grounded in; exams/generator.py's sibling
    MockExamGenerator.generate_exam has no equivalent field since a mock
    exam question is not attributed to one lecture unit the way a
    podcast line is."""

    model_config = ConfigDict(strict=True)

    speaker: Literal["Host", "Expert"]
    text: str
    source_unit_id: str

    @model_validator(mode="after")
    def _validate_text_is_not_blank(self) -> "PodcastLine":
        if not self.text.strip():
            raise ValueError("text must not be blank")
        return self


class PodcastScript(BaseModel):
    """Phase 14 handoff artifact: a lecture's full two-speaker podcast
    dialogue, in speaking order, plus the episode title the LLM pass
    itself proposes. dialogue requires at least one line and title must
    not be blank -- a schema-valid-but-empty response (zero dialogue
    lines, or a whitespace-only title) is a degenerate LLM output, not a
    legitimate zero-content podcast, and must be rejected at this same
    Pydantic boundary rather than reaching podcast/compiler.py as a
    silently "successful" episode."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    title: str
    dialogue: list[PodcastLine] = Field(min_length=1)
    execution_mode: ArtifactExecutionMode = ArtifactExecutionMode.PIPELINE_DEFAULT

    @model_validator(mode="after")
    def _validate_title_is_not_blank(self) -> "PodcastScript":
        if not self.title.strip():
            raise ValueError("title must not be blank")
        return self


class FigureCaption(BaseModel):
    """One extracted lecture frame described by the local vision model.

    timestamp_seconds is always parsed from the frame's own filename by
    captioning/catalog_store.py -- never taken from the model -- so a
    caption's traceability to its moment in the lecture cannot be spoofed
    by a model response, per D-22's traceability requirement."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    frame_filename: str
    timestamp_seconds: float
    caption: str
    execution_mode: ArtifactExecutionMode = ArtifactExecutionMode.PIPELINE_DEFAULT


class LectureFigureCatalog(BaseModel):
    """Phase-independent handoff artifact: every FigureCaption produced for
    one lecture's extracted frames, ready for the study material to embed."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    figures: list[FigureCaption]
    captioned_at: float

    @model_validator(mode="after")
    def _validate_no_duplicate_frame_filenames(self) -> "LectureFigureCatalog":
        frame_filenames = [figure.frame_filename for figure in self.figures]
        if len(frame_filenames) != len(set(frame_filenames)):
            raise ValueError(
                "figures must not contain two entries for the same frame_filename"
            )
        return self


class PersonalStudyFlashcard(BaseModel):
    """One inference-generated clinical flashcard produced by the
    personal-study route (ADR 0024), grounded strictly in one unit of a
    lecture's CanonicalLectureDocument -- unlike Phase 12's Flashcard, this
    is new LLM inference, not a deterministic copy of Phase 9's
    LectureGlobalIndex, so the JSON contract below is enforced at both the
    Pydantic boundary and by the generating Codex script's bounded
    retry."""

    model_config = ConfigDict(strict=True)

    prompt: str
    answer: str
    source_unit_sequence: int = Field(ge=1)


class PersonalStudyFlashcardDeck(BaseModel):
    """Personal-study handoff artifact (ADR 0024): a lecture's full
    inference-generated clinical flashcard deck, every
    PersonalStudyFlashcard generated for it, in generation order. Two
    cards sharing the same prompt are rejected: a spaced-repetition deck
    with a duplicate front is a degenerate LLM output, not a legitimate
    zero-content duplication, matching LectureFigureCatalog's own
    duplicate-identity guard above."""

    model_config = ConfigDict(strict=True)

    lecture_id: SafeIdentifier
    cards: list[PersonalStudyFlashcard]
    execution_mode: ArtifactExecutionMode = ArtifactExecutionMode.PIPELINE_DEFAULT

    @model_validator(mode="after")
    def _validate_no_duplicate_prompts(self) -> "PersonalStudyFlashcardDeck":
        prompts = [card.prompt for card in self.cards]
        if len(prompts) != len(set(prompts)):
            raise ValueError("cards must not contain two entries with the same prompt")
        return self
