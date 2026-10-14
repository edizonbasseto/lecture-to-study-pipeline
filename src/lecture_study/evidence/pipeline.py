"""Producer-consumer orchestrator: reads each queued lecture's Phase 3
transcription handoff and assembles Frame-Driven Chunking Evidence Packs.

Concurrency defaults to 4: pack assembly is pure CPU/RAM work (no NPU,
Metal, or subprocess calls), unlike Phase 3's single-flight whisper.cpp
invocation, so several lectures can be chunked concurrently without
resource contention.
"""

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from lecture_study.domain.models import (
    EvidencePack,
    LectureMetadata,
    LectureTranscriptionResult,
    SafeIdentifier,
)
from lecture_study.evidence.chunker import build_evidence_packs

TRANSCRIBED_INPUT_DIR = Path("workspace/data/05_transcribed")
EVIDENCE_PACKS_OUTPUT_DIR = Path("workspace/data/06_evidence_packs")

_logger = logging.getLogger(__name__)


class TranscriptionHandoffNotFoundError(RuntimeError):
    """Raised when a queued lecture has no Phase 3 transcription handoff
    file."""


@dataclass
class LectureEvidenceOutcome:
    """Per-lecture result of one orchestrator run: either the completed
    EvidencePack list, or the error that isolated this lecture's failure
    from the rest of the queue."""

    lecture_id: str
    packs: list[EvidencePack] | None
    error: str | None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class EvidencePackOrchestrator:
    """Drives Phase 4 Evidence Pack assembly for every lecture in
    queue_path."""

    def __init__(self, queue_path: Path, concurrency_limit: int = 4) -> None:
        self._queue_path = queue_path
        self._semaphore = asyncio.Semaphore(concurrency_limit)

    async def run(self) -> list[LectureEvidenceOutcome]:
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
            # EVIDENCE_PACKS_OUTPUT_DIR/{id}/ output directory.
            raise ValueError(
                f"queue.json contains duplicate lecture ids: {self._queue_path}"
            )
        return lectures

    async def _process_lecture_bounded(
        self, lecture: LectureMetadata
    ) -> LectureEvidenceOutcome:
        async with self._semaphore:
            return await self._process_lecture(lecture)

    async def _process_lecture(
        self, lecture: LectureMetadata
    ) -> LectureEvidenceOutcome:
        try:
            # Offloaded to a worker thread: _build_packs is synchronous
            # CPU/file-I/O work, and with no other await point in this
            # path, running it inline would starve the event loop and
            # make concurrency_limit have no observable effect.
            packs = await asyncio.to_thread(self._build_packs, lecture.id)
        except Exception as error:
            # Isolate this lecture's failure; the rest of the queue must
            # still be attempted. Logged here since the caller only sees
            # the stringified message, not a traceback.
            _logger.exception(
                "evidence pack assembly failed for lecture %s", lecture.id
            )
            return LectureEvidenceOutcome(
                lecture_id=lecture.id, packs=None, error=str(error)
            )
        return LectureEvidenceOutcome(lecture_id=lecture.id, packs=packs, error=None)

    def _build_packs(self, lecture_id: SafeIdentifier) -> list[EvidencePack]:
        handoff = self._read_transcription_handoff(lecture_id)
        packs = build_evidence_packs(handoff)
        self._save_packs(lecture_id, packs)
        return packs

    @staticmethod
    def _read_transcription_handoff(
        lecture_id: SafeIdentifier,
    ) -> LectureTranscriptionResult:
        handoff_path = TRANSCRIBED_INPUT_DIR / f"{lecture_id}_transcription.json"
        if not handoff_path.exists():
            raise TranscriptionHandoffNotFoundError(
                f"no Phase 3 transcription handoff found for lecture "
                f"{lecture_id!r} at {handoff_path}"
            )
        return LectureTranscriptionResult.model_validate_json(handoff_path.read_text())

    @staticmethod
    def _save_packs(lecture_id: SafeIdentifier, packs: list[EvidencePack]) -> None:
        # Built off to the side, then cut over via two back-to-back
        # rename()s (POSIX-atomic on the same filesystem): the old
        # lecture_dir is renamed out of the way before staging_dir takes
        # its place, so lecture_dir is either the untouched previous
        # good state or the fully-built new state at every instant. A
        # crash-then-rerun always converges: leftover staging/backup
        # directories from an interrupted run are cleared before this
        # run rebuilds from scratch.
        lecture_dir = EVIDENCE_PACKS_OUTPUT_DIR / lecture_id
        staging_dir = EVIDENCE_PACKS_OUTPUT_DIR / f"{lecture_id}.staging"
        backup_dir = EVIDENCE_PACKS_OUTPUT_DIR / f"{lecture_id}.previous"
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        staging_dir.mkdir(parents=True)

        for pack in packs:
            output_path = staging_dir / f"pack_{pack.pack_sequence:03d}.json"
            output_path.write_text(
                json.dumps(pack.model_dump(mode="json"), indent=2, ensure_ascii=False)
            )

        if lecture_dir.exists():
            lecture_dir.rename(backup_dir)
        staging_dir.replace(lecture_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
