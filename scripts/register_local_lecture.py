#!/usr/bin/env python3
"""Register a local video (no portal id) so the lecture pipeline can process it."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys
from subprocess import SubprocessError

from pydantic import ValidationError

from lecture_study.ops.catalog_bridge import QUEUE_PATH

from lecture_study.study_materials.academic_catalog import (
    load_academic_catalog,
    load_academic_schedule,
)
from lecture_study.study_materials.local_lectures import (
    DEFAULT_SECTION,
    LOCAL_LECTURES_PATH,
    VIDEO_ONLY_KIND,
    LocalLecture,
    PUBLIC_STUDY_GUIDES_ROOT,
    RAW_VIDEO_DIR,
    LocalLectureError,
    default_scheduled_date,
    register_local_lecture,
)


def main() -> int:
    args = _argument_parser().parse_args()
    try:
        lecture = LocalLecture(
            lecture_id=args.lecture_id,
            title=args.title,
            discipline=args.discipline,
            section=args.section,
            scheduled_date=args.scheduled_date or default_scheduled_date(args.semester),
            publish=args.publish,
            kind=VIDEO_ONLY_KIND if args.video_only else "study",
        )
        link_path = register_local_lecture(
            lecture,
            args.video_path,
            portal_ids=_portal_ids(args.targets_path, args.schedule_path),
            local_lectures_path=args.local_lectures_path,
            raw_video_dir=args.raw_video_dir,
            queue_path=args.queue_path,
            public_root=args.public_root,
        )
    except (
        ValidationError,
        LocalLectureError,
        OSError,
        ValueError,
        SubprocessError,
    ) as error:
        print(f"erro: {error}", file=sys.stderr)
        return 1
    print(f"registrada: {lecture.lecture_id} -> {link_path}")
    return 0


def _portal_ids(targets_path: Path, schedule_path: Path) -> frozenset[str]:
    """Every portal id; a missing source fails registration (no fail-open)."""

    catalog = load_academic_catalog(targets_path)
    schedule = load_academic_schedule(schedule_path)
    catalog_ids = {
        str(lesson.activity_id)
        for discipline in catalog.disciplines
        for lesson in discipline.lessons
    }
    return frozenset(catalog_ids | set(schedule))


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lecture_id", help="id sintético, apenas dígitos")
    parser.add_argument("video_path", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--discipline", required=True)
    parser.add_argument("--semester", required=True, help="AAAA.S, ex.: 2025.2")
    parser.add_argument("--section", default=DEFAULT_SECTION)
    parser.add_argument(
        "--scheduled-date",
        type=date.fromisoformat,
        help="AAAA-MM-DD; padrão: primeiro dia do semestre",
    )
    parser.add_argument(
        "--targets-path", type=Path, default=Path("config/targets.local.yaml")
    )
    parser.add_argument(
        "--schedule-path",
        type=Path,
        default=Path("config/academic-calendar.local.yaml"),
    )
    parser.add_argument("--raw-video-dir", type=Path, default=RAW_VIDEO_DIR)
    parser.add_argument("--queue-path", type=Path, default=QUEUE_PATH)
    parser.add_argument("--public-root", type=Path, default=PUBLIC_STUDY_GUIDES_ROOT)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="permite publicar no site (padrão: compilação em raiz privada)",
    )
    parser.add_argument(
        "--video-only",
        action="store_true",
        help=(
            "publica apenas o vídeo (sem guia, flashcards, simulado ou Codex); "
            "a compilação gera um pacote mínimo (ADR 0043)"
        ),
    )
    parser.add_argument("--local-lectures-path", type=Path, default=LOCAL_LECTURES_PATH)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
