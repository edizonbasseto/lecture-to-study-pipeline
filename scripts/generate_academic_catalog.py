#!/usr/bin/env python3
"""Generate the local study-guide index grouped by academic semester."""

from __future__ import annotations

import argparse
from pathlib import Path

from lecture_study.study_materials.academic_catalog import (
    load_academic_catalog,
    load_academic_schedule,
    render_academic_catalog,
)
from lecture_study.study_materials.local_lectures import (
    LOCAL_LECTURES_PATH,
    load_local_lectures,
    merge_local_lectures,
)


def main() -> None:
    args = _argument_parser().parse_args()
    catalog = load_academic_catalog(args.targets_path)
    schedule = load_academic_schedule(args.schedule_path)
    catalog, schedule = merge_local_lectures(
        catalog,
        schedule,
        [
            item
            for item in load_local_lectures(args.local_lectures_path)
            if item.publish
        ],
    )
    rendered = render_academic_catalog(
        catalog,
        schedule,
        args.output_path.parent,
        include_unscheduled=True,
    )
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(rendered, encoding="utf-8")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera o catálogo local de videoaulas por semestre."
    )
    parser.add_argument(
        "--targets-path",
        type=Path,
        default=Path("config/targets.local.yaml"),
    )
    parser.add_argument(
        "--local-lectures-path",
        type=Path,
        default=LOCAL_LECTURES_PATH,
    )
    parser.add_argument(
        "--schedule-path",
        type=Path,
        default=Path("config/academic-calendar.local.yaml"),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("workspace/study-guides/catalogo.md"),
    )
    return parser


if __name__ == "__main__":
    main()
