#!/usr/bin/env python3
"""Harvest and report the measurements of the demo run (ADR 0047, DEMO-03).

Subcommand `harvest` copies one adapter's run artifacts into
`workspace/demo/runs/<adapter>/` before anything can overwrite them, optionally
re-transcribes the lecture audio without the vocabulary prompt (the ASR
ablation), and prints a one-adapter preview of the results report.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from lecture_study.demo.asr_measure import AsrReference
from lecture_study.demo.harvest import (
    HARVEST_ROOT,
    HarvestError,
    HarvestSources,
    harvest_run,
    load_adapter_results,
    segments_json,
)
from lecture_study.demo.media_build import (
    BUILD_SUMMARY_PATH,
    DEMO_SCRIPT_PATH,
    BuildSummary,
)
from lecture_study.demo.report import DemoResults, render_results_markdown
from lecture_study.demo.script import DemoScript, load_demo_script
from lecture_study.demo.wer import normalized_words
from lecture_study.llm.gateway import AdapterName
from lecture_study.transcription.whisper_worker import (
    WhisperTranscriptionError,
    run_transcription,
)

ABLATION_DIRECTORY = "ablation"
PREVIEW_NAME = "preview.md"
_ABLATION_AUDIO_NAME = "audio.wav"
_ABLATION_SEGMENTS_NAME = "segments.json"
_ABLATION_UNAVAILABLE_NAME = "unavailable.txt"
_WITHOUT_VOCABULARY_PROMPT = ""


def record_asr_ablation(audio: Path, model_path: Path, harvest_directory: Path) -> Path:
    """Transcribe `audio` without the vocabulary prompt; return the file written.

    Whisper writes its JSON next to the input, so the audio is copied into the
    harvest first. A failure is recorded as `unavailable.txt` instead of
    stopping the harvest, and the baseline pipeline is left untouched.
    """

    ablation_directory = harvest_directory / ABLATION_DIRECTORY
    ablation_directory.mkdir()
    audio_copy = ablation_directory / _ABLATION_AUDIO_NAME
    shutil.copy2(audio, audio_copy)
    try:
        segments = asyncio.run(
            run_transcription(
                audio_copy, model_path, initial_prompt=_WITHOUT_VOCABULARY_PROMPT
            )
        )
    except (WhisperTranscriptionError, OSError) as error:
        unavailable = ablation_directory / _ABLATION_UNAVAILABLE_NAME
        unavailable.write_text(f"{error}\n")
        return unavailable
    written = ablation_directory / _ABLATION_SEGMENTS_NAME
    written.write_text(segments_json(segments) + "\n")
    return written


def preview_markdown(
    harvest_directory: Path, script: DemoScript, build_summary: BuildSummary
) -> str:
    reference = AsrReference(
        text=script.reference_text(), planted_terms=script.planted_terms
    )
    results = DemoResults(
        measured_on=date.today(),
        video_seconds=build_summary.video_seconds,
        reference_words=len(normalized_words(reference.text)),
        adapters=(load_adapter_results(harvest_directory, reference),),
        adapters_not_run=(),
    )
    return render_results_markdown(results)


def run_harvest(arguments: argparse.Namespace) -> int:
    destination = HARVEST_ROOT / arguments.adapter
    sources = HarvestSources.for_lecture(arguments.lecture_id)
    try:
        harvest_run(sources, AdapterName(arguments.adapter), destination)
    except HarvestError as error:
        print(f"harvest refused: {error}", file=sys.stderr)
        return 1
    print(f"harvested: {destination}")
    if arguments.asr_ablation:
        written = record_asr_ablation(
            sources.audio, arguments.whisper_model_path, destination
        )
        print(f"asr ablation: {written}")
    preview = preview_markdown(
        destination,
        load_demo_script(DEMO_SCRIPT_PATH),
        BuildSummary.model_validate_json(BUILD_SUMMARY_PATH.read_text()),
    )
    (destination / PREVIEW_NAME).write_text(preview)
    print(preview)
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    harvest = subcommands.add_parser("harvest", help="preserve one adapter's run")
    harvest.add_argument(
        "--adapter", choices=[member.value for member in AdapterName], required=True
    )
    harvest.add_argument("--lecture-id", required=True)
    harvest.add_argument("--asr-ablation", action="store_true")
    harvest.add_argument("--whisper-model-path", type=Path)
    harvest.set_defaults(run=run_harvest)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.asr_ablation and arguments.whisper_model_path is None:
        parser.error("--asr-ablation requires --whisper-model-path")
    return int(arguments.run(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
