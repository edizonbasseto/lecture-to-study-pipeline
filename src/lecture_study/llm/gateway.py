"""Raw-text LLM gateway port, its value types and its typed error hierarchy.

The port is deliberately narrow (ADR 0046): prompt text goes in, response
bytes come out. Parsing, validation and the single bounded retry live in
``lecture_study.llm.json_completion``, so every adapter stays a thin process
or network wrapper. This module imports nothing from any adapter.

Error messages are built from the stored attributes only. They never
interpolate prompt text or a process argv, because both embed lecture
content and must not reach logs or tracebacks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol


class Stage(StrEnum):
    """Pipeline stage that issues a model call."""

    SEMANTIC = "semantic"
    DRAFTING = "drafting"
    CRITIC = "critic"
    EXAMS = "exams"
    PODCAST = "podcast"
    FLASHCARDS = "flashcards"


class AdapterName(StrEnum):
    """Registered gateway adapter."""

    CODEX = "codex"
    LLAMA_CPP = "llama-cpp"


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """One raw-text completion request."""

    stage: Stage
    prompt: str = field(repr=False)
    is_retry: bool = False
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is None:
            return
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """Raw model output; the adapter never parses it."""

    content: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class GatewayIdentity:
    """Which model answered, recorded for reproducibility."""

    adapter_name: AdapterName
    model_identifier: str
    model_path: Path | None = None
    engine_version: str | None = None


class LlmGateway(Protocol):
    """Port implemented by every adapter."""

    def complete(self, request: LlmRequest) -> LlmResponse: ...

    def describe(self) -> GatewayIdentity: ...


class LlmGatewayError(RuntimeError):
    """Base class of every failure raised by the gateway layer."""


class GatewayConfigurationError(LlmGatewayError):
    """The gateway configuration is invalid or cannot be honoured."""


class RemoteEgressRefusedError(LlmGatewayError):
    """A remote adapter was selected for a lecture that is not cleared."""

    def __init__(self, lecture_id: str, stage: Stage) -> None:
        super().__init__(
            f"the codex adapter is refused for lecture {lecture_id} at stage "
            f"{stage.value}: the lecture is not flagged remote_llm_allowed "
            "(ADR 0045 section 2)"
        )
        self.lecture_id = lecture_id
        self.stage = stage


class PromptTooLargeError(LlmGatewayError):
    """The prompt cannot fit the adapter limit; no model was called."""

    def __init__(
        self,
        stage: Stage,
        required: int,
        limit: int,
        unit: Literal["tokens", "bytes"],
    ) -> None:
        super().__init__(
            f"prompt for stage {stage.value} needs {required} {unit} but the "
            f"limit is {limit} {unit}; no model was called"
        )
        self.stage = stage
        self.required = required
        self.limit = limit
        self.unit = unit


class LlmCallError(LlmGatewayError):
    """The model call itself failed (process, timeout, output cap)."""

    def __init__(self, stage: Stage, adapter_name: AdapterName, reason: str) -> None:
        super().__init__(
            f"{adapter_name.value} call for stage {stage.value} failed: {reason}"
        )
        self.stage = stage
        self.adapter_name = adapter_name
        self.reason = reason
