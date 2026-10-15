"""Gateway configuration: which adapter serves which pipeline stage.

One resolver is shared by the CLI preflight and by every script, so the two
validation layers cannot drift apart (D-12). Resolution precedence, highest
first: the ``LECTURE_STUDY_LLM_ADAPTER`` environment override (which also
replaces every per-stage entry, D-20), the per-stage entry of the YAML file,
the YAML ``default_adapter``, and the built-in default (codex).

This module reads one YAML file and nothing else. It spawns no process and
contacts nothing; executable and model existence are checked by the adapter
factory. Every malformed input fails closed with ``GatewayConfigurationError``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticCustomError

from lecture_study.domain.models import SafeIdentifier
from lecture_study.llm.gateway import AdapterName, GatewayConfigurationError, Stage
from lecture_study.ops.strict_yaml import duplicated_key_paths

DEFAULT_GATEWAY_CONFIG_PATH = Path("config/llm-gateway.local.yaml")
ADAPTER_OVERRIDE_ENV = "LECTURE_STUDY_LLM_ADAPTER"
CONFIG_PATH_ENV = "LECTURE_STUDY_LLM_CONFIG"

_UNLOCATED_ERROR = "<configuration>"
_OVERRIDE_SOURCE = f"{ADAPTER_OVERRIDE_ENV} override"

NonEmptyText = Annotated[str, Field(min_length=1)]


class CodexSettings(BaseModel):
    """Settings of the remote codex adapter."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    executable: NonEmptyText = "codex"


class LlamaCppSettings(BaseModel):
    """Settings of the local llama.cpp adapter."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    completion_executable: NonEmptyText
    tokenize_executable: NonEmptyText
    model_path: NonEmptyText
    model_family: NonEmptyText


class GatewayConfiguration(BaseModel):
    """Validated gateway configuration with per-stage adapter selection."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    default_adapter: AdapterName = AdapterName.CODEX
    stages: Mapping[Stage, AdapterName] = Field(default_factory=dict)
    remote_llm_allowed_lecture_ids: tuple[SafeIdentifier, ...] = ()
    codex: CodexSettings = Field(default_factory=CodexSettings)
    llama_cpp: LlamaCppSettings | None = None

    @model_validator(mode="after")
    def require_llama_cpp_settings_when_selected(self) -> Self:
        if self.llama_cpp is None and AdapterName.LLAMA_CPP in self._selected():
            raise PydanticCustomError(
                "llama_cpp_section_missing",
                "llama-cpp is selected but the llama_cpp section is absent",
            )
        return self

    def adapter_for(self, stage: Stage) -> AdapterName:
        return self.stages.get(stage, self.default_adapter)

    def _selected(self) -> set[AdapterName]:
        return {self.default_adapter, *self.stages.values()}


def load_gateway_configuration(environ: Mapping[str, str]) -> GatewayConfiguration:
    """Resolve the configuration from the YAML file and the environment."""

    configuration = _read_configuration_file(environ)
    requested_adapter = environ.get(ADAPTER_OVERRIDE_ENV, "")
    if not requested_adapter:
        return configuration
    return _override_every_stage(configuration, requested_adapter)


def _read_configuration_file(environ: Mapping[str, str]) -> GatewayConfiguration:
    explicit_path = environ.get(CONFIG_PATH_ENV, "")
    path = Path(explicit_path) if explicit_path else DEFAULT_GATEWAY_CONFIG_PATH
    if _configuration_file_present(path):
        return _parse_configuration_file(path)
    if explicit_path:
        raise GatewayConfigurationError(f"gateway configuration file not found: {path}")
    return GatewayConfiguration()


def _configuration_file_present(path: Path) -> bool:
    """True when something exists at ``path`` (a dangling symlink counts).

    Absence is exactly ``FileNotFoundError``. ``Path.exists`` is avoided: it
    swallows other ``OSError`` on newer Python versions and raises raw on
    older ones, so an unreadable parent could select the built-in default.
    """

    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise GatewayConfigurationError(
            f"gateway configuration unreadable at {path}: {type(error).__name__}"
        ) from error
    return True


def _parse_configuration_file(path: Path) -> GatewayConfiguration:
    try:
        text = path.read_text(encoding="utf-8")
        duplicated_keys = duplicated_key_paths(text)
        payload = yaml.safe_load(text)
    except (
        yaml.YAMLError,
        ValueError,
        RecursionError,
        KeyError,
        AttributeError,
        OSError,
    ) as error:
        # ValueError covers UnicodeDecodeError and calendar-invalid timestamp
        # scalars; RecursionError covers deeply nested documents; KeyError and
        # AttributeError cover explicit invalid tags such as ``!!bool .nan``
        # and ``!!timestamp 0x``.
        raise GatewayConfigurationError(
            f"gateway configuration unreadable at {path}: {type(error).__name__}"
        ) from error
    if duplicated_keys:
        raise GatewayConfigurationError(
            f"gateway configuration repeats keys in {path}: "
            + ", ".join(duplicated_keys)
        )
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise GatewayConfigurationError(
            f"gateway configuration root must be a mapping: {path}"
        )
    return _validate(payload, source=str(path))


def _override_every_stage(
    configuration: GatewayConfiguration, requested_adapter: str
) -> GatewayConfiguration:
    adapter = _parse_adapter_name(requested_adapter)
    payload = configuration.model_dump(mode="json")
    payload.update(default_adapter=adapter.value, stages={})
    return _validate(payload, source=_OVERRIDE_SOURCE)


def _parse_adapter_name(requested_adapter: str) -> AdapterName:
    try:
        return AdapterName(requested_adapter)
    except ValueError:
        valid_names = ", ".join(f'"{name.value}"' for name in AdapterName)
        raise GatewayConfigurationError(
            f"{ADAPTER_OVERRIDE_ENV} must be one of {valid_names}; "
            f"got {requested_adapter!r}"
        ) from None


def _validate(payload: Mapping[str, Any], *, source: str) -> GatewayConfiguration:
    try:
        return GatewayConfiguration.model_validate(payload, strict=False)
    except ValidationError as error:
        raise GatewayConfigurationError(
            f"invalid gateway configuration ({source}): {_summarize(error)}"
        ) from error


def _summarize(error: ValidationError) -> str:
    details = error.errors(
        include_url=False, include_context=False, include_input=False
    )
    return "; ".join(
        f"{_format_location(item['loc'])}: {item['type']}" for item in details
    )


def _format_location(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location) or _UNLOCATED_ERROR
