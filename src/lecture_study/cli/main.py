"""Master CLI entrypoint (Phase 16): `run-all`, `run-phase`, and
`run-personal-study`, which drive
src/lecture_study/cli/orchestrator.py's Phase 2-15 pipeline for one
lecture_id. See
docs/decisions/0016-master-cli-orchestrator.md.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from lecture_study.cli.orchestrator import (
    QUEUE_PATH,
    RUN_PHASE_CHOICES,
    OrchestratorContext,
    run_all,
    run_personal_study,
    run_phase,
)

_logger = logging.getLogger(__name__)


def _add_common_pipeline_flags(subparser: argparse.ArgumentParser) -> None:
    """Shared by `run-all`, `run-phase`, and `run-personal-study`:
    --queue-path (defaults to QUEUE_PATH, the file register_local_lecture writes)
    and --whisper-model-path, the only local model the pipeline still runs
    (ADR 0038). The path defaults to None; the transcription phase raises
    PipelineExecutionError via orchestrator._require_model_path if it was
    not supplied."""
    subparser.add_argument(
        "--queue-path", type=Path, default=QUEUE_PATH, dest="queue_path"
    )
    subparser.add_argument(
        "--whisper-model-path", type=Path, default=None, dest="whisper_model_path"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """`run-all`/`run-phase` keep their existing arguments and dispatch
    unchanged (ADR 0024's D-03). `run-personal-study` is the separate,
    explicitly named ADR 0024 uncertified personal-study entry point, over
    the same optional flags plus the positional lecture_id."""
    parser = argparse.ArgumentParser(
        prog="python -m lecture_study.cli",
        description="Lecture-to-study pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        (
            "run-all",
            "Run every pipeline phase for one lecture, resuming from existing artifacts",
        ),
        ("run-phase", "Run a single pipeline phase for one lecture"),
    ):
        subparser = subparsers.add_parser(command, help=help_text)
        _add_common_pipeline_flags(subparser)
        if command == "run-phase":
            subparser.add_argument("phase_number", type=int, choices=RUN_PHASE_CHOICES)
        subparser.add_argument("lecture_id")

    personal_study_subparser = subparsers.add_parser(
        "run-personal-study",
        help=(
            "Run the uncertified personal-study execution mode (ADR 0024) for "
            "phases 8, 9, 13, 14 and 15, without a publication certificate"
        ),
    )
    _add_common_pipeline_flags(personal_study_subparser)
    personal_study_subparser.add_argument("lecture_id")

    return parser


async def _dispatch(args: argparse.Namespace) -> None:
    ctx = OrchestratorContext(
        queue_path=args.queue_path,
        whisper_model_path=args.whisper_model_path,
    )
    if args.command == "run-all":
        await run_all(ctx, args.lecture_id)
    elif args.command == "run-personal-study":
        await run_personal_study(ctx, args.lecture_id)
    else:
        await run_phase(ctx, args.phase_number, args.lecture_id)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_arg_parser().parse_args(argv)
    try:
        asyncio.run(_dispatch(args))
    except (RuntimeError, ValueError) as error:
        # Every domain exception in this codebase (LectureNotFoundError,
        # SemanticLedgersNotFoundError, MockExamGenerationError,
        # PodcastGenerationError, AudioGenerationError,
        # PipelineExecutionError, ...) is a RuntimeError or ValueError
        # subclass, as is pydantic's own ValidationError -- this is the
        # CLI's outermost boundary, so catching both bases by design turns
        # every one of them into a friendly one-line message and exit
        # code 1, while a genuine bug (KeyError, TypeError, OSError, ...)
        # still propagates as a full traceback instead of being disguised
        # as a clean failure.
        _logger.error("%s", error)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
