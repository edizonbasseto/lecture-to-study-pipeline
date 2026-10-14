"""Unit tests for the Codex-based critic-review generator (Phase 7,
ADR 0035).

The `codex` subprocess itself is never invoked here -- these tests stub
`run_bounded_process` and exercise the pure logic: prompt construction,
the scratch-cwd security invariant, and the retry-on-failure path,
mirroring tests/test_generate_codex_drafts.py's own coverage for the
sibling Codex-based generator."""

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
    LearningUnitDraft,
    SemanticFact,
    SemanticLedger,
    TranscriptionSegment,
)


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

_REVIEW_JSON = b'{"passed":true,"hallucination_score":0.0,"feedback":"Grounded."}'


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


def test_call_codex_for_review_runs_with_a_scratch_cwd_not_the_repo_root() -> None:
    """Mirrors test_generate_codex_drafts.py's own security-review
    coverage: the subprocess must never run with the repository checkout
    (which holds .env) as its cwd/-C working root."""

    captured: dict[str, object] = {}

    def fake_run_bounded_process(args, *, cwd, **kwargs):
        captured["cwd"] = cwd
        captured["dash_c"] = args[args.index("-C") + 1]
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(_REVIEW_JSON)

    with patch.object(
        _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
    ):
        review = _MODULE._call_codex_for_review(
            codex_executable="codex",
            environment={},
            prompt="prompt",
        )

    repo_root = Path(__file__).parents[1].resolve()
    assert review.passed is True
    assert Path(captured["cwd"]).resolve() != repo_root
    assert str(captured["cwd"]) == captured["dash_c"]


def test_call_codex_for_review_raises_on_schema_mismatch() -> None:
    def fake_run_bounded_process(args, **_kwargs):
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(b'{"unexpected": "shape"}')

    with (
        patch.object(
            _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
        ),
        pytest.raises(_MODULE.LlmResponseContractError),
    ):
        _MODULE._call_codex_for_review(
            codex_executable="codex",
            environment={},
            prompt="prompt",
        )


def test_generate_review_retries_once_with_feedback_on_first_failure() -> None:
    attempts: list[str] = []

    def fake_call_codex_for_review(*, codex_executable, environment, prompt):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise _MODULE.LlmResponseContractError("boom", retry_category="schema")
        return _MODULE._REVIEW_ADAPTER.validate_json(_REVIEW_JSON)

    with patch.object(
        _MODULE, "_call_codex_for_review", side_effect=fake_call_codex_for_review
    ):
        review = _MODULE._generate_review(
            codex_executable="codex",
            environment={},
            draft=_draft(),
            pack=_pack(),
            ledger=_ledger(),
        )

    assert review.passed is True
    assert len(attempts) == 2
    assert "Retry feedback:" in attempts[1]


def test_generate_review_reports_a_rejected_draft_verdict_unchanged() -> None:
    """_generate_review() itself is a single-shot verdict, with no repair
    logic of its own -- the repair loop lives one layer up, in
    _evaluate_with_repair (see tests below)."""
    rejected = _MODULE._REVIEW_ADAPTER.validate_json(
        b'{"passed":false,"hallucination_score":0.9,"feedback":"Unsupported claim."}'
    )

    with patch.object(_MODULE, "_call_codex_for_review", return_value=rejected):
        review = _MODULE._generate_review(
            codex_executable="codex",
            environment={},
            draft=_draft(),
            pack=_pack(),
            ledger=_ledger(),
        )

    assert review.passed is False
    assert review.feedback == "Unsupported claim."


def test_evaluate_with_repair_skips_redraft_when_first_review_passes() -> None:
    """No repair attempt (and no call into generate_codex_drafts at all)
    when the first review already passes -- matches
    CriticOrchestrator._evaluate_with_repair's own short-circuit."""
    approved = _MODULE._REVIEW_ADAPTER.validate_json(_REVIEW_JSON)
    original_draft = _draft()

    with (
        patch.object(_MODULE, "_generate_review", return_value=approved),
        patch.object(
            _MODULE.generate_codex_drafts,
            "_generate_draft",
            side_effect=AssertionError("must not redraft on a first-try pass"),
        ),
    ):
        final_draft, review, attempts = _MODULE._evaluate_with_repair(
            codex_executable="codex",
            environment={},
            draft=original_draft,
            pack=_pack(),
            ledger=_ledger(),
            max_retries=2,
        )

    assert review.passed is True
    assert attempts == 0
    assert final_draft is original_draft


def test_evaluate_with_repair_redrafts_with_critic_feedback_until_passing() -> None:
    """ADR 0035 decision 4 revision (2026-09-16, operator report): a real
    batch run rejected 19/23 units for genuine grounding defects with no
    repair path, leaving most of a lecture permanently flagged. This
    mirrors CriticOrchestrator._evaluate_with_repair: on rejection,
    redraft via generate_codex_drafts._generate_draft with the critic's
    feedback, then re-review, until it passes or retries run out."""
    rejected = _MODULE._REVIEW_ADAPTER.validate_json(
        b'{"passed":false,"hallucination_score":0.8,'
        b'"feedback":"Claim X is unsupported."}'
    )
    approved = _MODULE._REVIEW_ADAPTER.validate_json(_REVIEW_JSON)
    reviews = iter([rejected, approved])
    repaired_draft = _draft()

    def fake_generate_draft(*, codex_executable, environment, pack, ledger, feedback):
        assert feedback == "Claim X is unsupported."
        return repaired_draft

    with (
        patch.object(
            _MODULE, "_generate_review", side_effect=lambda **_kw: next(reviews)
        ),
        patch.object(
            _MODULE.generate_codex_drafts,
            "_generate_draft",
            side_effect=fake_generate_draft,
        ) as mock_generate_draft,
    ):
        final_draft, review, attempts = _MODULE._evaluate_with_repair(
            codex_executable="codex",
            environment={},
            draft=_draft(),
            pack=_pack(),
            ledger=_ledger(),
            max_retries=2,
        )

    assert review.passed is True
    assert attempts == 1
    assert final_draft is repaired_draft
    assert mock_generate_draft.call_count == 1


def test_evaluate_with_repair_gives_up_after_max_retries_and_keeps_last_attempt() -> (
    None
):
    """Matches the local CriticOrchestrator's own exhausted-retries
    behavior: a unit that never passes is still persisted, with its last
    (failing) attempt and verdict, not silently discarded."""
    always_rejected = _MODULE._REVIEW_ADAPTER.validate_json(
        b'{"passed":false,"hallucination_score":0.5,"feedback":"Still unsupported."}'
    )
    last_draft = _draft()

    with (
        patch.object(_MODULE, "_generate_review", return_value=always_rejected),
        patch.object(
            _MODULE.generate_codex_drafts,
            "_generate_draft",
            return_value=last_draft,
        ) as mock_generate_draft,
    ):
        final_draft, review, attempts = _MODULE._evaluate_with_repair(
            codex_executable="codex",
            environment={},
            draft=_draft(),
            pack=_pack(),
            ledger=_ledger(),
            max_retries=2,
        )

    assert review.passed is False
    assert attempts == 2
    assert final_draft is last_draft
    assert mock_generate_draft.call_count == 2
