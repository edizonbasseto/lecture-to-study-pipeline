"""Unit tests for `lecture_study.ops.catalog_bridge`: field
mapping, keyed merge, error wrapping, and same-process write safety under
concurrent callers."""

import json
import threading
from pathlib import Path

import pytest

from lecture_study.ops import catalog_bridge

_LESSON = {
    "activity_id": "10001",
    "moodle_order": 1,
    "section": "Seção 1",
    "display_title": "Aula 1",
    "url": "https://lms.example.edu/mod/supervideo/view.php?id=10001",
    "enabled": True,
    "discovery_timestamp": "2026-01-01T00:00:00+00:00",
}


def test_upsert_maps_lesson_fields_into_lecture_metadata(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.json"

    lecture = catalog_bridge.upsert_lecture_in_queue(
        _LESSON, "Disciplina A", queue_path=queue_path
    )

    assert lecture.id == "10001"
    assert lecture.title == "Aula 1"
    assert lecture.subject == "Disciplina A"
    assert lecture.url == _LESSON["url"]
    assert lecture.estimated_duration_sec == 0.0


def test_upsert_refreshes_existing_row_instead_of_duplicating(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.json"
    catalog_bridge.upsert_lecture_in_queue(_LESSON, "Antigo", queue_path=queue_path)

    updated_lesson = {**_LESSON, "display_title": "Aula 1 (revisada)"}
    catalog_bridge.upsert_lecture_in_queue(
        updated_lesson, "Disciplina A", queue_path=queue_path
    )

    payload = json.loads(queue_path.read_text())
    assert len(payload) == 1
    assert payload[0]["title"] == "Aula 1 (revisada)"
    assert payload[0]["subject"] == "Disciplina A"


def test_upsert_wraps_malformed_lesson_in_catalog_bridge_error(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.json"
    malformed_lesson = {"activity_id": "10001"}  # missing display_title/url

    with pytest.raises(catalog_bridge.CatalogBridgeError):
        catalog_bridge.upsert_lecture_in_queue(
            malformed_lesson, "Subject", queue_path=queue_path
        )
    assert not queue_path.exists()


def test_upsert_wraps_corrupt_existing_queue_in_catalog_bridge_error(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text("not valid json")

    with pytest.raises(catalog_bridge.CatalogBridgeError):
        catalog_bridge.upsert_lecture_in_queue(
            _LESSON, "Subject", queue_path=queue_path
        )


def test_upsert_serializes_concurrent_writers_without_losing_rows(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "queue.json"
    lecture_ids = [f"lesson-{i}" for i in range(8)]
    lessons = [
        {**_LESSON, "activity_id": lecture_id, "display_title": lecture_id}
        for lecture_id in lecture_ids
    ]
    start_barrier = threading.Barrier(len(lessons))

    def _upsert(lesson: dict) -> None:
        start_barrier.wait()  # forces every thread to race the lock together
        catalog_bridge.upsert_lecture_in_queue(lesson, "Subject", queue_path=queue_path)

    threads = [threading.Thread(target=_upsert, args=(lesson,)) for lesson in lessons]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    payload = json.loads(queue_path.read_text())
    assert {row["id"] for row in payload} == set(lecture_ids)
