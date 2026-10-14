"""Ingestion queue writer.

`workspace/data/queue.json` is the ingestion queue every Phase 2-15
orchestrator reads. `upsert_lecture_in_queue` is the one writer that turns a
lesson record (`activity_id`, `display_title`, `url`) into a
`LectureMetadata` row and merges it into that queue, so no phase needs to
know where a lecture came from -- it only ever looks at `queue.json`.
`study_materials.local_lectures` uses it to enqueue operator-registered
local videos.

`QUEUE_PATH` is a local constant, not an import of
`cli.orchestrator.QUEUE_PATH`: `cli.orchestrator` is the Phase 2-15
composition root and transitively imports every phase's heavy dependencies
(including `extraction.pipeline`'s macOS-only `pyobjc`/Vision bindings),
which this writer has no functional need for.
`tests/test_workspace_paths.py` guards the two constants staying equal.

`estimated_duration_sec` is always written as `0.0`: a lesson record
carries no duration signal, and no consumer downstream of `queue.json`
depends on that field today -- probing the video with `ffprobe` would add a
subprocess dependency this writer has no consumer-driven need for.

`_queue_lock` serializes the read-merge-write critical section so two
concurrent callers in the same process cannot race and silently drop each
other's row. A second OS process writing `queue.json` at the same time is
out of scope.
"""

import json
import threading
from pathlib import Path
from typing import Any

from lecture_study.domain.models import LectureMetadata

QUEUE_PATH = Path("workspace/data/queue.json")

_UNKNOWN_DURATION_SEC = 0.0

_queue_lock = threading.Lock()


class CatalogBridgeError(RuntimeError):
    """Raised when a lesson cannot be enqueued into `queue.json`.

    Wraps a malformed lesson (missing keys or invalid values), a corrupt
    pre-existing queue file, or a filesystem failure during the write --
    the one exception type a caller needs to catch for this writer.
    """


def upsert_lecture_in_queue(
    lesson: dict[str, Any], subject: str, queue_path: Path = QUEUE_PATH
) -> LectureMetadata:
    """Write/refresh one lesson's row in the ingestion queue.

    `lesson` must carry `activity_id`, `display_title` and `url`; `subject`
    becomes the row's discipline. Returns the `LectureMetadata` that was
    persisted. Raises `CatalogBridgeError` on a malformed `lesson`, a
    corrupt existing `queue.json`, or a filesystem failure -- never
    partially writes the queue file.
    """
    try:
        lecture = _to_lecture_metadata(lesson, subject)
        with _queue_lock:
            queue = _merge(_read_queue(queue_path), lecture)
            _write_queue(queue, queue_path)
        return lecture
    except (KeyError, ValueError, OSError) as exc:
        raise CatalogBridgeError(
            f"não foi possível enfileirar a aula em {queue_path}: {exc}"
        ) from exc


def _to_lecture_metadata(lesson: dict[str, Any], subject: str) -> LectureMetadata:
    return LectureMetadata(
        id=lesson["activity_id"],
        title=lesson["display_title"],
        subject=subject,
        url=lesson["url"],
        estimated_duration_sec=_UNKNOWN_DURATION_SEC,
    )


def _read_queue(queue_path: Path) -> list[LectureMetadata]:
    if not queue_path.is_file():
        return []
    payload = json.loads(queue_path.read_text())
    return [LectureMetadata.model_validate(item) for item in payload]


def _merge(
    queue: list[LectureMetadata], lecture: LectureMetadata
) -> list[LectureMetadata]:
    # Keyed merge: a re-enqueued lecture refreshes its existing row in
    # place, a new one is appended -- never duplicated.
    by_id = {item.id: item for item in queue}
    by_id[lecture.id] = lecture
    return list(by_id.values())


def _write_queue(queue: list[LectureMetadata], queue_path: Path) -> None:
    """Atomic tmp-file-then-replace write, so a concurrent reader never
    observes a partially written `queue.json`. Called only while
    `_queue_lock` is held, so this is also the only in-process writer at
    any given instant."""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [lecture.model_dump(mode="json") for lecture in queue]
    tmp_path = queue_path.with_suffix(queue_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp_path.replace(queue_path)
