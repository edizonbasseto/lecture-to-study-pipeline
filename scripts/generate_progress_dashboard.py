#!/usr/bin/env python3
"""Compute a point-in-time snapshot of pipeline progress across the whole
lecture catalog, for an administrative dashboard.

Reads workspace/study-guides/catalogo.md for the lecture list (semester,
discipline, date, title, id, catalog status) and cross-references each
lecture_id against the pipeline's own on-disk stage directories under
workspace/data/ -- the catalog's own "Concluída"/"Pendente" tag reflects
whether a lecture has a published site page, not per-phase pipeline
progress, so the two are reported side by side rather than merged.

Prints one JSON object to stdout; this script has no side effects on
workspace/data and never writes anything.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CATALOG_PATH = Path("workspace/study-guides/catalogo.md")
DATA_ROOT = Path("workspace/data")

# (stage directory name, human label) in pipeline order. Each is checked
# per lecture_id as DATA_ROOT/<stage>/<lecture_id>/, counting entries.
_STAGES: tuple[tuple[str, str], ...] = (
    ("06_evidence_packs", "evidence_packs"),
    ("07_semantic_ledgers", "semantic_ledgers"),
    ("08_drafts", "drafts"),
    ("09_validated_units", "validated_units"),
    ("14_mock_exams", "mock_exam"),
    ("15_podcast_scripts", "podcast_script"),
    ("16_podcast_audio", "podcast_audio"),
    ("17_figure_captions", "figure_captions"),
    ("18_personal_study_flashcards", "flashcards"),
)

_CATALOG_ENTRY = re.compile(
    r"^-\s+(?:(?P<date>\d{2}/\d{2}/\d{4})\s+-\s+)?"
    r"(?:\[(?P<linked_title>[^\]]+)\]\([^)]*\)|(?P<plain_title>[^(]+?))\s*"
    r"\(`(?P<lecture_id>\d+)`\)\s*-\s*\*\*(?P<status>[^*]+)\*\*"
)


def main() -> int:
    catalog_entries = _parse_catalog(CATALOG_PATH)
    lectures = [_build_lecture_snapshot(entry) for entry in catalog_entries]
    summary = _summarize(lectures)
    print(
        json.dumps(
            {"lectures": lectures, "summary": summary}, indent=2, ensure_ascii=False
        )
    )
    return 0


def _parse_catalog(catalog_path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    semester = ""
    section = ""
    for raw_line in catalog_path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("## "):
            semester = raw_line.removeprefix("## ").strip()
            continue
        if raw_line.startswith("### ") or raw_line.startswith("#### "):
            section = raw_line.lstrip("#").strip()
            continue
        match = _CATALOG_ENTRY.match(raw_line)
        if not match:
            continue
        title = (match["linked_title"] or match["plain_title"] or "").strip()
        title = re.sub(r"\s*Super Video$", "", title)
        entries.append(
            {
                "semester": semester,
                "section": section,
                "date": match["date"] or "",
                "title": title,
                "lecture_id": match["lecture_id"],
                "catalog_status": match["status"].strip(),
            }
        )
    return entries


def _build_lecture_snapshot(entry: dict[str, str]) -> dict[str, object]:
    lecture_id = entry["lecture_id"]
    stage_counts = {
        label: _count_entries(lecture_id, stage_dir) for stage_dir, label in _STAGES
    }
    expected_units = stage_counts["evidence_packs"]
    return {
        **entry,
        "stage_counts": stage_counts,
        "expected_units": expected_units,
        "pipeline_started": expected_units > 0,
        "semantic_complete": expected_units > 0
        and stage_counts["semantic_ledgers"] >= expected_units,
        "drafts_complete": expected_units > 0
        and stage_counts["drafts"] >= expected_units,
        "critic_complete": expected_units > 0
        and stage_counts["validated_units"] >= expected_units,
        "exam_ready": stage_counts["mock_exam"] > 0,
        "podcast_ready": stage_counts["podcast_audio"] > 0,
    }


def _count_entries(lecture_id: str, stage_dir: str) -> int:
    directory = DATA_ROOT / stage_dir / lecture_id
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.iterdir())


def _summarize(lectures: list[dict[str, object]]) -> dict[str, object]:
    by_catalog_status: dict[str, int] = {}
    for lecture in lectures:
        status = str(lecture["catalog_status"])
        by_catalog_status[status] = by_catalog_status.get(status, 0) + 1
    started = sum(1 for lecture in lectures if lecture["pipeline_started"])
    critic_complete = sum(1 for lecture in lectures if lecture["critic_complete"])
    return {
        "total_lectures": len(lectures),
        "by_catalog_status": by_catalog_status,
        "pipeline_started": started,
        "critic_complete": critic_complete,
    }


if __name__ == "__main__":
    if not CATALOG_PATH.exists():
        print(f"error: catalog not found at {CATALOG_PATH}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main())
