"""Gateway configuration resolution: YAML, environment override, fail-closed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lecture_study.llm import config
from lecture_study.llm.config import (
    ADAPTER_OVERRIDE_ENV,
    CONFIG_PATH_ENV,
    DEFAULT_GATEWAY_CONFIG_PATH,
    load_gateway_configuration,
)
from lecture_study.llm.gateway import AdapterName, GatewayConfigurationError, Stage

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIGURATION = REPOSITORY_ROOT / "config" / "llm-gateway.example.yaml"

LLAMA_SECTION = """
llama_cpp:
  completion_executable: bin/llama-completion
  tokenize_executable: bin/llama-tokenize
  model_path: models/example-00001-of-00002.gguf
  model_family: stub-family
"""


@pytest.fixture(autouse=True)
def isolated_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    working_directory = tmp_path / "work"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    return working_directory


def _write_configuration(directory: Path, text: str) -> Path:
    path = directory / "gateway.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _environ_for(path: Path, **extra: str) -> dict[str, str]:
    return {CONFIG_PATH_ENV: str(path), **extra}


def _adapters_by_stage(environ: dict[str, str]) -> dict[Stage, AdapterName]:
    configuration = load_gateway_configuration(environ)
    return {stage: configuration.adapter_for(stage) for stage in Stage}


def test_default_path_is_the_git_ignored_local_copy() -> None:
    assert DEFAULT_GATEWAY_CONFIG_PATH == Path("config/llm-gateway.local.yaml")


def test_no_file_and_empty_environment_resolve_every_stage_to_codex() -> None:
    adapters = _adapters_by_stage({})

    assert set(adapters) == set(Stage)
    assert set(adapters.values()) == {AdapterName.CODEX}


def test_default_file_in_the_working_directory_is_read(
    isolated_working_directory: Path,
) -> None:
    default_path = isolated_working_directory / DEFAULT_GATEWAY_CONFIG_PATH
    default_path.parent.mkdir()
    default_path.write_text(
        "default_adapter: llama-cpp\n" + LLAMA_SECTION, encoding="utf-8"
    )

    configuration = load_gateway_configuration({})

    assert configuration.default_adapter is AdapterName.LLAMA_CPP


def test_stage_entry_overrides_the_yaml_default(tmp_path: Path) -> None:
    path = _write_configuration(
        tmp_path,
        "default_adapter: llama-cpp\nstages:\n  critic: codex\n" + LLAMA_SECTION,
    )

    adapters = _adapters_by_stage(_environ_for(path))

    assert adapters[Stage.CRITIC] is AdapterName.CODEX
    other_stages = set(Stage) - {Stage.CRITIC}
    assert {adapters[stage] for stage in other_stages} == {AdapterName.LLAMA_CPP}


def test_environment_override_clears_per_stage_entries(tmp_path: Path) -> None:
    path = _write_configuration(
        tmp_path,
        "default_adapter: codex\nstages:\n  critic: codex\n" + LLAMA_SECTION,
    )

    adapters = _adapters_by_stage(
        _environ_for(path, **{ADAPTER_OVERRIDE_ENV: "llama-cpp"})
    )

    assert set(adapters.values()) == {AdapterName.LLAMA_CPP}


def test_environment_override_can_force_codex_over_a_local_default(
    tmp_path: Path,
) -> None:
    path = _write_configuration(
        tmp_path, "default_adapter: llama-cpp\n" + LLAMA_SECTION
    )

    adapters = _adapters_by_stage(_environ_for(path, **{ADAPTER_OVERRIDE_ENV: "codex"}))

    assert set(adapters.values()) == {AdapterName.CODEX}


def test_empty_environment_override_is_ignored(tmp_path: Path) -> None:
    path = _write_configuration(
        tmp_path, "default_adapter: llama-cpp\n" + LLAMA_SECTION
    )

    configuration = load_gateway_configuration(
        _environ_for(path, **{ADAPTER_OVERRIDE_ENV: ""})
    )

    assert configuration.default_adapter is AdapterName.LLAMA_CPP


def test_unknown_environment_override_fails_closed_and_lists_valid_names() -> None:
    with pytest.raises(GatewayConfigurationError) as raised:
        load_gateway_configuration({ADAPTER_OVERRIDE_ENV: "bogus"})

    assert '"llama-cpp"' in str(raised.value)
    assert '"codex"' in str(raised.value)


def test_llama_override_without_llama_settings_fails_closed() -> None:
    with pytest.raises(GatewayConfigurationError, match="llama_cpp"):
        load_gateway_configuration({ADAPTER_OVERRIDE_ENV: "llama-cpp"})


def test_llama_selected_in_yaml_without_llama_settings_fails_closed(
    tmp_path: Path,
) -> None:
    path = _write_configuration(tmp_path, "stages:\n  exams: llama-cpp\n")

    with pytest.raises(GatewayConfigurationError, match="llama_cpp"):
        load_gateway_configuration(_environ_for(path))


@pytest.mark.parametrize(
    "text",
    [
        "unexpected_key: 1\n",
        "stages:\n  not_a_stage: codex\n",
        "default_adapter: bogus\n",
        "stages:\n  critic: bogus\n",
        "- a\n- list\n",
        "default_adapter: [unterminated\n",
        "codex:\n  executable: ''\n",
        "codex:\n  unexpected: 1\n",
    ],
    ids=[
        "unknown-top-level-key",
        "unknown-stage-key",
        "unknown-default-adapter",
        "unknown-stage-adapter",
        "non-mapping-root",
        "invalid-yaml",
        "empty-executable",
        "unknown-codex-key",
    ],
)
def test_invalid_configuration_file_fails_closed(tmp_path: Path, text: str) -> None:
    path = _write_configuration(tmp_path, text)

    with pytest.raises(GatewayConfigurationError):
        load_gateway_configuration(_environ_for(path))


def test_unreadable_bytes_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "gateway.yaml"
    path.write_bytes(b"\xff\xfe\x00invalid")

    with pytest.raises(GatewayConfigurationError):
        load_gateway_configuration(_environ_for(path))


def test_explicit_missing_configuration_path_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "absent.yaml"

    with pytest.raises(GatewayConfigurationError, match="absent.yaml"):
        load_gateway_configuration(_environ_for(missing))


def test_empty_configuration_file_resolves_to_defaults(tmp_path: Path) -> None:
    path = _write_configuration(tmp_path, "")

    adapters = _adapters_by_stage(_environ_for(path))

    assert set(adapters.values()) == {AdapterName.CODEX}


def test_validation_message_names_field_locations_only(tmp_path: Path) -> None:
    path = _write_configuration(tmp_path, "stages:\n  critic: SECRET-LOOKING-VALUE\n")

    with pytest.raises(GatewayConfigurationError) as raised:
        load_gateway_configuration(_environ_for(path))

    assert "stages.critic" in str(raised.value)
    assert "SECRET-LOOKING-VALUE" not in str(raised.value)


def test_example_configuration_loads_with_codex_default_and_llama_section() -> None:
    configuration = load_gateway_configuration(
        {CONFIG_PATH_ENV: str(EXAMPLE_CONFIGURATION)}
    )

    assert configuration.default_adapter is AdapterName.CODEX
    assert dict(configuration.stages) == {}
    assert configuration.remote_llm_allowed_lecture_ids == ()
    assert configuration.llama_cpp is not None
    assert configuration.llama_cpp.model_path.endswith("-00001-of-00003.gguf")


def test_example_configuration_holds_no_absolute_path() -> None:
    lines = EXAMPLE_CONFIGURATION.read_text(encoding="utf-8").splitlines()

    values = [line.split(":", 1)[1].strip() for line in lines if ":" in line]
    assert not [value for value in values if value.startswith("/")]


def test_allowed_lecture_ids_are_listed_and_validated(tmp_path: Path) -> None:
    path = _write_configuration(
        tmp_path, 'remote_llm_allowed_lecture_ids: ["10001", "lec-2"]\n'
    )

    configuration = load_gateway_configuration(_environ_for(path))

    assert configuration.remote_llm_allowed_lecture_ids == ("10001", "lec-2")


def test_allowed_lecture_id_with_a_slash_is_rejected(tmp_path: Path) -> None:
    path = _write_configuration(tmp_path, 'remote_llm_allowed_lecture_ids: ["a/b"]\n')

    with pytest.raises(GatewayConfigurationError):
        load_gateway_configuration(_environ_for(path))


def test_dangling_default_symlink_fails_closed_instead_of_selecting_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "llm-gateway.local.yaml"
    link.symlink_to(tmp_path / "absent-target.yaml")
    monkeypatch.setattr(config, "DEFAULT_GATEWAY_CONFIG_PATH", link)

    with pytest.raises(GatewayConfigurationError, match="unreadable"):
        load_gateway_configuration({})


def test_dangling_explicit_symlink_fails_closed(tmp_path: Path) -> None:
    link = tmp_path / "gateway.yaml"
    link.symlink_to(tmp_path / "absent-target.yaml")

    with pytest.raises(GatewayConfigurationError, match="unreadable"):
        load_gateway_configuration(_environ_for(link))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unreadable_parent_directory_fails_closed_instead_of_selecting_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked_directory = tmp_path / "locked"
    locked_directory.mkdir()
    monkeypatch.setattr(
        config, "DEFAULT_GATEWAY_CONFIG_PATH", locked_directory / "gateway.yaml"
    )
    locked_directory.chmod(0o000)
    try:
        with pytest.raises(
            GatewayConfigurationError, match="unreadable.*PermissionError"
        ):
            load_gateway_configuration({})
    finally:
        locked_directory.chmod(0o700)


def test_absent_default_file_still_resolves_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DEFAULT_GATEWAY_CONFIG_PATH", tmp_path / "absent.yaml")

    assert load_gateway_configuration({}).default_adapter is AdapterName.CODEX


@pytest.mark.parametrize(
    ("text", "repeated_path"),
    [
        ("default_adapter: codex\ndefault_adapter: codex\n", "default_adapter"),
        ("stages:\n  exams: codex\n  exams: codex\n", "stages.exams"),
        (
            "remote_llm_allowed_lecture_ids: ['1']\n"
            "remote_llm_allowed_lecture_ids: ['2']\n",
            "remote_llm_allowed_lecture_ids",
        ),
    ],
)
def test_duplicate_yaml_keys_fail_closed_naming_only_the_key_path(
    tmp_path: Path, text: str, repeated_path: str
) -> None:
    path = _write_configuration(tmp_path, text)

    with pytest.raises(GatewayConfigurationError, match="repeats keys") as raised:
        load_gateway_configuration(_environ_for(path))

    assert repeated_path in str(raised.value)


@pytest.mark.parametrize(
    ("text", "type_name"),
    [
        ("released: 2001-13-45\n", "ValueError"),
        ("a: " + "[" * 5000 + "]" * 5000 + "\n", "RecursionError"),
        ("x: !!bool .nan\n", "KeyError"),
        ("x: !!timestamp 0x\n", "AttributeError"),
    ],
    ids=[
        "calendar-invalid-timestamp",
        "deep-nesting",
        "invalid-bool-tag",
        "invalid-timestamp-tag",
    ],
)
def test_unparseable_yaml_scalars_fail_closed_without_content(
    tmp_path: Path, text: str, type_name: str
) -> None:
    path = _write_configuration(tmp_path, text)

    with pytest.raises(GatewayConfigurationError, match=type_name) as raised:
        load_gateway_configuration(_environ_for(path))

    assert "2001-13-45" not in str(raised.value)
