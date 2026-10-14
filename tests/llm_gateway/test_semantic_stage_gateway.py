"""Stage 5 end to end: configuration, policy, factory, adapter, run record.

The Codex CLI is a scripted stub executable; no model is ever contacted.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lecture_study.domain.models import (
    CanonicalFrame,
    EvidencePack,
    TranscriptionSegment,
)
from lecture_study.llm.config import ADAPTER_OVERRIDE_ENV, CONFIG_PATH_ENV

LECTURE_ID = "10001"
SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "generate_codex_semantic_ledgers.py"
)
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


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_codex_semantic_ledgers_gateway_test", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator script from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> ModuleType:
    return _load_script()


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    working_directory = tmp_path / "work"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.delenv(ADAPTER_OVERRIDE_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    _write_evidence_pack(working_directory)
    return working_directory


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


def _configure(
    working_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    configuration_path = working_directory / "gateway.yaml"
    configuration_path.write_text(body, encoding="utf-8")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(configuration_path))


def _codex_configuration(codex_stub, *, allowed_ids: list[str]) -> str:
    return (
        f"codex:\n  executable: {codex_stub.executable}\n"
        f"remote_llm_allowed_lecture_ids: {json.dumps(allowed_ids)}\n"
    )


def _run_script(script: ModuleType, runs_root: Path) -> int:
    sys.argv = ["generate_codex_semantic_ledgers", LECTURE_ID]
    sys.argv += ["--llm-runs-root", str(runs_root)]
    return script.main()


def _ledger_path(working_directory: Path) -> Path:
    return (
        working_directory
        / "workspace/data/07_semantic_ledgers"
        / LECTURE_ID
        / "ledger_001.json"
    )


def test_flagged_lecture_runs_end_to_end_and_records_the_run(
    script: ModuleType,
    codex_stub,
    isolated_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(
        isolated_environment,
        monkeypatch,
        _codex_configuration(codex_stub, allowed_ids=[LECTURE_ID]),
    )
    codex_stub.control.script("codex", [{"response_file_text": LEDGER_TEXT}])
    runs_root = tmp_path / "runs"

    exit_code = _run_script(script, runs_root)

    record = json.loads((runs_root / LECTURE_ID / "semantic.json").read_text())
    assert exit_code == 0
    assert _ledger_path(isolated_environment).exists()
    assert record["status"] == "succeeded"
    assert record["gateways"][0]["adapter_name"] == "codex"
    assert record["gateways"][0]["calls"] == 1
    assert len(codex_stub.control.calls("codex")) == 1


def test_unflagged_lecture_is_refused_before_any_call(
    script: ModuleType,
    codex_stub,
    isolated_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure(
        isolated_environment,
        monkeypatch,
        _codex_configuration(codex_stub, allowed_ids=[]),
    )
    runs_root = tmp_path / "runs"

    exit_code = _run_script(script, runs_root)

    assert exit_code == 1
    assert "remote_llm_allowed" in capsys.readouterr().err
    assert codex_stub.control.calls("codex") == []
    assert not _ledger_path(isolated_environment).exists()
    assert not (runs_root / LECTURE_ID / "semantic.json").exists()
