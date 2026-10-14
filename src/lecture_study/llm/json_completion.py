"""Shared JSON completion: one parse contract and one bounded retry.

``complete_json`` works against any ``LlmGateway``. It owns the parse step and
the single retry that appends trusted feedback to the prompt; model output is
never replayed into the retry. Gateway failures (process, egress, size,
configuration) are never retried: a retry cannot repair them (D-22).
"""

from __future__ import annotations

from collections.abc import Callable

from lecture_study.llm.gateway import LlmGateway, LlmGatewayError, LlmRequest, Stage
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    build_bounded_retry_prompt_section,
    build_llm_retry_feedback,
)

_RETRY_HEADING = "Retry feedback:"


def complete_json[T](
    gateway: LlmGateway,
    *,
    stage: Stage,
    prompt: str,
    parse: Callable[[bytes], T],
    validate: Callable[[T], None] | None = None,
    retry_on: tuple[type[Exception], ...] = (LlmResponseContractError,),
    timeout_seconds: float | None = None,
) -> T:
    """Complete, parse and validate; retry once on a ``retry_on`` failure."""
    first_request = LlmRequest(stage, prompt, False, timeout_seconds)
    try:
        return _complete_and_parse(gateway, first_request, parse, validate)
    except LlmGatewayError:
        raise
    except retry_on as error:
        retry_prompt = prompt + _retry_section(error)
    retry_request = LlmRequest(stage, retry_prompt, True, timeout_seconds)
    return _complete_and_parse(gateway, retry_request, parse, validate)


def _complete_and_parse[T](
    gateway: LlmGateway,
    request: LlmRequest,
    parse: Callable[[bytes], T],
    validate: Callable[[T], None] | None,
) -> T:
    result = parse(gateway.complete(request).content)
    if validate is not None:
        validate(result)
    return result


def _retry_section(error: Exception) -> str:
    return build_bounded_retry_prompt_section(
        heading=_RETRY_HEADING,
        feedback=build_llm_retry_feedback(error),
    )
