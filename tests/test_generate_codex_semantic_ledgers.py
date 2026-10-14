"""Unit tests for the semantic-ledger generator on the LLM gateway (Phase 5,
ADR 0035, ADR 0046).

No model is ever called: a scripted gateway double replays canned responses.
The tests exercise prompt construction, the bounded retry, the traceability
guard that motivated ADR 0035's own code review (a mismatched or
hallucinated ledger must never reach disk), the malformed-id gate and the
import boundary that keeps process code out of the script."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lecture_study.domain.models import (
    CanonicalFrame,
    EvidencePack,
    TranscriptionSegment,
)
from lecture_study.llm import Stage


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


def test_generate_ledger_retries_once_with_feedback_on_first_failure(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway([b'{"unexpected": "shape"}', _LEDGER_JSON])

    ledger = _MODULE._generate_ledger(gateway=gateway, pack=_pack())

    assert ledger.pack_sequence == 1
    assert len(gateway.requests) == 2
    assert [request.is_retry for request in gateway.requests] == [False, True]
    assert all(request.stage is Stage.SEMANTIC for request in gateway.requests)
    assert "Retry feedback:" in gateway.requests[1].prompt
    assert "Retry feedback:" not in gateway.requests[0].prompt


def test_generate_ledger_raises_after_two_schema_mismatches(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway(
        [b'{"unexpected": "shape"}', b'{"unexpected": "shape"}']
    )

    with pytest.raises(_MODULE.LlmResponseContractError):
        _MODULE._generate_ledger(gateway=gateway, pack=_pack())

    assert len(gateway.requests) == 2


def test_generate_ledger_rejects_a_lecture_id_mismatch_even_on_the_retry(
    make_scripted_gateway,
) -> None:
    """Regression (principal-code-reviewer BLOCK finding, 2026-09-16): a
    local redefinition of _validate_traceability() used to shadow the
    real one, silently dropping its fact-count-cap and timestamp-grounding
    checks. Both attempts here return a ledger echoing the wrong
    lecture_id, which only the real validator's echo check -- still wired
    as the `validate` step of the retry boundary -- catches."""
    wrong_lecture_ledger = _LEDGER_JSON.replace(b"lecture-001", b"lecture-999")
    gateway = make_scripted_gateway([wrong_lecture_ledger, wrong_lecture_ledger])

    with pytest.raises(_MODULE.LlmParsingError, match="does not match"):
        _MODULE._generate_ledger(gateway=gateway, pack=_pack())

    assert len(gateway.requests) == 2


def test_main_rejects_a_malformed_lecture_id_before_opening_a_gateway(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened_gateways: list[object] = []
    monkeypatch.setattr(
        _MODULE, "open_stage_gateway", lambda *args, **kwargs: opened_gateways.append(1)
    )
    monkeypatch.setattr(sys, "argv", ["generate_codex_semantic_ledgers", "../x"])

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
