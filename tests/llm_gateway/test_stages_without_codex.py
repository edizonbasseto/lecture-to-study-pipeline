"""Success criterion 1: stages 5, 6 and 7 run on llama-cpp with no Codex call.

The orchestrator drives the real generation scripts as child processes. The
llama.cpp executables are scripted stubs answering by prompt content, and a
tripwire ``codex`` stub sits first on ``PATH`` so any Codex invocation is
recorded. No model or binary is ever downloaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture_study.cli import orchestrator
from lecture_study.cli.orchestrator import OrchestratorContext, run_phase
from lecture_study.domain.models import (
    CanonicalFrame,
    EvidencePack,
    TranscriptionSegment,
)
from lecture_study.llm.config import ADAPTER_OVERRIDE_ENV, CONFIG_PATH_ENV
from lecture_study.ops.llm_instructions import (
    CRITIC_INSTRUCTION,
    EXTRACTION_INSTRUCTION,
)

LECTURE_ID = "gw-demo-001"
SCRIPTS_DIRECTORY = Path(__file__).resolve().parents[2] / "scripts"
LLAMA_SCRIPTED_TOKEN_COUNT = 100
DRAFTING_INSTRUCTION_OPENING = "You are a medical writer."
LLAMA_CPP_ADAPTER = "llama-cpp"
RECORDED_STAGES = ("semantic", "drafting", "critic")

LEDGER_TEXT = json.dumps(
    {
        "lecture_id": LECTURE_ID,
        "pack_sequence": 1,
        "facts": [
            {
                "entity_name": "baço",
                "category": "anatomy",
                "clinical_context": "contexto clínico",
                "timestamp_ref": 1.0,
            }
        ],
    }
)
DRAFT_TEXT = json.dumps(
    {
        "lecture_id": LECTURE_ID,
        "pack_sequence": 1,
        "title": "Titulo do rascunho",
        "body_markdown": "Corpo do rascunho.",
        "clinical_warnings": [],
    }
)
PASSING_REVIEW_TEXT = json.dumps(
    {"passed": True, "hallucination_score": 0.0, "feedback": "ok"}
)


def _write_evidence_pack(working_directory: Path) -> None:
    pack = EvidencePack(
        lecture_id=LECTURE_ID,
        pack_sequence=1,
        start_time=0.0,
        end_time=5.0,
        anchor_frame=CanonicalFrame(
            timestamp_sec=2.0,
            ocr_text="Slide de exemplo",
            image_path=Path("workspace/data/04_canonical/frame-001.png"),
        ),
        segments=[
            TranscriptionSegment(
                start_time=0.0, end_time=5.0, text="Transcrição de exemplo."
            )
        ],
    )
    pack_directory = working_directory / "workspace/data/06_evidence_packs" / LECTURE_ID
    pack_directory.mkdir(parents=True)
    (pack_directory / "pack_001.json").write_text(
        pack.model_dump_json(), encoding="utf-8"
    )


def _install_stage_rules(llama_stubs) -> None:
    llama_stubs.control.rules(
        "llama-tokenize", [{"match": "", "token_count": LLAMA_SCRIPTED_TOKEN_COUNT}]
    )
    llama_stubs.control.rules(
        "llama-completion",
        [
            {"match": EXTRACTION_INSTRUCTION[:60], "stdout": LEDGER_TEXT},
            {"match": DRAFTING_INSTRUCTION_OPENING, "stdout": DRAFT_TEXT},
            {"match": CRITIC_INSTRUCTION[:60], "stdout": PASSING_REVIEW_TEXT},
        ],
    )


def _llama_cpp_section(llama_stubs) -> str:
    return (
        "llama_cpp:\n"
        f"  completion_executable: {llama_stubs.completion_executable}\n"
        f"  tokenize_executable: {llama_stubs.tokenize_executable}\n"
        f"  model_path: {llama_stubs.model_path}\n"
        "  model_family: stub-family\n"
    )


@pytest.fixture
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, llama_stubs, tripwire_codex_on_path
) -> Path:
    working_directory = tmp_path / "work"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(orchestrator, "_GENERATION_SCRIPTS_ROOT", SCRIPTS_DIRECTORY)
    monkeypatch.delenv(ADAPTER_OVERRIDE_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    _write_evidence_pack(working_directory)
    _install_stage_rules(llama_stubs)
    return working_directory


def _select_configuration(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    configuration_path = workspace / "gateway.yaml"
    configuration_path.write_text(body, encoding="utf-8")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(configuration_path))


async def _run_stages_five_to_seven() -> None:
    for phase_number in (5, 6, 7):
        result = await run_phase(OrchestratorContext(), phase_number, LECTURE_ID)
        assert result.status == "ran", phase_number


def _assert_llama_cpp_served_every_stage(
    workspace: Path, tripwire_codex_on_path
) -> None:
    data = workspace / "workspace/data"
    assert (data / "07_semantic_ledgers" / LECTURE_ID / "ledger_001.json").exists()
    assert (data / "08_drafts" / LECTURE_ID / "draft_001.json").exists()
    assert (data / "09_validated_units" / LECTURE_ID / "unit_001.json").exists()
    for stage in RECORDED_STAGES:
        record = json.loads(
            (data / "llm_runs" / LECTURE_ID / f"{stage}.json").read_text()
        )
        assert record["status"] == "succeeded", stage
        assert {usage["adapter_name"] for usage in record["gateways"]} == {
            LLAMA_CPP_ADAPTER
        }, stage
    assert tripwire_codex_on_path.calls() == []


@pytest.mark.asyncio
async def test_yaml_selecting_llama_cpp_runs_stages_five_to_seven_without_codex(
    workspace: Path,
    llama_stubs,
    tripwire_codex_on_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_configuration(
        workspace,
        monkeypatch,
        f"default_adapter: {LLAMA_CPP_ADAPTER}\n{_llama_cpp_section(llama_stubs)}",
    )

    await _run_stages_five_to_seven()

    _assert_llama_cpp_served_every_stage(workspace, tripwire_codex_on_path)


@pytest.mark.asyncio
async def test_environment_override_beats_a_codex_yaml_and_reaches_the_children(
    workspace: Path,
    llama_stubs,
    tripwire_codex_on_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_configuration(
        workspace,
        monkeypatch,
        "default_adapter: codex\n"
        "stages:\n"
        "  semantic: codex\n"
        "  drafting: codex\n"
        "  critic: codex\n"
        f"{_llama_cpp_section(llama_stubs)}",
    )
    monkeypatch.setenv(ADAPTER_OVERRIDE_ENV, LLAMA_CPP_ADAPTER)

    await _run_stages_five_to_seven()

    _assert_llama_cpp_served_every_stage(workspace, tripwire_codex_on_path)
