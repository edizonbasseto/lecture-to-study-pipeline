"""Unit tests for the Codex-based semantic-ledger generator (Phase 5,
ADR 0035).

The `codex` subprocess itself is never invoked here -- these tests stub
`run_bounded_process` and exercise the pure logic: prompt construction,
the scratch-cwd security invariant, the retry-on-failure path, and the
traceability guard that motivated ADR 0035's own code review (a mismatched
or hallucinated ledger must never reach disk), mirroring
tests/test_generate_codex_drafts.py's own coverage for the sibling
Codex-based generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from lecture_study.domain.models import (
    CanonicalFrame,
    EvidencePack,
    TranscriptionSegment,
)


def _load_module() -> ModuleType:
    script_path = (
        Path(__file__).parents[1] / "scripts" / "generate_codex_semantic_ledgers.py"
    )
    spec = importlib.util.spec_from_file_location(
        "generate_codex_semantic_ledgers",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()

_LEDGER_JSON = (
    b'{"lecture_id":"lecture-001","pack_sequence":1,"facts":['
    b'{"entity_name":"ba\xc3\xa7o","category":"anatomy",'
    b'"clinical_context":"contexto cl\xc3\xadnico","timestamp_ref":1.0}]}'
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


def test_build_prompt_embeds_evidence_and_schema() -> None:
    prompt = _MODULE._build_prompt(_pack())

    assert "lecture_id: lecture-001" in prompt
    assert "pack_sequence: 1" in prompt
    assert "Slide de exemplo" in prompt
    assert "Transcrição de exemplo." in prompt
    assert '"facts"' in prompt


def test_call_codex_for_ledger_runs_with_a_scratch_cwd_not_the_repo_root() -> None:
    """Mirrors test_generate_codex_drafts.py's own security-review
    coverage: the subprocess must never run with the repository checkout
    (which holds .env) as its cwd/-C working root."""

    captured: dict[str, object] = {}

    def fake_run_bounded_process(args, *, cwd, **kwargs):
        captured["cwd"] = cwd
        captured["dash_c"] = args[args.index("-C") + 1]
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(_LEDGER_JSON)

    with patch.object(
        _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
    ):
        ledger = _MODULE._call_codex_for_ledger(
            codex_executable="codex",
            environment={},
            prompt="prompt",
        )

    repo_root = Path(__file__).parents[1].resolve()
    assert ledger.pack_sequence == 1
    assert Path(captured["cwd"]).resolve() != repo_root
    assert str(captured["cwd"]) == captured["dash_c"]


def test_call_codex_for_ledger_raises_on_schema_mismatch() -> None:
    def fake_run_bounded_process(args, **_kwargs):
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(b'{"unexpected": "shape"}')

    with (
        patch.object(
            _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
        ),
        pytest.raises(_MODULE.LlmResponseContractError),
    ):
        _MODULE._call_codex_for_ledger(
            codex_executable="codex",
            environment={},
            prompt="prompt",
        )


def test_generate_ledger_retries_once_with_feedback_on_first_failure() -> None:
    attempts: list[str] = []

    def fake_call_codex_for_ledger(*, codex_executable, environment, prompt):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise _MODULE.LlmResponseContractError("boom", retry_category="schema")
        return _MODULE._LEDGER_ADAPTER.validate_json(_LEDGER_JSON)

    with patch.object(
        _MODULE, "_call_codex_for_ledger", side_effect=fake_call_codex_for_ledger
    ):
        ledger = _MODULE._generate_ledger(
            codex_executable="codex",
            environment={},
            pack=_pack(),
        )

    assert ledger.pack_sequence == 1
    assert len(attempts) == 2
    assert "Retry feedback:" in attempts[1]


def test_generate_ledger_rejects_a_lecture_id_mismatch_even_on_the_retry() -> None:
    """Regression (principal-code-reviewer BLOCK finding, 2026-09-16): a
    local redefinition of _validate_traceability() used to shadow the
    real one imported from semantic/llm_worker.py, silently dropping its
    fact-count-cap and timestamp-grounding checks. Both attempts here
    return a ledger echoing the wrong lecture_id, which only the real
    validator's echo check -- still wired after the fix -- catches."""
    wrong_lecture_ledger = _MODULE._LEDGER_ADAPTER.validate_json(
        _LEDGER_JSON.replace(b"lecture-001", b"lecture-999")
    )

    def fake_call_codex_for_ledger(*, codex_executable, environment, prompt):
        return wrong_lecture_ledger

    with (
        patch.object(
            _MODULE, "_call_codex_for_ledger", side_effect=fake_call_codex_for_ledger
        ),
        pytest.raises(_MODULE.LlmParsingError, match="does not match"),
    ):
        _MODULE._generate_ledger(
            codex_executable="codex",
            environment={},
            pack=_pack(),
        )
