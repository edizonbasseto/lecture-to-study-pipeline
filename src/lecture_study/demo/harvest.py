"""Preserve one adapter's demo run and read its measurements back (ADR 0047).

A later step of the demo (the critic rerun of the blocked-lecture recovery,
or a second run of the same adapter) overwrites run records and units, so the
measured artifacts are copied first into ``workspace/demo/runs/<adapter>/``
with their timestamps preserved. This module is the filesystem adapter of the
measurement logic: ``load_adapter_results`` reads a harvest directory and
hands parsed data to the pure functions of ``asr_measure``, ``run_metrics``
and ``phase_timing``. Nothing here is ever moved or deleted except the
module's own half-built staging directory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from lecture_study.demo.asr_measure import (
    AsrMeasurement,
    AsrReference,
    measure_pipeline_transcript,
    measure_unprompted_transcript,
    parse_terminology_corrections,
)
from lecture_study.demo.lecture_id import parse_lecture_id
from lecture_study.demo.phase_timing import PhaseTiming, parse_phase_log
from lecture_study.demo.report import AdapterResults, NotRunAdapter
from lecture_study.demo.run_metrics import (
    CriticMetrics,
    StageMetrics,
    critic_metrics,
    parse_run_record,
    stage_metrics,
)
from lecture_study.domain.models import TranscriptionSegment, ValidatedLearningUnit
from lecture_study.llm.gateway import AdapterName, Stage

__all__ = [
    "DEMO_LOGS_ROOT",
    "HARVEST_ROOT",
    "NOT_RUN_MARKER",
    "HarvestError",
    "HarvestManifest",
    "HarvestSources",
    "HarvestedFile",
    "harvest_run",
    "load_adapter_results",
    "load_not_run",
    "segments_json",
]

HARVEST_ROOT = Path("workspace/demo/runs")
DEMO_LOGS_ROOT = Path("workspace/demo/logs")
NOT_RUN_MARKER = "NOT_RUN.txt"

_DATA_ROOT = Path("workspace/data")
_MANIFEST_NAME = "harvest.json"
_TRANSCRIPTION_NAME = "transcription.json"
_CORRECTIONS_NAME = "terminology_corrections.json"
_ABLATION_SEGMENTS = Path("ablation") / "segments.json"
_PIPELINE_LOGS = ("run-all.log", "personal-study.log")
_SUPERSEDED_SUFFIX = ".superseded.json"
_CRITIC_RECORD_NAME = f"{Stage.CRITIC.value}.json"
_HASH_CHUNK_BYTES = 1024 * 1024
_STAGING_SUFFIX = ".partial"
_OPERATOR_NOTES_NAME = "notes.txt"


class HarvestError(RuntimeError):
    """A run cannot be harvested, or a harvest directory cannot be read."""


@dataclass(frozen=True, slots=True)
class HarvestSources:
    """Where one lecture's measured artifacts live in the workspace."""

    lecture_id: str
    llm_runs: Path
    transcription: Path
    terminology_corrections: Path
    units: Path
    evidence_packs: Path
    audio: Path
    logs: Path

    @classmethod
    def for_lecture(
        cls,
        lecture_id: str,
        *,
        data_root: Path = _DATA_ROOT,
        logs_root: Path = DEMO_LOGS_ROOT,
    ) -> HarvestSources:
        lecture_id = parse_lecture_id(lecture_id)
        return cls(
            lecture_id=lecture_id,
            llm_runs=data_root / "llm_runs" / lecture_id,
            transcription=data_root
            / "05_transcribed"
            / f"{lecture_id}_transcription.json",
            terminology_corrections=(
                data_root
                / "05_transcribed"
                / f"{lecture_id}_terminology_corrections.json"
            ),
            units=data_root / "09_validated_units" / lecture_id,
            evidence_packs=data_root / "06_evidence_packs" / lecture_id,
            audio=data_root / "02_extracted_audio" / lecture_id / f"{lecture_id}.wav",
            logs=logs_root / lecture_id,
        )


class HarvestedFile(BaseModel):
    """One copied file, identified by its harvest-relative path and digest."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    path: str
    sha256: str
    size_bytes: int = Field(ge=0)


class HarvestManifest(BaseModel):
    """What a harvest holds: identity, time and a digest per copied file."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    lecture_id: str
    adapter: AdapterName
    harvested_at: datetime
    evidence_packs: int = Field(ge=0)
    files: tuple[HarvestedFile, ...]


class _TranscriptDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    segments: list[TranscriptionSegment]


_SEGMENTS_ADAPTER = TypeAdapter(list[TranscriptionSegment])


def harvest_run(
    sources: HarvestSources, adapter: AdapterName, destination: Path
) -> HarvestManifest:
    """Copy the run's artifacts to `destination`; refuse an existing one."""

    if destination.exists():
        raise HarvestError(f"harvest destination already exists: {destination}")
    _require_sources(sources)
    staging = destination.with_name(destination.name + _STAGING_SUFFIX)
    if staging.exists():
        raise HarvestError(f"a half-built harvest is in the way: {staging}")
    try:
        manifest = _copy_into(sources, adapter, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    staging.rename(destination)
    return manifest


def load_adapter_results(harvest_dir: Path, reference: AsrReference) -> AdapterResults:
    """Build the report model of one adapter from its harvest directory."""

    manifest = _read_manifest(harvest_dir)
    return AdapterResults(
        adapter=manifest.adapter,
        lecture_id=manifest.lecture_id,
        asr=_asr_measurements(harvest_dir, reference),
        phases=_merged_phases(harvest_dir, manifest.lecture_id),
        llm_stages=_stage_metrics(harvest_dir),
        critic=_critic_metrics(harvest_dir),
        evidence_packs=manifest.evidence_packs,
        notes=_notes(harvest_dir),
    )


def load_not_run(harvest_dir: Path, adapter: AdapterName) -> NotRunAdapter | None:
    """The reason an adapter was not run, when its directory says so."""

    marker = harvest_dir / NOT_RUN_MARKER
    if not marker.is_file():
        return None
    reason = marker.read_text().strip()
    if not reason:
        raise HarvestError(f"{NOT_RUN_MARKER} states no reason")
    return NotRunAdapter(adapter=adapter, reason=reason)


def _require_sources(sources: HarvestSources) -> None:
    required_files = (
        sources.transcription,
        sources.terminology_corrections,
        *(sources.logs / name for name in _PIPELINE_LOGS),
    )
    required_directories = (sources.llm_runs, sources.units, sources.evidence_packs)
    missing = [str(path) for path in required_files if not path.is_file()]
    missing += [str(path) for path in required_directories if not path.is_dir()]
    if missing:
        raise HarvestError("nothing to harvest, missing: " + ", ".join(missing))


def _copy_into(
    sources: HarvestSources, adapter: AdapterName, staging: Path
) -> HarvestManifest:
    staging.mkdir(parents=True)
    _copy_tree(sources.llm_runs, staging / "llm_runs")
    _copy_tree(sources.units, staging / "units")
    _copy_tree(sources.logs, staging / "logs")
    shutil.copy2(sources.transcription, staging / _TRANSCRIPTION_NAME)
    shutil.copy2(sources.terminology_corrections, staging / _CORRECTIONS_NAME)
    manifest = HarvestManifest(
        lecture_id=sources.lecture_id,
        adapter=adapter,
        harvested_at=datetime.now(UTC),
        evidence_packs=sum(1 for _ in sources.evidence_packs.glob("pack_*.json")),
        files=_harvested_files(staging),
    )
    (staging / _MANIFEST_NAME).write_text(manifest.model_dump_json(indent=2) + "\n")
    return manifest


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def _harvested_files(root: Path) -> tuple[HarvestedFile, ...]:
    return tuple(
        HarvestedFile(
            path=path.relative_to(root).as_posix(),
            sha256=_sha256_of(path),
            size_bytes=path.stat().st_size,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(harvest_dir: Path) -> HarvestManifest:
    manifest_path = harvest_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise HarvestError(f"{harvest_dir} holds no {_MANIFEST_NAME}")
    return HarvestManifest.model_validate_json(manifest_path.read_text(), strict=False)


def _asr_measurements(
    harvest_dir: Path, reference: AsrReference
) -> tuple[AsrMeasurement, ...]:
    document = _TranscriptDocument.model_validate_json(
        (harvest_dir / _TRANSCRIPTION_NAME).read_text()
    )
    corrections = parse_terminology_corrections(
        (harvest_dir / _CORRECTIONS_NAME).read_text()
    )
    measurements = [
        measure_pipeline_transcript(reference, document.segments, corrections)
    ]
    ablation_path = harvest_dir / _ABLATION_SEGMENTS
    if ablation_path.is_file():
        raw_segments = _SEGMENTS_ADAPTER.validate_json(ablation_path.read_text())
        measurements.append(measure_unprompted_transcript(reference, raw_segments))
    return tuple(measurements)


def _merged_phases(harvest_dir: Path, lecture_id: str) -> tuple[PhaseTiming, ...]:
    merged: dict[int, PhaseTiming] = {}
    for log_name in _PIPELINE_LOGS:
        log_text = (harvest_dir / "logs" / log_name).read_text()
        for timing in parse_phase_log(log_text, lecture_id):
            merged[timing.number] = _preferred_timing(merged.get(timing.number), timing)
    return tuple(merged[number] for number in sorted(merged))


def _preferred_timing(earlier: PhaseTiming | None, later: PhaseTiming) -> PhaseTiming:
    """A phase that ran keeps its wall time over a later 'already done' skip."""
    if earlier is not None and earlier.status == "ran" and later.status == "skipped":
        return earlier
    return later


def _stage_metrics(harvest_dir: Path) -> tuple[StageMetrics, ...]:
    return tuple(
        stage_metrics(
            parse_run_record(path.read_text()),
            superseded=path.name.endswith(_SUPERSEDED_SUFFIX),
        )
        for path in sorted((harvest_dir / "llm_runs").glob("*.json"))
    )


def _critic_metrics(harvest_dir: Path) -> CriticMetrics | None:
    record_path = harvest_dir / "llm_runs" / _CRITIC_RECORD_NAME
    if not record_path.is_file():
        return None
    return critic_metrics(
        parse_run_record(record_path.read_text()), _unit_review_flags(harvest_dir)
    )


def _unit_review_flags(harvest_dir: Path) -> list[bool]:
    return [
        ValidatedLearningUnit.model_validate_json(
            path.read_text(), strict=False
        ).review.passed
        for path in sorted((harvest_dir / "units").glob("unit_*.json"))
    ]


def _notes(harvest_dir: Path) -> tuple[str, ...]:
    """Operator notes of the logs directory, one per line, plus the ablation status."""
    notes = _operator_notes(harvest_dir / "logs" / _OPERATOR_NOTES_NAME)
    if (harvest_dir / "ablation" / "unavailable.txt").is_file():
        notes += ("the ASR ablation without the vocabulary prompt was not available",)
    return notes


def _operator_notes(notes_path: Path) -> tuple[str, ...]:
    if not notes_path.is_file():
        return ()
    lines = (line.strip() for line in notes_path.read_text().splitlines())
    return tuple(line for line in lines if line)


def segments_json(segments: list[TranscriptionSegment]) -> str:
    """Serialize ASR segments for the ablation file."""
    return json.dumps(
        [segment.model_dump() for segment in segments], ensure_ascii=False, indent=2
    )
