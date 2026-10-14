"""complete_json: one parse, one bounded retry, no retry of gateway failures."""

from __future__ import annotations

import json

import pytest

from lecture_study.llm.gateway import (
    AdapterName,
    GatewayIdentity,
    LlmCallError,
    LlmRequest,
    LlmResponse,
    Stage,
)
from lecture_study.llm.json_completion import complete_json
from lecture_study.ops.llm_response_contract import LlmResponseContractError

PROMPT = "BASE-PROMPT"


class DomainParsingError(ValueError):
    """Stand-in for a script-level parsing error."""


class RecordingGateway:
    """In-memory gateway returning scripted responses or raising scripted errors."""

    def __init__(self, outcomes: list[bytes | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LlmResponse(content=outcome)

    def describe(self) -> GatewayIdentity:
        return GatewayIdentity(AdapterName.CODEX, "recording-gateway")


def _parse_object(content: bytes) -> dict[str, int]:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        raise LlmResponseContractError(
            "malformed JSON output", retry_category="malformed_json"
        ) from None
    return {str(key): int(value) for key, value in decoded.items()}


def _parse_with_domain_error(content: bytes) -> dict[str, int]:
    try:
        return _parse_object(content)
    except LlmResponseContractError:
        raise DomainParsingError("domain parse failure") from None


def _require_positive_value(result: dict[str, int]) -> None:
    if result["value"] <= 0:
        raise LlmResponseContractError(
            "value must be positive", retry_category="schema"
        )


def test_first_attempt_success_sends_one_request() -> None:
    gateway = RecordingGateway([b'{"value": 1}'])

    result = complete_json(
        gateway, stage=Stage.SEMANTIC, prompt=PROMPT, parse=_parse_object
    )

    assert result == {"value": 1}
    assert [request.is_retry for request in gateway.requests] == [False]
    assert gateway.requests[0].prompt == PROMPT
    assert gateway.requests[0].stage is Stage.SEMANTIC


def test_parse_failure_is_retried_once_with_feedback() -> None:
    gateway = RecordingGateway([b"not json", b'{"value": 2}'])

    result = complete_json(
        gateway, stage=Stage.DRAFTING, prompt=PROMPT, parse=_parse_object
    )

    assert result == {"value": 2}
    first, second = gateway.requests
    assert second.is_retry is True
    assert second.prompt.startswith(first.prompt)
    assert "Retry feedback:" in second.prompt
    assert "not json" not in second.prompt


def test_validate_failure_is_retried_the_same_way() -> None:
    gateway = RecordingGateway([b'{"value": 0}', b'{"value": 3}'])

    result = complete_json(
        gateway,
        stage=Stage.EXAMS,
        prompt=PROMPT,
        parse=_parse_object,
        validate=_require_positive_value,
    )

    assert result == {"value": 3}
    assert "Retry feedback:" in gateway.requests[1].prompt


def test_domain_error_listed_in_retry_on_is_retried() -> None:
    gateway = RecordingGateway([b"not json", b'{"value": 4}'])

    result = complete_json(
        gateway,
        stage=Stage.SEMANTIC,
        prompt=PROMPT,
        parse=_parse_with_domain_error,
        retry_on=(DomainParsingError,),
    )

    assert result == {"value": 4}
    assert len(gateway.requests) == 2


def test_domain_error_not_listed_in_retry_on_propagates_immediately() -> None:
    gateway = RecordingGateway([b"not json", b'{"value": 4}'])

    with pytest.raises(DomainParsingError):
        complete_json(
            gateway,
            stage=Stage.SEMANTIC,
            prompt=PROMPT,
            parse=_parse_with_domain_error,
        )

    assert len(gateway.requests) == 1


def test_second_failure_propagates_after_exactly_two_requests() -> None:
    gateway = RecordingGateway([b"first bad", b"second bad"])

    with pytest.raises(LlmResponseContractError):
        complete_json(gateway, stage=Stage.CRITIC, prompt=PROMPT, parse=_parse_object)

    assert len(gateway.requests) == 2


def test_gateway_error_is_never_retried_even_when_retry_on_matches() -> None:
    call_error = LlmCallError(Stage.PODCAST, AdapterName.CODEX, "timed out after 1s")
    gateway = RecordingGateway([call_error, b'{"value": 1}'])

    with pytest.raises(LlmCallError):
        complete_json(
            gateway,
            stage=Stage.PODCAST,
            prompt=PROMPT,
            parse=_parse_object,
            retry_on=(RuntimeError,),
        )

    assert len(gateway.requests) == 1


def test_timeout_reaches_both_requests() -> None:
    gateway = RecordingGateway([b"bad", b'{"value": 1}'])

    complete_json(
        gateway,
        stage=Stage.PODCAST,
        prompt=PROMPT,
        parse=_parse_object,
        timeout_seconds=42.0,
    )

    assert [request.timeout_seconds for request in gateway.requests] == [42.0, 42.0]
