"""Pluggable LLM gateway: one raw-text port with swappable adapters.

Dependency rule (ADR 0046): this package imports only ``lecture_study.domain``,
``lecture_study.quality.prompt_budget`` and the ``lecture_study.ops`` modules
``process_lifecycle``, ``process_environment`` and ``llm_response_contract``.
Scripts and the CLI depend on this package; ``lecture_study.ops`` and
``lecture_study.site`` never import it. Inner modules (the port and the
shared JSON helper) import no adapter module.
"""

from lecture_study.llm.gateway import (
    AdapterName,
    GatewayConfigurationError,
    GatewayIdentity,
    LlmCallError,
    LlmGateway,
    LlmGatewayError,
    LlmRequest,
    LlmResponse,
    PromptTooLargeError,
    RemoteEgressRefusedError,
    Stage,
)

__all__ = [
    "AdapterName",
    "GatewayConfigurationError",
    "GatewayIdentity",
    "LlmCallError",
    "LlmGateway",
    "LlmGatewayError",
    "LlmRequest",
    "LlmResponse",
    "PromptTooLargeError",
    "RemoteEgressRefusedError",
    "Stage",
]
