"""Adapter factory: the one place that turns configuration into a gateway.

This is the second validation layer of D-12: scripts call it after their own
gates and before their first model call, so an unknown or misconfigured
adapter stops the run with no model call. Construction spawns nothing and
contacts nothing; it only checks that the configured executables and the model
file exist and are regular files.

For the remote codex adapter the egress policy is consulted first (D-14), so a
refused lecture never causes an executable to be resolved, let alone started.
"""

from __future__ import annotations

from lecture_study.llm.codex_adapter import CodexCliAdapter
from lecture_study.llm.config import (
    CodexSettings,
    GatewayConfiguration,
    LlamaCppSettings,
)
from lecture_study.llm.egress import RemoteEgressPolicy, require_remote_egress_allowed
from lecture_study.llm.gateway import (
    AdapterName,
    GatewayConfigurationError,
    LlmGateway,
    Stage,
)
from lecture_study.llm.llama_cpp_adapter import LlamaCppAdapter
from lecture_study.ops.process_environment import resolve_trusted_executable


def build_gateway(
    stage: Stage,
    *,
    lecture_id: str,
    configuration: GatewayConfiguration,
    egress_policy: RemoteEgressPolicy,
    codex_executable_override: str | None = None,
) -> LlmGateway:
    """Build the adapter that the configuration selects for ``stage``."""

    adapter_name = configuration.adapter_for(stage)
    if adapter_name is AdapterName.CODEX:
        require_remote_egress_allowed(egress_policy, lecture_id=lecture_id, stage=stage)
        return _build_codex_adapter(
            stage, configuration.codex, codex_executable_override
        )
    return _build_llama_cpp_adapter(stage, configuration.llama_cpp)


def _build_codex_adapter(
    stage: Stage, settings: CodexSettings, executable_override: str | None
) -> CodexCliAdapter:
    configured_executable = executable_override or settings.executable
    return CodexCliAdapter(
        _resolve_configured_executable(stage, AdapterName.CODEX, configured_executable)
    )


def _build_llama_cpp_adapter(
    stage: Stage, settings: LlamaCppSettings | None
) -> LlamaCppAdapter:
    if settings is None:
        raise GatewayConfigurationError(
            f"stage {stage.value} selects llama-cpp but the llama_cpp section is absent"
        )
    return LlamaCppAdapter(
        _resolve_configured_executable(
            stage, AdapterName.LLAMA_CPP, settings.completion_executable
        ),
        _resolve_configured_executable(
            stage, AdapterName.LLAMA_CPP, settings.tokenize_executable
        ),
        settings.model_path,
        settings.model_family,
    )


def _resolve_configured_executable(
    stage: Stage, adapter_name: AdapterName, configured_executable: str
) -> str:
    try:
        return resolve_trusted_executable(configured_executable)
    except OSError as error:
        raise GatewayConfigurationError(
            f"the {adapter_name.value} executable {configured_executable!r} "
            f"configured for stage {stage.value} cannot be used: "
            f"{type(error).__name__}"
        ) from None
