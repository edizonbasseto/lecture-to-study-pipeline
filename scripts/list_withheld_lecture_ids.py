#!/usr/bin/env python3
"""Print rsync exclude patterns for local lectures that are not published.

Intended as an rsync `--exclude-from` source for a site deploy, so no local
lecture reaches the server through the mock-exam or study-guide channels
unless it carries `publish: true`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lecture_study.study_materials.local_lectures import (
    LOCAL_LECTURES_PATH,
    load_local_lectures,
    withheld_exam_lecture_ids,
    withheld_lecture_ids,
)

_PATTERN_BY_CHANNEL = {
    "exams": "/{lecture_id}/",
    "guides": "{lecture_id}-*/",
}
_WITHHELD_BY_CHANNEL = {
    "exams": withheld_exam_lecture_ids,
    "guides": withheld_lecture_ids,
}


def main() -> None:
    args = _argument_parser().parse_args()
    pattern = _PATTERN_BY_CHANNEL[args.channel]
    withheld = _WITHHELD_BY_CHANNEL[args.channel](
        load_local_lectures(args.local_lectures_path)
    )
    for lecture_id in withheld:
        print(pattern.format(lecture_id=lecture_id))


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", choices=sorted(_PATTERN_BY_CHANNEL))
    parser.add_argument("--local-lectures-path", type=Path, default=LOCAL_LECTURES_PATH)
    return parser


if __name__ == "__main__":
    main()
