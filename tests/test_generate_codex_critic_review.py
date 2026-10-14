"""Unit tests for the critic-review generator on the LLM gateway (Phase 7,
ADR 0035, ADR 0036, ADR 0046).

No model is ever called: scripted gateway doubles replay canned responses.
Two gateways are scripted because reviews belong to the critic stage and
redrafts of the repair loop belong to the drafting stage (D-21)."""

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
    scripts_dir = Path(__file__).parents[1] / "scripts"
    # generate_codex_critic_review.py does `import generate_codex_drafts`
    # as a plain sibling import -- works unmodified when Python runs the
    # script directly (its own directory lands on sys.path[0]), but
    # importlib.util.spec_from_file_location() bypasses that, so the
    # scripts/ directory needs adding here too.
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / "generate_codex_critic_review.py"
    spec = importlib.util.spec_from_file_location(
        "generate_codex_critic_review",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()

_APPROVED_JSON = b'{"passed":true,"hallucination_score":0.0,"feedback":"Grounded."}'
_REJECTED_JSON = (
    b'{"passed":false,"hallucination_score":0.8,"feedback":"Claim X is unsupported."}'
)
_REPAIRED_DRAFT_JSON = (
    b'{"lecture_id":"lecture-001","pack_sequence":1,"title":"Titulo reparado",'
    b'"body_markdown":"Corpo reparado.","clinical_warnings":[]}'
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


def _draft() -> LearningUnitDraft:
    return LearningUnitDraft(
        lecture_id="lecture-001",
        pack_sequence=1,
        title="Titulo",
        body_markdown="Corpo do texto.",
        clinical_warnings=[],
    )


def test_build_prompt_embeds_draft_evidence_ledger_and_schema() -> None:
    prompt = _MODULE._build_prompt(_draft(), _pack(), _ledger())

    assert "DRAFT title: Titulo" in prompt
    assert "Slide de exemplo" in prompt
    assert "Transcrição de exemplo." in prompt
    assert "baço (anatomy): contexto clínico" in prompt
    assert '"passed"' in prompt


def test_generate_review_retries_once_with_feedback_on_first_failure(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway([_INVALID_JSON, _APPROVED_JSON])

    review = _MODULE._generate_review(
        gateway=gateway, draft=_draft(), pack=_pack(), ledger=_ledger()
    )

    assert review.passed is True
    assert [request.is_retry for request in gateway.requests] == [False, True]
    assert all(request.stage is Stage.CRITIC for request in gateway.requests)
    assert "Retry feedback:" in gateway.requests[1].prompt
    assert "Retry feedback:" not in gateway.requests[0].prompt


def test_generate_review_reports_a_rejected_draft_verdict_unchanged(
    make_scripted_gateway,
) -> None:
    """_generate_review() itself is a single-shot verdict, with no repair
    logic of its own -- a rejection is valid JSON, so it is not retried. The
    repair loop lives one layer up, in _evaluate_with_repair."""
    gateway = make_scripted_gateway([_REJECTED_JSON])

    review = _MODULE._generate_review(
        gateway=gateway, draft=_draft(), pack=_pack(), ledger=_ledger()
    )

    assert review.passed is False
    assert review.feedback == "Claim X is unsupported."
    assert len(gateway.requests) == 1


def test_evaluate_with_repair_skips_redraft_when_first_review_passes(
    make_scripted_gateway,
) -> None:
    """No repair attempt (and no drafting-gateway request at all) when the
    first review already passes -- matches CriticOrchestrator's own
    short-circuit."""
    critic_gateway = make_scripted_gateway([_APPROVED_JSON])
    drafting_gateway = make_scripted_gateway([])
    original_draft = _draft()

    final_draft, review, attempts = _MODULE._evaluate_with_repair(
        critic_gateway=critic_gateway,
        drafting_gateway=drafting_gateway,
        draft=original_draft,
        pack=_pack(),
        ledger=_ledger(),
        max_retries=2,
    )

    assert review.passed is True
    assert attempts == 0
    assert final_draft is original_draft
    assert drafting_gateway.requests == []


def test_evaluate_with_repair_redrafts_with_critic_feedback_until_passing(
    make_scripted_gateway,
) -> None:
    """ADR 0035 decision 4 revision (2026-09-16): on rejection, redraft with
    the critic's feedback through the drafting gateway, then re-review
    through the critic gateway, until it passes or retries run out."""
    critic_gateway = make_scripted_gateway([_REJECTED_JSON, _APPROVED_JSON])
    drafting_gateway = make_scripted_gateway([_REPAIRED_DRAFT_JSON])

    final_draft, review, attempts = _MODULE._evaluate_with_repair(
        critic_gateway=critic_gateway,
        drafting_gateway=drafting_gateway,
        draft=_draft(),
        pack=_pack(),
        ledger=_ledger(),
        max_retries=2,
    )

    assert review.passed is True
    assert attempts == 1
    assert final_draft.title == "Titulo reparado"
    (redraft_request,) = drafting_gateway.requests
    assert redraft_request.stage is Stage.DRAFTING
    assert redraft_request.is_retry is False
    assert _CRITIC_REJECTION_HEADING in redraft_request.prompt
    assert "Claim X is unsupported." in redraft_request.prompt
    assert [request.stage for request in critic_gateway.requests] == [
        Stage.CRITIC,
        Stage.CRITIC,
    ]
    assert "Titulo reparado" in critic_gateway.requests[1].prompt


def test_evaluate_with_repair_gives_up_after_max_retries_and_keeps_last_attempt(
    make_scripted_gateway,
) -> None:
    """Matches the local CriticOrchestrator's own exhausted-retries
    behavior: a unit that never passes is still persisted, with its last
    (failing) attempt and verdict, not silently discarded."""
    critic_gateway = make_scripted_gateway([_REJECTED_JSON] * 3)
    drafting_gateway = make_scripted_gateway([_REPAIRED_DRAFT_JSON] * 2)

    final_draft, review, attempts = _MODULE._evaluate_with_repair(
        critic_gateway=critic_gateway,
        drafting_gateway=drafting_gateway,
        draft=_draft(),
        pack=_pack(),
        ledger=_ledger(),
        max_retries=2,
    )

    assert review.passed is False
    assert review.feedback == "Claim X is unsupported."
    assert attempts == 2
    assert final_draft.title == "Titulo reparado"
    assert len(critic_gateway.requests) == 3
    assert len(drafting_gateway.requests) == 2


def test_run_persists_the_last_failing_pair_and_records_both_gateways(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_scripted_gateway,
) -> None:
    lecture_id = "lecture-001"
    roots = _write_stage_inputs(tmp_path, lecture_id)
    critic_gateway = make_scripted_gateway([_REJECTED_JSON] * 2)
    drafting_gateway = make_scripted_gateway([_REPAIRED_DRAFT_JSON])
    opened_stages: list[Stage] = []
    gateways_by_stage = {Stage.CRITIC: critic_gateway, Stage.DRAFTING: drafting_gateway}

    def open_scripted_gateway(stage: Stage, **_keywords: object):
        opened_stages.append(stage)
        return gateways_by_stage[stage]

    monkeypatch.setattr(_MODULE, "open_stage_gateway", open_scripted_gateway)
    args = argparse.Namespace(
        lecture_id=lecture_id,
        drafts_root=roots["drafts"],
        packs_root=roots["packs"],
        ledgers_root=roots["ledgers"],
        output_root=roots["output"],
        codex_executable=None,
        llm_runs_root=roots["runs"],
        max_retries=1,
    )

    exit_code = _MODULE._run(args)

    assert exit_code == 0
    assert opened_stages == [Stage.CRITIC, Stage.DRAFTING]
    unit = json.loads((roots["output"] / lecture_id / "unit_001.json").read_text())
    assert unit["review"]["passed"] is False
    assert unit["content"]["title"] == "Titulo reparado"
    record = json.loads((roots["runs"] / lecture_id / "critic.json").read_text())
    assert record["status"] == "succeeded"
    assert [(usage["stage"], usage["calls"]) for usage in record["gateways"]] == [
        ("critic", 2),
        ("drafting", 1),
    ]


def test_main_rejects_a_malformed_lecture_id_before_opening_a_gateway(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened_gateways: list[int] = []
    monkeypatch.setattr(
        _MODULE, "open_stage_gateway", lambda *_a, **_k: opened_gateways.append(1)
    )
    monkeypatch.setattr(sys, "argv", ["generate_codex_critic_review", "../x"])

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


def _write_stage_inputs(tmp_path: Path, lecture_id: str) -> dict[str, Path]:
    roots = {
        "drafts": tmp_path / "drafts",
        "packs": tmp_path / "packs",
        "ledgers": tmp_path / "ledgers",
        "output": tmp_path / "validated",
        "runs": tmp_path / "runs",
    }
    inputs = (
        ("drafts", "draft_001.json", _draft().model_dump_json()),
        ("packs", "pack_001.json", _pack().model_dump_json()),
        ("ledgers", "ledger_001.json", _ledger().model_dump_json()),
    )
    for root_name, file_name, content in inputs:
        lecture_directory = roots[root_name] / lecture_id
        lecture_directory.mkdir(parents=True)
        (lecture_directory / file_name).write_text(content, encoding="utf-8")
    return roots


def _imported_module_names(source_text: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source_text)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names
