"""Hang-proof OCR: runs ocr_worker in a killable child process per frame.

Apple Vision can block forever inside a native call, and that stuck call
poisons the Vision queue of the whole process, so only killing the process
recovers. Each attempt therefore runs in a fresh child that is killed when
it exceeds OCR_FRAME_TIMEOUT_SECONDS; one retry is made before giving up.
"""

import json
import subprocess
import sys
from pathlib import Path

from lecture_study.extraction.ocr_worker import OCR_FAILURE_EXIT_CODE, OcrError
from lecture_study.ops.process_environment import sanitized_child_process_environment
from lecture_study.ops.process_lifecycle import run_bounded_process

OCR_FRAME_TIMEOUT_SECONDS = 120.0
OCR_MAX_ATTEMPTS = 2
_MAXIMUM_STDOUT_BYTES = 4 * 1024 * 1024
_MAXIMUM_STDERR_BYTES = 256 * 1024


def extract_text_supervised(
    image_path: Path, timeout_seconds: float = OCR_FRAME_TIMEOUT_SECONDS
) -> str:
    """OCR image_path in a child process, retrying once if the child hangs."""
    for _ in range(OCR_MAX_ATTEMPTS):
        try:
            return _run_ocr_child(image_path, timeout_seconds)
        except subprocess.TimeoutExpired:
            continue
    raise OcrError(f"Vision OCR hung on {image_path}")


def _run_ocr_child(image_path: Path, timeout_seconds: float) -> str:
    completed = run_bounded_process(
        [
            # Own interpreter, deliberately unresolved: resolving the venv
            # symlink would drop the venv's site-packages.
            sys.executable,
            "-m",
            "lecture_study.extraction.ocr_worker",
            str(image_path),
        ],
        env=sanitized_child_process_environment(),
        timeout_seconds=timeout_seconds,
        maximum_stdout_bytes=_MAXIMUM_STDOUT_BYTES,
        maximum_stderr_bytes=_MAXIMUM_STDERR_BYTES,
    )
    if completed.returncode == OCR_FAILURE_EXIT_CODE:
        raise OcrError(completed.stderr.strip())
    if completed.returncode != 0:
        raise OcrError(
            f"OCR child exited {completed.returncode} on {image_path}: "
            f"{completed.stderr.strip()[-500:]}"
        )
    return str(json.loads(completed.stdout)["text"])
