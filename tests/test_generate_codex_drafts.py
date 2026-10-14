"""Unit tests for the Codex-based personal-study draft generator.

The `codex` subprocess itself is never invoked here -- these tests stub
`run_bounded_process` and exercise the pure logic: prompt construction and
the scratch-cwd security invariant (mirrors
tests/test_generate_codex_mock_exam.py's own coverage for the sibling
Codex-based generator)."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from lecture_study.domain.models import (
    CanonicalFrame,
    EvidencePack,
    LearningUnitDraft,
    SemanticFact,
    SemanticLedger,
    TranscriptionSegment,
)


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


def test_call_codex_for_draft_runs_with_a_scratch_cwd_not_the_repo_root() -> None:
    """Mirrors test_generate_codex_mock_exam.py's own security-review
    coverage: the subprocess must never run with the repository checkout
    (which holds .env) as its cwd/-C working root."""

    captured: dict[str, object] = {}

    def fake_run_bounded_process(args, *, cwd, **kwargs):
        captured["cwd"] = cwd
        captured["dash_c"] = args[args.index("-C") + 1]
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(_DRAFT_JSON)

    with patch.object(
        _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
    ):
        draft = _MODULE._call_codex_for_draft(
            codex_executable="codex",
            environment={},
            prompt="prompt",
        )

    repo_root = Path(__file__).parents[1].resolve()
    assert draft.title == "Titulo"
    assert Path(captured["cwd"]).resolve() != repo_root
    assert str(captured["cwd"]) == captured["dash_c"]


def test_call_codex_for_draft_raises_on_schema_mismatch() -> None:
    def fake_run_bounded_process(args, **_kwargs):
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(b'{"unexpected": "shape"}')

    with (
        patch.object(
            _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
        ),
        pytest.raises(_MODULE.LlmResponseContractError),
    ):
        _MODULE._call_codex_for_draft(
            codex_executable="codex",
            environment={},
            prompt="prompt",
        )


def test_generate_draft_retries_once_with_feedback_on_first_failure() -> None:
    attempts: list[str] = []

    def fake_call_codex_for_draft(*, codex_executable, environment, prompt):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise _MODULE.LlmResponseContractError("boom", retry_category="schema")
        return _MODULE._DRAFT_ADAPTER.validate_json(_DRAFT_JSON)

    with patch.object(
        _MODULE, "_call_codex_for_draft", side_effect=fake_call_codex_for_draft
    ):
        draft = _MODULE._generate_draft(
            codex_executable="codex",
            environment={},
            pack=_pack(),
            ledger=_ledger(),
        )

    assert draft.title == "Titulo"
    assert len(attempts) == 2
    assert "Retry feedback:" in attempts[1]


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


def test_run_writes_a_depth_report_alongside_the_lecture_drafts(
    tmp_path: Path,
) -> None:
    """Proves the wiring in `_run`, not just the `_write_depth_report`
    helper in isolation: a real (stubbed-Codex) run must leave
    depth_report.md next to the draft_*.json files it writes."""
    lecture_id = "lecture-001"
    packs_root = tmp_path / "packs"
    ledgers_root = tmp_path / "ledgers"
    output_root = tmp_path / "drafts"
    (packs_root / lecture_id).mkdir(parents=True)
    (ledgers_root / lecture_id).mkdir(parents=True)
    (packs_root / lecture_id / "pack_001.json").write_text(
        _pack().model_dump_json(), encoding="utf-8"
    )
    (ledgers_root / lecture_id / "ledger_001.json").write_text(
        _ledger().model_dump_json(), encoding="utf-8"
    )

    args = argparse.Namespace(
        lecture_id=lecture_id,
        packs_root=packs_root,
        ledgers_root=ledgers_root,
        output_root=output_root,
        codex_executable="codex",
    )

    with (
        patch.object(
            _MODULE, "resolve_trusted_executable", return_value="/trusted/codex"
        ),
        patch.object(
            _MODULE,
            "_generate_draft",
            return_value=_MODULE._DRAFT_ADAPTER.validate_json(_DRAFT_JSON),
        ),
    ):
        exit_code = _MODULE._run(args)

    assert exit_code == 0
    report_path = output_root / lecture_id / "depth_report.md"
    assert report_path.exists()
    assert "| 1 |" in report_path.read_text(encoding="utf-8")
