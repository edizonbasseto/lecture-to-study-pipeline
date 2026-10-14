"""Traceable figure-caption catalog storage.

Parses the timestamp encoded in each extracted frame's own filename,
discovers one lecture's frames in timestamp order, and persists a
LectureFigureCatalog atomically under a rooted, traversal-safe path keyed
by a validated lecture identifier -- the same temporary-file-then-replace
persistence pattern already used by assembly/assembler.py and
metadata/aggregator.py.
"""

import json
import re
from pathlib import Path

from pydantic import TypeAdapter

from lecture_study.domain.models import LectureFigureCatalog, SafeIdentifier

# Phase 2's numbered stage directories run 01 through 16 (see
# extraction/pipeline.py through quality/delivery.py); 17 is the first
# unclaimed number.
EXTRACTED_SLIDES_INPUT_DIR = Path("workspace/data/03_extracted_slides")
FIGURE_CAPTIONS_OUTPUT_DIR = Path("workspace/data/17_figure_captions")

_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)

# Mirrors extraction/media_workers.py's own `f"frame_{timestamp:08.3f}.png"`
# naming convention exactly: an integer second count of at least one digit,
# a decimal point, and exactly three decimal digits. The timestamp parser
# below must agree with this writer, or a stray file dropped into the frame
# directory could become a figure with a fabricated timestamp.
_FRAME_FILENAME_PATTERN = re.compile(r"^frame_(?P<timestamp>\d+\.\d{3})\.png$")


class InvalidFrameFilenameError(ValueError):
    """Raised when a filename does not match the extraction frame naming convention."""


def frame_timestamp_seconds(frame_path: Path) -> float:
    """Parse the second a frame was extracted at from its own filename.

    Raises InvalidFrameFilenameError for any name that does not match
    extraction/media_workers.py's naming convention, so a caller can never
    derive a timestamp from a file that was not actually produced by that
    stage.
    """
    match = _FRAME_FILENAME_PATTERN.match(frame_path.name)
    if match is None:
        raise InvalidFrameFilenameError(
            f"{frame_path.name!r} does not match the extraction frame naming "
            "convention (frame_<seconds>.<millis>.png)"
        )
    return float(match.group("timestamp"))


def discover_lecture_frames(
    lecture_id: str,
    frames_root: Path = EXTRACTED_SLIDES_INPUT_DIR,
) -> list[Path]:
    """List one lecture's extracted frames, sorted by timestamp.

    Returns an empty list -- rather than raising -- when the lecture has no
    frames directory, matching assembly/assembler.py's own
    empty-is-valid-not-an-error stance for a stage that has not yet run.
    """
    validated_lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    lecture_dir = frames_root / validated_lecture_id
    if not lecture_dir.is_dir():
        return []
    frame_paths = [
        frame_path
        for frame_path in lecture_dir.iterdir()
        if _FRAME_FILENAME_PATTERN.match(frame_path.name)
    ]
    return sorted(frame_paths, key=frame_timestamp_seconds)


def write_figure_catalog(
    catalog: LectureFigureCatalog,
    output_root: Path = FIGURE_CAPTIONS_OUTPUT_DIR,
) -> None:
    """Persist a lecture's figure catalog through a temporary file and an
    atomic replace, rejecting an unsafe lecture identifier before any path
    is composed."""
    validated_lecture_id = _LECTURE_ID_ADAPTER.validate_python(catalog.lecture_id)
    lecture_dir = output_root / validated_lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    output_path = lecture_dir / "figures.json"
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(catalog.model_dump(mode="json"), indent=2, ensure_ascii=False)
    )
    tmp_path.replace(output_path)


def read_figure_catalog(
    lecture_id: str,
    output_root: Path = FIGURE_CAPTIONS_OUTPUT_DIR,
) -> LectureFigureCatalog:
    """Load and validate a previously persisted figure catalog."""
    validated_lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    catalog_path = output_root / validated_lecture_id / "figures.json"
    return LectureFigureCatalog.model_validate_json(catalog_path.read_text())
