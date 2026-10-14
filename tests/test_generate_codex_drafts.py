"""Unit tests for the draft generator on the LLM gateway (Phase 6, ADR 0046).

No model is ever called: a scripted gateway double replays canned responses.
The tests exercise prompt construction, the bounded retry, the critic-feedback
redraft, the depth report, the run record and the import boundary that keeps
process code out of the script."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lecture_study.domain.models import (
    CanonicalFrame,
    EvidencePack,
    LearningUnitDraft,
    SemanticFact,
    SemanticLedger,
    TranscriptionSegment,
)
from lecture_study.llm import Stage


def _load_module() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "generate_codex_drafts.py"
    spec = importlib.util.spec_from_file_location(
        "generate_codex_drafts",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()

_DRAFT_JSON = (
    b'{"lecture_id":"lecture-001","pack_sequence":1,"title":"Titulo",'
    b'"body_markdown":"Corpo do texto.","clinical_warnings":[]}'
)
_INVALID_JSON = b'{"unexpected": "shape"}'
_CRITIC_REJECTION_HEADING = "A previous attempt was rejected by the critic reviewer"


def _pack() -> EvidencePack:
    return EvidencePack(
        lecture_id="lecture-001",
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


def _ledger() -> SemanticLedger:
    return SemanticLedger(
        lecture_id="lecture-001",
        pack_sequence=1,
        facts=[
            SemanticFact(
                entity_name="baço",
                category="anatomy",
                clinical_context="contexto clínico",
                timestamp_ref=1.0,
            )
        ],
    )


def test_build_prompt_embeds_evidence_ledger_and_schema() -> None:
    prompt = _MODULE._build_prompt(_pack(), _ledger())

    assert "lecture_id: lecture-001" in prompt
    assert "pack_sequence: 1" in prompt
    assert "Slide de exemplo" in prompt
    assert "Transcrição de exemplo." in prompt
    assert "baço (anatomy): contexto clínico" in prompt
    assert '"title"' in prompt


def test_generate_draft_retries_once_with_feedback_on_first_failure(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway([_INVALID_JSON, _DRAFT_JSON])

    draft = _MODULE._generate_draft(gateway=gateway, pack=_pack(), ledger=_ledger())

    assert draft.title == "Titulo"
    assert [request.is_retry for request in gateway.requests] == [False, True]
    assert all(request.stage is Stage.DRAFTING for request in gateway.requests)
    assert "Retry feedback:" in gateway.requests[1].prompt
    assert "Retry feedback:" not in gateway.requests[0].prompt


def test_generate_draft_raises_after_two_schema_mismatches(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway([_INVALID_JSON, _INVALID_JSON])

    with pytest.raises(_MODULE.LlmResponseContractError):
        _MODULE._generate_draft(gateway=gateway, pack=_pack(), ledger=_ledger())

    assert len(gateway.requests) == 2


def test_generate_draft_with_critic_feedback_is_a_first_attempt_request(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway([_DRAFT_JSON])

    _MODULE._generate_draft(
        gateway=gateway,
        pack=_pack(),
        ledger=_ledger(),
        feedback="FEEDBACK-MARKER-7",
    )

    (request,) = gateway.requests
    assert request.is_retry is False
    assert _CRITIC_REJECTION_HEADING in request.prompt
    assert "FEEDBACK-MARKER-7" in request.prompt


def test_write_depth_report_writes_markdown_table_next_to_drafts(
    tmp_path: Path,
) -> None:
    draft = LearningUnitDraft(
        lecture_id="lecture-001",
        pack_sequence=1,
        title="Titulo",
        body_markdown="Corpo do texto sobre anatomia.",
        clinical_warnings=[],
    )

    _MODULE._write_depth_report([draft], tmp_path)

    report_path = tmp_path / "depth_report.md"
    content = report_path.read_text(encoding="utf-8")
    assert "Categorias cobertas" in content
    assert "| 1 |" in content


def test_write_depth_report_is_a_no_op_for_an_empty_lecture(
    tmp_path: Path,
) -> None:
    _MODULE._write_depth_report([], tmp_path)

    assert not (tmp_path / "depth_report.md").exists()


def test_run_writes_drafts_depth_report_and_run_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_scripted_gateway,
) -> None:
    """Proves the wiring in `_run`: drafts, the depth report next to them and
    the drafting run record all land through one scripted gateway."""
    lecture_id = "lecture-001"
    packs_root = tmp_path / "packs"
    ledgers_root = tmp_path / "ledgers"
    output_root = tmp_path / "drafts"
    runs_root = tmp_path / "runs"
    (packs_root / lecture_id).mkdir(parents=True)
    (ledgers_root / lecture_id).mkdir(parents=True)
    (packs_root / lecture_id / "pack_001.json").write_text(
        _pack().model_dump_json(), encoding="utf-8"
    )
    (ledgers_root / lecture_id / "ledger_001.json").write_text(
        _ledger().model_dump_json(), encoding="utf-8"
    )
    gateway = make_scripted_gateway([_DRAFT_JSON])
    monkeypatch.setattr(_MODULE, "open_stage_gateway", lambda *_a, **_k: gateway)
    args = argparse.Namespace(
        lecture_id=lecture_id,
        packs_root=packs_root,
        ledgers_root=ledgers_root,
        output_root=output_root,
        codex_executable=None,
        llm_runs_root=runs_root,
    )

    exit_code = _MODULE._run(args)

    assert exit_code == 0
    lecture_output = output_root / lecture_id
    assert (lecture_output / "draft_001.json").exists()
    assert "| 1 |" in (lecture_output / "depth_report.md").read_text(encoding="utf-8")
    record = json.loads((runs_root / lecture_id / "drafting.json").read_text())
    assert record["status"] == "succeeded"
    assert record["script_stage"] == "drafting"
    assert record["gateways"][0]["calls"] == 1


def test_main_rejects_a_malformed_lecture_id_before_opening_a_gateway(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened_gateways: list[int] = []
    monkeypatch.setattr(
        _MODULE, "open_stage_gateway", lambda *_a, **_k: opened_gateways.append(1)
    )
    monkeypatch.setattr(sys, "argv", ["generate_codex_drafts", "../x"])

    exit_code = _MODULE.main()

    assert exit_code == 1
    assert "error:" in capsys.readouterr().err
    assert opened_gateways == []


def test_script_imports_no_process_or_scratch_directory_module() -> None:
    forbidden_modules = {
        "tempfile",
        "subprocess",
        "lecture_study.ops.process_lifecycle",
        "lecture_study.ops.process_environment",
    }
    source_text = Path(_MODULE.__file__).read_text(encoding="utf-8")

    assert _imported_module_names(source_text).isdisjoint(forbidden_modules)


def _imported_module_names(source_text: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source_text)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names
