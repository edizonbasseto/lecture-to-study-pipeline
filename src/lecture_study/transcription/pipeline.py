"""Producer-consumer orchestrator: reads each queued lecture's Phase 2
canonical handoff and drives Whisper transcription against it.

Concurrency defaults to 1: the M5 Pro's Metal-backed whisper.cpp saturates
the Neural Engine, so running more than one transcription at once causes
memory contention and local crashes, unlike the I/O-bound FFmpeg workers in
Phase 2. Each lecture's failure is isolated: one lecture failing does not
abort the rest of the queue.

Artifact-Driven idempotency (operator decision): `queue.json` is read-only
here -- it is only ever a list of intentions, never mutated by this or any
downstream phase. A lecture is treated as already processed when its
TRANSCRIBED_OUTPUT_DIR/{id}_transcription.json artifact exists on disk; no
status field is written back to the queue to track that. A failure is
logged and isolated to that lecture, never persisted as queue metadata.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from lecture_study.domain.models import (
    LectureExtractionResult,
    LectureMetadata,
    LectureTranscriptionResult,
    SafeIdentifier,
)
from lecture_study.transcription.terminology import (
    TerminologyCorrection,
    correct_specialist_terminology,
)
from lecture_study.transcription.whisper_worker import run_transcription

CANONICAL_INPUT_DIR = Path("workspace/data/04_canonical")
TRANSCRIBED_OUTPUT_DIR = Path("workspace/data/05_transcribed")

_logger = logging.getLogger(__name__)


class CanonicalHandoffNotFoundError(RuntimeError):
    """Raised when a queued lecture has no Phase 2 canonical handoff file."""


@dataclass
class LectureTranscriptionOutcome:
    """Per-lecture result of one orchestrator run: either a completed
    LectureTranscriptionResult, or the error that isolated this lecture's
    failure from the rest of the queue."""

    lecture_id: str
    result: LectureTranscriptionResult | None
    error: str | None
    skipped: bool = False

    @property
    def succeeded(self) -> bool:
        return self.error is None


class TranscriptionOrchestrator:
    """Drives Phase 3 Whisper transcription for every lecture in queue_path."""

    def __init__(
        self, queue_path: Path, model_path: Path, concurrency_limit: int = 1
    ) -> None:
        self._queue_path = queue_path
        self._model_path = model_path
        self._semaphore = asyncio.Semaphore(concurrency_limit)

    async def run(self) -> list[LectureTranscriptionOutcome]:
        """Process every queued lecture, bounded by the concurrency limit."""
        lectures = self._read_queue()
        return list(
            await asyncio.gather(
                *(self._process_lecture_bounded(lecture) for lecture in lectures)
            )
        )

    def _read_queue(self) -> list[LectureMetadata]:
        payload = json.loads(self._queue_path.read_text())
        lectures = [LectureMetadata.model_validate(item) for item in payload]
        seen_ids = {lecture.id for lecture in lectures}
        if len(seen_ids) != len(lectures):
            # Two entries sharing one lecture.id would race on the same
            # TRANSCRIBED_OUTPUT_DIR/{id}_transcription.json output file.
            raise ValueError(
                f"queue.json contains duplicate lecture ids: {self._queue_path}"
            )
        return lectures

    async def _process_lecture_bounded(
        self, lecture: LectureMetadata
    ) -> LectureTranscriptionOutcome:
        async with self._semaphore:
            return await self._process_lecture(lecture)

    async def _process_lecture(
        self, lecture: LectureMetadata
    ) -> LectureTranscriptionOutcome:
        if self._transcription_output_path(lecture.id).exists():
            # Filesystem-driven idempotency: queue.json is read-only for this
            # phase (Artifact-Driven architecture, operator decision). A
            # lecture is "already done" precisely when its output artifact
            # exists on disk -- no status flag is written back to the queue.
            _logger.info(
                "skipping lecture %s: transcription artifact already exists",
                lecture.id,
            )
            return LectureTranscriptionOutcome(
                lecture_id=lecture.id, result=None, error=None, skipped=True
            )
        try:
            result = await self._transcribe_lecture(lecture)
        except Exception as error:
            # Isolate this lecture's failure; the rest of the queue must
            # still be attempted. Logged here since the caller only sees
            # the stringified message, not a traceback.
            _logger.exception("transcription failed for lecture %s", lecture.id)
            return LectureTranscriptionOutcome(
                lecture_id=lecture.id, result=None, error=str(error)
            )
        return LectureTranscriptionOutcome(
            lecture_id=lecture.id, result=result, error=None
        )

    async def _transcribe_lecture(
        self, lecture: LectureMetadata
    ) -> LectureTranscriptionResult:
        handoff = self._read_canonical_handoff(lecture.id)
        transcribed = await run_transcription(handoff.audio_path, self._model_path)
        segments, corrections = correct_specialist_terminology(transcribed)
        result = LectureTranscriptionResult(
            lecture_id=lecture.id, frames=handoff.frames, segments=segments
        )
        # The audit record is written first, because the transcription
        # artifact is this phase's idempotency marker: were the order
        # reversed, a failure between the two writes would leave a
        # corrected transcript that every later run skips, with no record
        # of what was rewritten.
        self._save_terminology_corrections(lecture.id, corrections)
        self._save_transcription(result)
        return result

    @staticmethod
    def _read_canonical_handoff(lecture_id: SafeIdentifier) -> LectureExtractionResult:
        handoff_path = CANONICAL_INPUT_DIR / f"{lecture_id}.json"
        if not handoff_path.exists():
            raise CanonicalHandoffNotFoundError(
                f"no Phase 2 canonical handoff found for lecture {lecture_id!r} "
                f"at {handoff_path}"
            )
        return LectureExtractionResult.model_validate_json(handoff_path.read_text())

    @staticmethod
    def _transcription_output_path(lecture_id: SafeIdentifier) -> Path:
        return TRANSCRIBED_OUTPUT_DIR / f"{lecture_id}_transcription.json"

    @staticmethod
    def _save_transcription(result: LectureTranscriptionResult) -> None:
        TRANSCRIBED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = TranscriptionOrchestrator._transcription_output_path(
            result.lecture_id
        )
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)
        )
        tmp_path.replace(output_path)

    @staticmethod
    def _terminology_corrections_path(lecture_id: SafeIdentifier) -> Path:
        return TRANSCRIBED_OUTPUT_DIR / f"{lecture_id}_terminology_corrections.json"

    @staticmethod
    def _save_terminology_corrections(
        lecture_id: SafeIdentifier, corrections: list[TerminologyCorrection]
    ) -> None:
        """Write the audit record of every specialist term rewritten after
        the ASR pass, so each rewrite can be checked against the audio.

        The file is written even when nothing was corrected: an empty
        report states that the lecture needed no rewrite, which a missing
        file cannot.
        """
        TRANSCRIBED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = TranscriptionOrchestrator._terminology_corrections_path(
            lecture_id
        )
        payload = [
            {
                "start_time": correction.start_time,
                "original_text": correction.original_text,
                "corrected_text": correction.corrected_text,
                "term": correction.term,
            }
            for correction in corrections
        ]
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        tmp_path.replace(output_path)
