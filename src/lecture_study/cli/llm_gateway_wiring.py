"""Composition point joining the ``llm`` package with the registry-backed policy.

``llm/`` defines the egress question but cannot import the academic registry
(D-02). Scripts and the CLI therefore obtain gateways through this module,
which loads the configuration, builds the registry-backed policy and hands
both to the adapter factory. Nothing here spawns a process or contacts a
service.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from lecture_study.llm.config import GatewayConfiguration, load_gateway_configuration
from lecture_study.llm.factory import build_gateway
from lecture_study.llm.gateway import AdapterName, LlmGateway, Stage
from lecture_study.study_materials.local_lectures import LOCAL_LECTURES_PATH
from lecture_study.study_materials.remote_llm_policy import RegistryRemoteEgressPolicy


def open_stage_gateway(
    stage: Stage,
    *,
    lecture_id: str,
    environ: Mapping[str, str] | None = None,
    codex_executable_override: str | None = None,
    local_lectures_path: Path = LOCAL_LECTURES_PATH,
) -> LlmGateway:
    """Resolve the configuration and build the gateway for one stage."""

    configuration = load_gateway_configuration(_environment(environ))
    return _build_stage_gateway(
        stage,
        lecture_id=lecture_id,
        configuration=configuration,
        codex_executable_override=codex_executable_override,
        local_lectures_path=local_lectures_path,
    )


def preflight_stage_gateways(
    stages: Iterable[Stage],
    *,
    lecture_id: str,
    environ: Mapping[str, str] | None = None,
    local_lectures_path: Path = LOCAL_LECTURES_PATH,
) -> None:
    """Build and discard one gateway per distinct stage; the first failure raises."""

    configuration = load_gateway_configuration(_environment(environ))
    for stage in dict.fromkeys(stages):
        _build_stage_gateway(
            stage,
            lecture_id=lecture_id,
            configuration=configuration,
            codex_executable_override=None,
            local_lectures_path=local_lectures_path,
        )


def _build_stage_gateway(
    stage: Stage,
    *,
    lecture_id: str,
    configuration: GatewayConfiguration,
    codex_executable_override: str | None,
    local_lectures_path: Path,
) -> LlmGateway:
    egress_policy = RegistryRemoteEgressPolicy(
        allowed_portal_lecture_ids=configuration.remote_llm_allowed_lecture_ids,
        local_lectures_path=local_lectures_path,
    )
    if configuration.adapter_for(stage) is AdapterName.CODEX:
        egress_policy.require_consistent_sources()
    return build_gateway(
        stage,
        lecture_id=lecture_id,
        configuration=configuration,
        egress_policy=egress_policy,
        codex_executable_override=codex_executable_override,
    )


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ
