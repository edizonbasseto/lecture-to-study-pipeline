"""Adapter factory and stage preflight: every misconfiguration fails before a spawn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lecture_study.cli.llm_gateway_wiring import preflight_stage_gateways
from lecture_study.llm import factory
from lecture_study.llm.codex_adapter import CodexCliAdapter
from lecture_study.llm.config import (
    CONFIG_PATH_ENV,
    CodexSettings,
    GatewayConfiguration,
    LlamaCppSettings,
)
from lecture_study.llm.factory import build_gateway
from lecture_study.llm.gateway import (
    AdapterName,
    GatewayConfigurationError,
    RemoteEgressRefusedError,
    Stage,
)
from lecture_study.llm.llama_cpp_adapter import LlamaCppAdapter

LECTURE_ID = "10001"


@dataclass(frozen=True)
class FixedEgressPolicy:
    allowed: bool

    def is_remote_allowed(self, lecture_id: str) -> bool:
        return self.allowed


ALLOWING = FixedEgressPolicy(allowed=True)
REFUSING = FixedEgressPolicy(allowed=False)


def _llama_settings(llama_stubs, **overrides: str) -> LlamaCppSettings:
    values = {
        "completion_executable": str(llama_stubs.completion_executable),
        "tokenize_executable": str(llama_stubs.tokenize_executable),
        "model_path": str(llama_stubs.model_path),
        "model_family": "stub-family",
    }
    return LlamaCppSettings(**{**values, **overrides})


def _llama_configuration(llama_stubs, **overrides: str) -> GatewayConfiguration:
    return GatewayConfiguration(
        default_adapter=AdapterName.LLAMA_CPP,
        llama_cpp=_llama_settings(llama_stubs, **overrides),
    )


def _codex_configuration(codex_stub) -> GatewayConfiguration:
    return GatewayConfiguration(
        codex=CodexSettings(executable=str(codex_stub.executable))
    )


def test_codex_for_a_refused_lecture_never_resolves_an_executable(
    codex_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver_calls: list[str] = []
    monkeypatch.setattr(
        factory,
        "resolve_trusted_executable",
        lambda executable: resolver_calls.append(executable),
    )

    with pytest.raises(RemoteEgressRefusedError):
        build_gateway(
            Stage.SEMANTIC,
            lecture_id=LECTURE_ID,
            configuration=_codex_configuration(codex_stub),
            egress_policy=REFUSING,
        )

    assert resolver_calls == []
    assert codex_stub.control.calls("codex") == []


def test_codex_with_an_allowed_lecture_builds_the_codex_adapter(codex_stub) -> None:
    gateway = build_gateway(
        Stage.SEMANTIC,
        lecture_id=LECTURE_ID,
        configuration=_codex_configuration(codex_stub),
        egress_policy=ALLOWING,
    )

    assert isinstance(gateway, CodexCliAdapter)
    assert codex_stub.control.calls("codex") == []


def test_codex_with_a_missing_executable_names_the_stage(tmp_path: Path) -> None:
    configuration = GatewayConfiguration(
        codex=CodexSettings(executable=str(tmp_path / "absent-codex"))
    )

    with pytest.raises(GatewayConfigurationError, match="stage drafting"):
        build_gateway(
            Stage.DRAFTING,
            lecture_id=LECTURE_ID,
            configuration=configuration,
            egress_policy=ALLOWING,
        )


def test_codex_executable_override_wins_over_the_configuration(
    codex_stub, tmp_path: Path
) -> None:
    configuration = GatewayConfiguration(
        codex=CodexSettings(executable=str(tmp_path / "absent-codex"))
    )

    gateway = build_gateway(
        Stage.SEMANTIC,
        lecture_id=LECTURE_ID,
        configuration=configuration,
        egress_policy=ALLOWING,
        codex_executable_override=str(codex_stub.executable),
    )

    assert isinstance(gateway, CodexCliAdapter)


@pytest.mark.parametrize(
    "broken_setting",
    [
        {"completion_executable": "absent-completion"},
        {"tokenize_executable": "absent-tokenize"},
        {"model_path": "absent-model.gguf"},
        {"model_path": "https://example.invalid/model.gguf"},
    ],
    ids=["completion", "tokenize", "model-file", "model-url"],
)
def test_llama_cpp_with_a_broken_setting_is_a_configuration_error(
    llama_stubs, tmp_path: Path, broken_setting: dict[str, str]
) -> None:
    resolved_setting = {
        key: str(tmp_path / value) if "://" not in value else value
        for key, value in broken_setting.items()
    }

    with pytest.raises(GatewayConfigurationError):
        build_gateway(
            Stage.CRITIC,
            lecture_id=LECTURE_ID,
            configuration=_llama_configuration(llama_stubs, **resolved_setting),
            egress_policy=REFUSING,
        )

    assert llama_stubs.control.call_names() == []


def test_llama_cpp_construction_contacts_nothing_and_skips_the_egress_check(
    llama_stubs,
) -> None:
    gateway = build_gateway(
        Stage.CRITIC,
        lecture_id=LECTURE_ID,
        configuration=_llama_configuration(llama_stubs),
        egress_policy=REFUSING,
    )

    assert isinstance(gateway, LlamaCppAdapter)
    assert llama_stubs.control.calls("llama-completion") == []
    assert llama_stubs.control.calls("llama-tokenize") == []


def _write_mixed_configuration(directory: Path, codex_stub, llama_stubs) -> Path:
    path = directory / "mixed-gateway.yaml"
    path.write_text(
        "default_adapter: codex\n"
        "stages:\n  critic: llama-cpp\n"
        f"codex:\n  executable: {codex_stub.executable}\n"
        "llama_cpp:\n"
        f"  completion_executable: {llama_stubs.completion_executable}\n"
        f"  tokenize_executable: {llama_stubs.tokenize_executable}\n"
        f"  model_path: {llama_stubs.model_path}\n"
        "  model_family: stub-family\n",
        encoding="utf-8",
    )
    return path


def test_preflight_refuses_the_codex_stage_of_an_unflagged_lecture(
    codex_stub, llama_stubs, tmp_path: Path
) -> None:
    configuration_path = _write_mixed_configuration(tmp_path, codex_stub, llama_stubs)

    with pytest.raises(RemoteEgressRefusedError, match="stage drafting"):
        preflight_stage_gateways(
            [Stage.CRITIC, Stage.DRAFTING],
            lecture_id=LECTURE_ID,
            environ={CONFIG_PATH_ENV: str(configuration_path)},
            local_lectures_path=tmp_path / "absent-local-lectures.yaml",
        )

    assert codex_stub.control.calls("codex") == []
    assert llama_stubs.control.call_names() == []


def test_preflight_accepts_a_local_only_selection_for_an_unflagged_lecture(
    codex_stub, llama_stubs, tmp_path: Path
) -> None:
    configuration_path = _write_mixed_configuration(tmp_path, codex_stub, llama_stubs)

    preflight_stage_gateways(
        [Stage.CRITIC, Stage.CRITIC],
        lecture_id=LECTURE_ID,
        environ={CONFIG_PATH_ENV: str(configuration_path)},
        local_lectures_path=tmp_path / "absent-local-lectures.yaml",
    )


def test_preflight_honors_the_explicit_environment_over_the_process_environment(
    codex_stub, llama_stubs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration_path = _write_mixed_configuration(tmp_path, codex_stub, llama_stubs)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "absent-gateway.yaml"))

    preflight_stage_gateways(
        [Stage.CRITIC],
        lecture_id=LECTURE_ID,
        environ={CONFIG_PATH_ENV: str(configuration_path)},
        local_lectures_path=tmp_path / "absent-local-lectures.yaml",
    )


def test_executable_path_with_an_embedded_nul_is_a_configuration_error() -> None:
    configuration = GatewayConfiguration(codex=CodexSettings(executable="codex\x00x"))

    with pytest.raises(GatewayConfigurationError, match="stage drafting"):
        build_gateway(
            Stage.DRAFTING,
            lecture_id=LECTURE_ID,
            configuration=configuration,
            egress_policy=ALLOWING,
        )
