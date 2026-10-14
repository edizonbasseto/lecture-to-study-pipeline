"""Critic repair loop (ADR 0036) through both adapters over stub binaries.

The loop lives in the stage 7 script. Every case runs once per registered
adapter (``codex`` and ``llama_cpp``) with real stub processes and one adapter
serving both the critic and the drafting gateway, so the loop is proven
adapter-neutral. No model or binary is ever downloaded.
"""

from __future__ import annotations

import importlib.util
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
from lecture_study.llm.codex_adapter import CodexCliAdapter
from lecture_study.llm.gateway import LlmGateway
from lecture_study.llm.llama_cpp_adapter import LlamaCppAdapter

FEEDBACK_MARKER = "FEEDBACK-MARKER-7"
CRITIC_REJECTION_HEADING = "A previous attempt was rejected by the critic reviewer"
LLAMA_SCRIPTED_TOKEN_COUNT = 100
REPEAT_PENALTY_FLAG = "--repeat-penalty"
SCRIPTS_DIRECTORY = Path(__file__).resolve().parents[2] / "scripts"

REJECTED_REVIEW = (
    f'{{"passed": false, "hallucination_score": 0.8, "feedback": "{FEEDBACK_MARKER}"}}'
)
APPROVED_REVIEW = '{"passed": true, "hallucination_score": 0.0, "feedback": "ok"}'
REDRAFT = (
    '{"lecture_id": "lecture-001", "pack_sequence": 1, "title": "Titulo reparado", '
    '"body_markdown": "Corpo reparado.", "clinical_warnings": []}'
)
NOT_JSON = "this is not json"


def _load_critic_script() -> ModuleType:
    # The script does `import generate_codex_drafts` as a plain sibling
    # import, so its directory must be importable (see
    # tests/test_generate_codex_critic_review.py).
    if str(SCRIPTS_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIRECTORY))
    script_path = SCRIPTS_DIRECTORY / "generate_codex_critic_review.py"
    spec = importlib.util.spec_from_file_location(
        "generate_codex_critic_review_repair_contract", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load critic script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_CRITIC_SCRIPT = _load_critic_script()


class CodexHarness:
    """Scripts the Codex stub behind the adapter-neutral harness surface."""

    supports_retry_penalty = False

    def __init__(self, codex_stub) -> None:
        self._stub = codex_stub
        self.gateway: LlmGateway = CodexCliAdapter(str(codex_stub.executable))

    def script_responses(self, responses: list[str]) -> None:
        self._stub.control.script(
            "codex", [{"response_file_text": response} for response in responses]
        )

    def model_calls(self) -> list[dict[str, object]]:
        return self._stub.control.calls("codex")

    def prompt_of(self, call: dict[str, object]) -> str:
        return str(call["argv"][-1])


class LlamaCppHarness:
    """Scripts the llama stubs behind the adapter-neutral harness surface."""

    supports_retry_penalty = True

    def __init__(self, llama_stubs) -> None:
        self._stubs = llama_stubs
        self.gateway: LlmGateway = LlamaCppAdapter(
            str(llama_stubs.completion_executable),
            str(llama_stubs.tokenize_executable),
            llama_stubs.model_path,
            "contract-family",
        )

    def script_responses(self, responses: list[str]) -> None:
        self._stubs.control.script(
            "llama-tokenize",
            [{"token_count": LLAMA_SCRIPTED_TOKEN_COUNT} for _ in responses],
        )
        self._stubs.control.script(
            "llama-completion", [{"stdout": response} for response in responses]
        )

    def model_calls(self) -> list[dict[str, object]]:
        return self._stubs.control.calls("llama-completion")

    def prompt_of(self, call: dict[str, object]) -> str:
        return str(call["prompt_file_text"])


@pytest.fixture(params=["codex", "llama_cpp"])
def adapter_harness(
    request: pytest.FixtureRequest, codex_stub, llama_stubs
) -> CodexHarness | LlamaCppHarness:
    if request.param == "codex":
        return CodexHarness(codex_stub)
    if request.param == "llama_cpp":
        return LlamaCppHarness(llama_stubs)
    raise AssertionError(f"unregistered adapter param: {request.param}")


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


def _original_draft() -> LearningUnitDraft:
    return LearningUnitDraft(
        lecture_id="lecture-001",
        pack_sequence=1,
        title="Titulo original",
        body_markdown="Corpo original.",
        clinical_warnings=[],
    )


def _evaluate_with_repair(harness, *, max_retries: int = 2):
    return _CRITIC_SCRIPT._evaluate_with_repair(
        critic_gateway=harness.gateway,
        drafting_gateway=harness.gateway,
        draft=_original_draft(),
        pack=_pack(),
        ledger=_ledger(),
        max_retries=max_retries,
    )


def _calls_with_repeat_penalty(harness) -> list[int]:
    return [
        index
        for index, call in enumerate(harness.model_calls())
        if REPEAT_PENALTY_FLAG in call["argv"]
    ]


def test_rejected_review_is_repaired_with_critic_feedback_and_no_retry_penalty(
    adapter_harness,
) -> None:
    adapter_harness.script_responses([REJECTED_REVIEW, REDRAFT, APPROVED_REVIEW])

    final_draft, review, attempts = _evaluate_with_repair(adapter_harness)

    calls = adapter_harness.model_calls()
    redraft_prompt = adapter_harness.prompt_of(calls[1])
    assert review.passed is True
    assert attempts == 1
    assert final_draft.title == "Titulo reparado"
    assert len(calls) == 3
    assert CRITIC_REJECTION_HEADING in redraft_prompt
    assert FEEDBACK_MARKER in redraft_prompt
    assert "Retry feedback:" not in redraft_prompt
    assert _calls_with_repeat_penalty(adapter_harness) == []


def test_unparseable_review_is_retried_once_and_only_that_call_is_penalised(
    adapter_harness,
) -> None:
    adapter_harness.script_responses(
        [NOT_JSON, REJECTED_REVIEW, REDRAFT, APPROVED_REVIEW]
    )

    final_draft, review, attempts = _evaluate_with_repair(adapter_harness)

    calls = adapter_harness.model_calls()
    assert review.passed is True
    assert attempts == 1
    assert final_draft.title == "Titulo reparado"
    assert len(calls) == 4
    assert "Retry feedback:" in adapter_harness.prompt_of(calls[1])
    assert "Retry feedback:" not in adapter_harness.prompt_of(calls[2])
    expected_penalised_calls = [1] if adapter_harness.supports_retry_penalty else []
    assert _calls_with_repeat_penalty(adapter_harness) == expected_penalised_calls
