"""Producer-consumer orchestrator: reads the processing queue and drives
audio extraction, slide frame extraction, and OCR for each queued lecture.

A bounded semaphore protects the M5 Pro's unified memory from being
strangled by too many concurrent FFmpeg and Vision processes. Each
lecture's failure is isolated: one lecture failing does not abort the
rest of the queue.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from lecture_study.domain.models import (
    CanonicalFrame,
    LectureExtractionResult,
    LectureMetadata,
    SafeIdentifier,
)
from lecture_study.extraction.media_workers import extract_audio, extract_frames
from lecture_study.extraction.ocr_supervisor import extract_text_supervised

RAW_VIDEO_DIR = Path("workspace/data/01_raw")
AUDIO_OUTPUT_DIR = Path("workspace/data/02_extracted_audio")
SLIDES_OUTPUT_DIR = Path("workspace/data/03_extracted_slides")
CANONICAL_OUTPUT_DIR = Path("workspace/data/04_canonical")

_logger = logging.getLogger(__name__)


class RawVideoNotFoundError(RuntimeError):
    """Raised when a queued lecture has no exactly-one matching raw video
    file in RAW_VIDEO_DIR."""


@dataclass
class LectureExtractionOutcome:
    """Per-lecture result of one orchestrator run: either a completed
    LectureExtractionResult, or the error that isolated this lecture's
    failure from the rest of the queue."""

    lecture_id: str
    result: LectureExtractionResult | None
    error: str | None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class ExtractionOrchestrator:
    """Drives Phase 2 extraction for every lecture in queue_path."""

    def __init__(
        self,
        queue_path: Path,
        concurrency_limit: int = 2,
        ocr_concurrency_limit: int = 4,
    ) -> None:
        self._queue_path = queue_path
        self._lecture_semaphore = asyncio.Semaphore(concurrency_limit)
        # OCR fan-out within a single lecture is bounded independently:
        # without this, a lecture with hundreds of scene-change frames
        # would dispatch that many concurrent Vision requests at once,
        # defeating the point of lecture_semaphore's protection.
        self._ocr_semaphore = asyncio.Semaphore(ocr_concurrency_limit)

    async def run(self) -> list[LectureExtractionOutcome]:
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
            # Two entries sharing one lecture.id would concurrently write the
            # same AUDIO_OUTPUT_DIR/{id} and SLIDES_OUTPUT_DIR/{id}
            # subdirectory, reopening the collision decision 1 in ADR 0002
            # exists to prevent. Fail fast at read time instead of racing.
            raise ValueError(
                f"queue.json contains duplicate lecture ids: {self._queue_path}"
            )
        return lectures

    async def _process_lecture_bounded(
        self, lecture: LectureMetadata
    ) -> LectureExtractionOutcome:
        async with self._lecture_semaphore:
            return await self._process_lecture(lecture)

    async def _process_lecture(
        self, lecture: LectureMetadata
    ) -> LectureExtractionOutcome:
        try:
            result = await self._extract_lecture(lecture)
        except Exception as error:
            # Isolate this lecture's failure; the rest of the queue must
            # still be attempted. Logged here since the caller only sees
            # the stringified message, not a traceback.
            _logger.exception("extraction failed for lecture %s", lecture.id)
            return LectureExtractionOutcome(
                lecture_id=lecture.id, result=None, error=str(error)
            )
        return LectureExtractionOutcome(
            lecture_id=lecture.id, result=result, error=None
        )

    async def _extract_lecture(
        self, lecture: LectureMetadata
    ) -> LectureExtractionResult:
        video_path = self._resolve_raw_video_path(lecture.id)
        # Each lecture gets its own audio/slides subdirectory: FFmpeg names
        # slide frames sequentially from frame_0001.png regardless of
        # source video, so two lectures sharing one output directory under
        # concurrency_limit > 1 would collide and overwrite each other's
        # frames.
        audio_output_dir = AUDIO_OUTPUT_DIR / lecture.id
        slides_output_dir = SLIDES_OUTPUT_DIR / lecture.id
        # Run sequentially, not concurrently: both read the same
        # video_path from disk, so concurrent FFmpeg passes over it would
        # only add disk I/O contention without shortening wall-clock time.
        audio_path = await extract_audio(video_path, audio_output_dir)
        extracted_frames = await extract_frames(video_path, slides_output_dir)
        canonical_frames = await self._build_canonical_frames(extracted_frames)
        result = LectureExtractionResult(
            lecture_id=lecture.id, audio_path=audio_path, frames=canonical_frames
        )
        self._save_intermediate(result)
        return result

    async def _build_canonical_frames(
        self, extracted_frames: list[tuple[float, Path]]
    ) -> list[CanonicalFrame]:
        ocr_texts = await asyncio.gather(
            *(self._run_ocr(frame_path) for _, frame_path in extracted_frames)
        )
        return [
            CanonicalFrame(
                timestamp_sec=timestamp, ocr_text=ocr_text, image_path=frame_path
            )
            for (timestamp, frame_path), ocr_text in zip(extracted_frames, ocr_texts)
        ]

    async def _run_ocr(self, frame_path: Path) -> str:
        # The supervisor blocks while a killable child process runs Vision
        # (which can hang forever), so it runs in a worker thread rather
        # than blocking the event loop other lectures depend on.
        async with self._ocr_semaphore:
            return await asyncio.to_thread(extract_text_supervised, frame_path)

    @staticmethod
    def _resolve_raw_video_path(lecture_id: SafeIdentifier) -> Path:
        matches = sorted(RAW_VIDEO_DIR.glob(f"{lecture_id}.*"))
        if len(matches) != 1:
            raise RawVideoNotFoundError(
                f"expected exactly 1 raw video for lecture {lecture_id!r} in "
                f"{RAW_VIDEO_DIR}, found {len(matches)}"
            )
        return matches[0]

    @staticmethod
    def _save_intermediate(result: LectureExtractionResult) -> None:
        CANONICAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = CANONICAL_OUTPUT_DIR / f"{result.lecture_id}.json"
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)
        )
        tmp_path.replace(output_path)
