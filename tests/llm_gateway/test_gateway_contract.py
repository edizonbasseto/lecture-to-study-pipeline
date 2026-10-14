"""Adapter-parametrized contract suite for the LlmGateway port.

Plan 06-02 adds the ``llama_cpp`` param; every case below must pass for every
registered adapter without per-adapter branching in the test bodies.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from lecture_study.llm.codex_adapter import CodexCliAdapter
from lecture_study.llm.gateway import LlmCallError, LlmGateway, LlmRequest, Stage
from lecture_study.llm.json_completion import complete_json
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    parse_llm_json_response,
)

PROMPT_MARKER = "CONTRACT-MARKER-7"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_CAP_BYTES = 1024


class _Item(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    count: int


_ITEM_ADAPTER: TypeAdapter[_Item] = TypeAdapter(_Item)
_ITEM_LIST_ADAPTER: TypeAdapter[list[_Item]] = TypeAdapter(list[_Item])


def _parse_item(content: bytes) -> _Item:
    return parse_llm_json_response(
        content,
        expected_kind="object",
        response_adapter=_ITEM_ADAPTER,
        declared_type=_Item,
    )


def _parse_items(content: bytes) -> list[_Item]:
    return parse_llm_json_response(
        content,
        expected_kind="array",
        response_adapter=_ITEM_LIST_ADAPTER,
        declared_type=list[_Item],
    )


class CodexHarness:
    """Scripts the Codex stub behind the adapter-neutral harness surface."""

    def __init__(self, codex_stub) -> None:
        self._stub = codex_stub
        self.gateway: LlmGateway = CodexCliAdapter(
            str(codex_stub.executable), maximum_output_bytes=OUTPUT_CAP_BYTES
        )

    def script_responses(self, responses: list[bytes]) -> None:
        self._stub.control.script(
            "codex",
            [{"response_file_text": response.decode()} for response in responses],
        )

    def script_failure(self, exit_code: int) -> None:
        self._stub.control.script("codex", [{"exit_code": exit_code}])

    def script_sleep(self, seconds: float) -> None:
        self._stub.control.script("codex", [{"sleep_seconds": seconds}])

    def script_flood(self, byte_count: int) -> None:
        self._stub.control.script("codex", [{"stdout": "x" * byte_count}])

    def model_calls(self) -> list[dict[str, object]]:
        return self._stub.control.calls("codex")

    def prompts(self) -> list[str]:
        return [call["argv"][-1] for call in self.model_calls()]


@pytest.fixture(params=["codex"])
def gateway_harness(request: pytest.FixtureRequest, codex_stub) -> CodexHarness:
    if request.param == "codex":
        return CodexHarness(codex_stub)
    raise AssertionError(f"unregistered adapter param: {request.param}")


def test_raw_bytes_are_returned_verbatim(gateway_harness) -> None:
    raw_response = b'  {"name": "alpha", "count": 1}\n\ntrailing text '
    gateway_harness.script_responses([raw_response])

    response = gateway_harness.gateway.complete(
        LlmRequest(Stage.SEMANTIC, PROMPT_MARKER)
    )

    assert response.content == raw_response
    assert gateway_harness.prompts() == [PROMPT_MARKER]


def test_complete_json_object_round_trip(gateway_harness) -> None:
    gateway_harness.script_responses([b'{"name": "alpha", "count": 1}'])

    result = complete_json(
        gateway_harness.gateway,
        stage=Stage.DRAFTING,
        prompt=PROMPT_MARKER,
        parse=_parse_item,
    )

    assert result == _Item(name="alpha", count=1)


def test_complete_json_array_round_trip(gateway_harness) -> None:
    gateway_harness.script_responses(
        [b'[{"name": "alpha", "count": 1}, {"name": "beta", "count": 2}]']
    )

    result = complete_json(
        gateway_harness.gateway,
        stage=Stage.EXAMS,
        prompt=PROMPT_MARKER,
        parse=_parse_items,
    )

    assert result == [_Item(name="alpha", count=1), _Item(name="beta", count=2)]


def test_invalid_then_valid_response_retries_with_feedback(gateway_harness) -> None:
    gateway_harness.script_responses([b"not json", b'{"name": "alpha", "count": 1}'])

    result = complete_json(
        gateway_harness.gateway,
        stage=Stage.CRITIC,
        prompt=PROMPT_MARKER,
        parse=_parse_item,
    )

    assert result == _Item(name="alpha", count=1)
    first_prompt, second_prompt = gateway_harness.prompts()
    assert "Retry feedback:" not in first_prompt
    assert "Retry feedback:" in second_prompt


def test_two_invalid_responses_raise_after_exactly_two_model_calls(
    gateway_harness,
) -> None:
    gateway_harness.script_responses([b"not json", b"still not json"])

    with pytest.raises(LlmResponseContractError):
        complete_json(
            gateway_harness.gateway,
            stage=Stage.FLASHCARDS,
            prompt=PROMPT_MARKER,
            parse=_parse_item,
        )

    assert len(gateway_harness.model_calls()) == 2


def test_non_zero_exit_raises_a_call_error_after_one_model_call(
    gateway_harness,
) -> None:
    gateway_harness.script_failure(exit_code=3)

    with pytest.raises(LlmCallError) as raised:
        complete_json(
            gateway_harness.gateway,
            stage=Stage.SEMANTIC,
            prompt=PROMPT_MARKER,
            parse=_parse_item,
        )

    assert len(gateway_harness.model_calls()) == 1
    assert PROMPT_MARKER not in str(raised.value)


def test_timeout_raises_a_call_error(gateway_harness) -> None:
    gateway_harness.script_sleep(10)

    with pytest.raises(LlmCallError):
        gateway_harness.gateway.complete(
            LlmRequest(Stage.PODCAST, PROMPT_MARKER, timeout_seconds=0.5)
        )


def test_output_cap_overflow_raises_a_call_error(gateway_harness) -> None:
    gateway_harness.script_flood(OUTPUT_CAP_BYTES * 4)

    with pytest.raises(LlmCallError):
        gateway_harness.gateway.complete(LlmRequest(Stage.PODCAST, PROMPT_MARKER))


def test_child_starts_in_a_fresh_empty_directory(gateway_harness) -> None:
    gateway_harness.script_responses([b'{"name": "alpha", "count": 1}'])

    gateway_harness.gateway.complete(LlmRequest(Stage.SEMANTIC, PROMPT_MARKER))

    (call,) = gateway_harness.model_calls()
    child_cwd = Path(str(call["cwd"])).resolve()
    assert call["cwd_entries"] == []
    assert child_cwd != REPOSITORY_ROOT
    assert child_cwd != Path.cwd().resolve()
