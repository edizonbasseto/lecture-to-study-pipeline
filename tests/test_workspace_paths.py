"""Phase 7 workspace migration regression: every phase's default I/O
directory constant must be rooted under workspace/data/, not data/ or the
project root, so no orchestrator run can write execution artifacts outside
the isolated workspace/ directory."""

from pathlib import Path

from lecture_study.cli import main as cli_main
from lecture_study.evidence import pipeline as evidence_pipeline
from lecture_study.extraction import pipeline as extraction_pipeline
from lecture_study.ops import catalog_bridge
from lecture_study.transcription import pipeline as transcription_pipeline

_WORKSPACE_DATA_ROOT = Path("workspace/data")


def test_extraction_pipeline_directories_are_rooted_under_workspace_data():
    assert extraction_pipeline.RAW_VIDEO_DIR == _WORKSPACE_DATA_ROOT / "01_raw"
    assert (
        extraction_pipeline.AUDIO_OUTPUT_DIR
        == _WORKSPACE_DATA_ROOT / "02_extracted_audio"
    )
    assert (
        extraction_pipeline.SLIDES_OUTPUT_DIR
        == _WORKSPACE_DATA_ROOT / "03_extracted_slides"
    )
    assert (
        extraction_pipeline.CANONICAL_OUTPUT_DIR
        == _WORKSPACE_DATA_ROOT / "04_canonical"
    )


def test_transcription_pipeline_directories_are_rooted_under_workspace_data():
    assert (
        transcription_pipeline.CANONICAL_INPUT_DIR
        == _WORKSPACE_DATA_ROOT / "04_canonical"
    )
    assert (
        transcription_pipeline.TRANSCRIBED_OUTPUT_DIR
        == _WORKSPACE_DATA_ROOT / "05_transcribed"
    )


def test_evidence_pipeline_directories_are_rooted_under_workspace_data():
    assert (
        evidence_pipeline.TRANSCRIBED_INPUT_DIR
        == _WORKSPACE_DATA_ROOT / "05_transcribed"
    )
    assert (
        evidence_pipeline.EVIDENCE_PACKS_OUTPUT_DIR
        == _WORKSPACE_DATA_ROOT / "06_evidence_packs"
    )


def test_cli_queue_path_is_rooted_under_workspace_data():
    assert cli_main.QUEUE_PATH == _WORKSPACE_DATA_ROOT / "queue.json"


def test_ops_catalog_bridge_queue_path_matches_cli_queue_path():
    # ops/catalog_bridge.py deliberately does NOT import cli.orchestrator
    # (domain isolation, see catalog_bridge.py's module docstring) — this
    # test is the only thing guarding the two constants from drifting apart.
    assert catalog_bridge.QUEUE_PATH == cli_main.QUEUE_PATH
