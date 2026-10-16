"""Harvest of a demo run: copy fidelity, refusal to overwrite and result loading."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lecture_study.demo.asr_measure import AsrReference
from lecture_study.demo.harvest import (
    NOT_RUN_MARKER,
    HarvestError,
    HarvestManifest,
    HarvestSources,
    harvest_run,
    load_adapter_results,
    load_not_run,
    segments_json,
)
from lecture_study.domain.models import (
    CriticReview,
    LearningUnitDraft,
    TranscriptionSegment,
    ValidatedLearningUnit,
)
from lecture_study.llm.gateway import AdapterName, Stage
from lecture_study.llm.run_record import GatewayUsage, LlmRunRecord

LECTURE_ID = "925100"
ORCHESTRATOR = "lecture_study.cli.orchestrator"
STARTED_AT = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
FIXED_MTIME_NS = 1_700_000_000_123_456_789
REFERENCE = AsrReference(
    text="O plano fascial é a barreira profunda.", planted_terms=("fascial",)
)


def usage(stage: Stage, *, calls: int, retry_calls: int = 0) -> GatewayUsage:
    return GatewayUsage(
        stage=stage,
        adapter_name=AdapterName.LLAMA_CPP,
        model_identifier="qwen-test",
        calls=calls,
        retry_calls=retry_calls,
        call_seconds=2.0,
    )


def run_record(stage: Stage, *gateways: GatewayUsage) -> LlmRunRecord:
    return LlmRunRecord(
        lecture_id=LECTURE_ID,
        script_stage=stage,
        schema_name="test-schema",
        status="succeeded",
        started_at=STARTED_AT,
        finished_at=STARTED_AT,
        wall_seconds=5.0,
        gateways=gateways,
    )


def validated_unit(sequence: int, *, passed: bool) -> ValidatedLearningUnit:
    return ValidatedLearningUnit(
        lecture_id=LECTURE_ID,
        pack_sequence=sequence,
        content=LearningUnitDraft(
            lecture_id=LECTURE_ID,
            pack_sequence=sequence,
            title="Unidade",
            body_markdown="Corpo.",
            clinical_warnings=[],
        ),
        review=CriticReview(passed=passed, hallucination_score=0.1, feedback="ok"),
    )


def log_text(*lines: tuple[str, str]) -> str:
    return "\n".join(
        f"2026-10-15 {clock} INFO {ORCHESTRATOR}: {message}" for clock, message in lines
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))


@pytest.fixture
def sources(tmp_path: Path) -> HarvestSources:
    data = tmp_path / "data"
    logs = tmp_path / "logs" / LECTURE_ID
    built = HarvestSources.for_lecture(
        LECTURE_ID, data_root=data, logs_root=logs.parent
    )
    records = {
        "semantic.json": run_record(
            Stage.SEMANTIC, usage(Stage.SEMANTIC, calls=3, retry_calls=1)
        ),
        "critic.json": run_record(
            Stage.CRITIC,
            usage(Stage.CRITIC, calls=4, retry_calls=0),
            usage(Stage.DRAFTING, calls=1),
        ),
        "flashcards.superseded.json": run_record(
            Stage.FLASHCARDS, usage(Stage.FLASHCARDS, calls=2)
        ),
    }
    for name, record in records.items():
        write(built.llm_runs / name, record.model_dump_json())
    segments = [
        TranscriptionSegment(
            start_time=0.0, end_time=4.0, text="O plano fascial é a barreira profunda."
        )
    ]
    write(
        built.transcription,
        json.dumps(
            {
                "lecture_id": LECTURE_ID,
                "frames": [],
                "segments": [segment.model_dump() for segment in segments],
            }
        ),
    )
    write(
        built.terminology_corrections,
        json.dumps(
            [
                {
                    "start_time": 0.0,
                    "original_text": "O plano facial é a barreira profunda.",
                    "corrected_text": "O plano fascial é a barreira profunda.",
                    "term": "fascial",
                }
            ]
        ),
    )
    for sequence, passed in ((1, True), (2, False), (3, True), (4, True)):
        write(
            built.units / f"unit_{sequence:03d}.json",
            validated_unit(sequence, passed=passed).model_dump_json(),
        )
    write(built.evidence_packs / "pack_001.json", "{}")
    write(
        built.logs / "run-all.log",
        log_text(
            ("10:00:00,000", f"phase 8 (assemble) running for {LECTURE_ID}"),
            ("10:00:04,500", f"phase 8 (assemble) done for {LECTURE_ID}"),
            ("10:00:05,000", f"phase 9 (aggregate) running for {LECTURE_ID}"),
        ),
    )
    write(
        built.logs / "personal-study.log",
        log_text(
            (
                "11:00:00,000",
                f"phase 8 (assemble) already done for {LECTURE_ID}, skipping",
            ),
            ("11:00:01,000", f"phase 9 (aggregate) running for {LECTURE_ID}"),
            ("11:00:03,000", f"phase 9 (aggregate) done for {LECTURE_ID}"),
        ),
    )
    return built


def harvest(sources: HarvestSources, tmp_path: Path) -> tuple[Path, HarvestManifest]:
    destination = tmp_path / "runs" / "llama-cpp"
    manifest = harvest_run(sources, AdapterName.LLAMA_CPP, destination)
    return destination, manifest


def test_harvest_copies_every_artifact_including_superseded_records(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)

    assert (destination / "llm_runs" / "flashcards.superseded.json").is_file()
    assert (destination / "llm_runs" / "critic.json").is_file()
    assert (destination / "transcription.json").is_file()
    assert (destination / "terminology_corrections.json").is_file()
    assert len(list((destination / "units").glob("unit_*.json"))) == 4
    assert (destination / "logs" / "run-all.log").is_file()
    assert (destination / "harvest.json").is_file()
    assert not (tmp_path / "runs" / "llama-cpp.partial").exists()


def test_harvest_preserves_modification_times_and_leaves_sources_in_place(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)

    copied = destination / "llm_runs" / "critic.json"
    assert copied.stat().st_mtime_ns == FIXED_MTIME_NS
    assert (destination / "transcription.json").stat().st_mtime_ns == FIXED_MTIME_NS
    assert (sources.llm_runs / "critic.json").is_file()


def test_manifest_records_identity_pack_count_and_a_digest_per_file(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, manifest = harvest(sources, tmp_path)

    assert manifest.lecture_id == LECTURE_ID
    assert manifest.adapter is AdapterName.LLAMA_CPP
    assert manifest.evidence_packs == 1
    by_path = {item.path: item for item in manifest.files}
    transcription = (destination / "transcription.json").read_bytes()
    assert (
        by_path["transcription.json"].sha256
        == hashlib.sha256(transcription).hexdigest()
    )
    assert "harvest.json" not in by_path
    assert manifest.harvested_at.tzinfo is not None


def test_harvest_refuses_an_existing_destination(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)
    marker = destination / "llm_runs" / "critic.json"
    before = marker.read_bytes()

    with pytest.raises(HarvestError, match="already exists"):
        harvest_run(sources, AdapterName.LLAMA_CPP, destination)

    assert marker.read_bytes() == before


def test_harvest_names_missing_sources_and_creates_nothing(
    sources: HarvestSources, tmp_path: Path
) -> None:
    (sources.llm_runs / "critic.json").unlink()
    sources.terminology_corrections.unlink()
    destination = tmp_path / "runs" / "llama-cpp"

    with pytest.raises(HarvestError, match="terminology_corrections"):
        harvest_run(sources, AdapterName.LLAMA_CPP, destination)

    assert not destination.exists()
    assert not destination.with_name("llama-cpp.partial").exists()


def test_results_carry_the_pipeline_asr_measurement(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)

    results = load_adapter_results(destination, REFERENCE)

    assert results.adapter is AdapterName.LLAMA_CPP
    assert results.lecture_id == LECTURE_ID
    assert [item.condition for item in results.asr] == ["pipeline_prompt_on"]
    pipeline = results.asr[0]
    assert pipeline.before.errors() == 1
    assert pipeline.after.errors() == 0
    assert results.evidence_packs == 1


def test_results_add_the_ablation_measurement_when_its_segments_exist(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)
    ablation = [
        TranscriptionSegment(
            start_time=0.0, end_time=4.0, text="O plano facial é a barreira profunda."
        )
    ]
    write(destination / "ablation" / "segments.json", segments_json(ablation))

    results = load_adapter_results(destination, REFERENCE)

    assert [item.condition for item in results.asr] == [
        "pipeline_prompt_on",
        "ablation_prompt_off",
    ]
    assert results.notes == ()


def test_results_note_an_unavailable_ablation(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)
    write(destination / "ablation" / "unavailable.txt", "whisper rejected the prompt")

    results = load_adapter_results(destination, REFERENCE)

    assert len(results.asr) == 1
    assert len(results.notes) == 1
    assert "whisper rejected" not in results.notes[0]


def test_results_hold_one_metric_per_record_with_superseded_marked(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)

    results = load_adapter_results(destination, REFERENCE)

    by_stage = {
        (item.script_stage, item.superseded): item for item in results.llm_stages
    }
    assert set(by_stage) == {
        (Stage.CRITIC, False),
        (Stage.SEMANTIC, False),
        (Stage.FLASHCARDS, True),
    }
    semantic = by_stage[(Stage.SEMANTIC, False)]
    assert (semantic.requests, semantic.retries) == (2, 1)


def test_results_count_critic_rejections_from_records_and_unit_flags(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)

    critic = load_adapter_results(destination, REFERENCE).critic

    assert critic is not None
    assert critic.reviews == 4
    assert critic.redrafts == 1
    assert critic.units == 4
    assert critic.final_rejected_units == 1
    assert critic.rejected_reviews == 2


def test_results_have_no_critic_without_a_critic_record(
    sources: HarvestSources, tmp_path: Path
) -> None:
    (sources.llm_runs / "critic.json").unlink()
    destination, _ = harvest(sources, tmp_path)

    assert load_adapter_results(destination, REFERENCE).critic is None


def test_phases_merge_both_logs_preferring_ran_over_skipped(
    sources: HarvestSources, tmp_path: Path
) -> None:
    destination, _ = harvest(sources, tmp_path)

    phases = {
        item.number: item
        for item in load_adapter_results(destination, REFERENCE).phases
    }

    assert [number for number in phases] == [8, 9]
    assert phases[8].status == "ran"
    assert phases[8].seconds == pytest.approx(4.5)
    assert phases[9].status == "ran"
    assert phases[9].seconds == pytest.approx(2.0)


def test_not_run_marker_gives_a_not_run_adapter(tmp_path: Path) -> None:
    write(tmp_path / NOT_RUN_MARKER, "the remote provider refused the request\n")

    result = load_not_run(tmp_path, AdapterName.CODEX)

    assert result is not None
    assert result.adapter is AdapterName.CODEX
    assert result.reason == "the remote provider refused the request"


def test_no_marker_means_the_adapter_was_run(tmp_path: Path) -> None:
    assert load_not_run(tmp_path, AdapterName.CODEX) is None


def test_an_empty_not_run_marker_is_refused(tmp_path: Path) -> None:
    write(tmp_path / NOT_RUN_MARKER, "  \n")

    with pytest.raises(HarvestError, match="no reason"):
        load_not_run(tmp_path, AdapterName.CODEX)


def test_loading_a_directory_without_a_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(HarvestError, match="harvest.json"):
        load_adapter_results(tmp_path, REFERENCE)


def test_operator_notes_in_the_logs_become_report_notes(
    sources: HarvestSources, tmp_path: Path
) -> None:
    write(
        sources.logs / "notes.txt",
        "flashcards floor lowered to 5\n\nexams attempt 1 failed\n",
    )
    destination, _ = harvest(sources, tmp_path)

    results = load_adapter_results(destination, REFERENCE)

    assert results.notes == ("flashcards floor lowered to 5", "exams attempt 1 failed")


def test_harvest_sources_refuse_an_unsafe_lecture_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe lecture id"):
        HarvestSources.for_lecture("../../x", data_root=tmp_path, logs_root=tmp_path)
