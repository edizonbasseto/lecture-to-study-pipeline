#!/usr/bin/env python3
"""Harvest and report the measurements of the demo run (ADR 0047, DEMO-03).

Subcommand `report` renders the committed results report from every harvested
adapter (or its not-run marker) and the recovery evidence.

Subcommand `harvest` copies one adapter's run artifacts into
`workspace/demo/runs/<adapter>/` before anything can overwrite them, optionally
re-transcribes the lecture audio without the vocabulary prompt (the ASR
ablation), and prints a one-adapter preview of the results report.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import cast

from lecture_study.demo.asr_measure import AsrReference
from lecture_study.demo.harvest import (
    HARVEST_ROOT,
    HarvestError,
    HarvestManifest,
    HarvestSources,
    harvest_run,
    load_adapter_results,
    load_not_run,
    segments_json,
)
from lecture_study.demo.lecture_id import parse_lecture_id
from lecture_study.demo.media_build import (
    BUILD_SUMMARY_PATH,
    DEMO_SCRIPT_PATH,
    BuildSummary,
)
from lecture_study.demo.recovery import RecoveryEvidence
from lecture_study.demo.report import (
    AdapterResults,
    DemoResults,
    NotRunAdapter,
    render_results_json,
    render_results_markdown,
)
from lecture_study.demo.script import DemoScript, load_demo_script
from lecture_study.demo.site_access import require_inside
from lecture_study.demo.wer import normalized_words
from lecture_study.llm.gateway import AdapterName
from lecture_study.transcription.whisper_worker import (
    WhisperTranscriptionError,
    run_transcription,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ABLATION_DIRECTORY = "ablation"
PREVIEW_NAME = "preview.md"
_ABLATION_AUDIO_NAME = "audio.wav"
_ABLATION_SEGMENTS_NAME = "segments.json"
_ABLATION_UNAVAILABLE_NAME = "unavailable.txt"
_WITHOUT_VOCABULARY_PROMPT = ""
DEFAULT_REPORT_DIRECTORY = Path("docs/results")
RECOVERY_EVIDENCE_PATH = Path("workspace/demo/recovery/evidence.json")
RECOVERY_NOTES_PATH = Path("workspace/demo/recovery/notes.txt")
_HARVEST_MANIFEST_NAME = "harvest.json"
_MARKDOWN_NAME = "demo-results.md"
_JSON_NAME = "demo-results.json"
_NO_HARVEST_REASON = "no harvest found"


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
    reference = _reference(script)
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


def _reference(script: DemoScript) -> AsrReference:
    return AsrReference(
        text=script.reference_text(), planted_terms=script.planted_terms
    )


def _harvest_or_reason(
    adapter: AdapterName, reference: AsrReference
) -> AdapterResults | NotRunAdapter:
    harvest_directory = HARVEST_ROOT / adapter.value
    not_run = load_not_run(harvest_directory, adapter)
    if not_run is not None:
        return not_run
    if not (harvest_directory / _HARVEST_MANIFEST_NAME).is_file():
        return NotRunAdapter(adapter=adapter, reason=_NO_HARVEST_REASON)
    return load_adapter_results(harvest_directory, reference)


def _latest_harvest_date(run: tuple[AdapterResults, ...]) -> date:
    harvested = [
        HarvestManifest.model_validate_json(
            (HARVEST_ROOT / item.adapter.value / _HARVEST_MANIFEST_NAME).read_text()
        ).harvested_at
        for item in run
    ]
    return max(harvested).date()


def _recovery_evidence() -> RecoveryEvidence | None:
    if not RECOVERY_EVIDENCE_PATH.is_file():
        return None
    return RecoveryEvidence.model_validate_json(RECOVERY_EVIDENCE_PATH.read_text())


def _recovery_notes() -> tuple[str, ...]:
    if not RECOVERY_NOTES_PATH.is_file():
        return ()
    lines = (line.strip() for line in RECOVERY_NOTES_PATH.read_text().splitlines())
    return tuple(line for line in lines if line)


def collect_results() -> DemoResults:
    script = load_demo_script(DEMO_SCRIPT_PATH)
    reference = _reference(script)
    outcomes = [_harvest_or_reason(adapter, reference) for adapter in AdapterName]
    run = tuple(item for item in outcomes if isinstance(item, AdapterResults))
    not_run = tuple(item for item in outcomes if isinstance(item, NotRunAdapter))
    if not run:
        raise HarvestError("no adapter has a harvest; run `make demo-harvest` first")
    build_summary = BuildSummary.model_validate_json(BUILD_SUMMARY_PATH.read_text())
    return DemoResults(
        measured_on=_latest_harvest_date(run),
        video_seconds=build_summary.video_seconds,
        reference_words=len(normalized_words(reference.text)),
        adapters=run,
        adapters_not_run=not_run,
        recovery=_recovery_evidence(),
        recovery_notes=_recovery_notes(),
    )


def run_report(arguments: argparse.Namespace) -> int:
    try:
        results = collect_results()
    except HarvestError as error:
        print(f"report refused: {error}", file=sys.stderr)
        return 1
    try:
        output_directory = require_inside(
            cast(Path, arguments.output_dir), REPOSITORY_ROOT
        )
    except ValueError as error:
        print(f"report refused: {error}", file=sys.stderr)
        return 1
    output_directory.mkdir(parents=True, exist_ok=True)
    markdown_path = output_directory / _MARKDOWN_NAME
    json_path = output_directory / _JSON_NAME
    markdown_path.write_text(render_results_markdown(results))
    json_path.write_text(render_results_json(results) + "\n")
    print(f"report: {markdown_path}")
    print(f"report: {json_path}")
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    harvest = subcommands.add_parser("harvest", help="preserve one adapter's run")
    harvest.add_argument(
        "--adapter", choices=[member.value for member in AdapterName], required=True
    )
    harvest.add_argument("--lecture-id", type=parse_lecture_id, required=True)
    harvest.add_argument("--asr-ablation", action="store_true")
    harvest.add_argument("--whisper-model-path", type=Path)
    harvest.set_defaults(run=run_harvest)
    report = subcommands.add_parser("report", help="render the committed report")
    report.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    report.set_defaults(run=run_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.chdir(REPOSITORY_ROOT)
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    if (
        arguments.command == "harvest"
        and arguments.asr_ablation
        and arguments.whisper_model_path is None
    ):
        parser.error("--asr-ablation requires --whisper-model-path")
    return int(arguments.run(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
