"""Child-process environment isolation tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lecture_study.ops import process_environment
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.quality.trust import QUALITY_HMAC_KEY_ENV

_PROJECT_ROOT = Path(__file__).parents[1]
_PRODUCTION_ROOT = _PROJECT_ROOT / "src" / "lecture_study"
_QUALITY_ENVIRONMENT_PREFIX = "LECTURE_STUDY_QUALITY_"
_EXTERNAL_PROCESS_FUNCTIONS = {
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "Popen"),
    ("subprocess", "run"),
}


def test_sanitized_environment_inherits_only_operational_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_environment = {
        "LANG": "pt_BR.UTF-8",
        "LC_ALL": "pt_BR.UTF-8",
        "LC_CTYPE": "pt_BR.UTF-8",
        "PATH": "/usr/bin",
        "TEMP": "/runtime/temp",
        "TMP": "/runtime/tmp",
        "TMPDIR": "/runtime/tmpdir",
        "HOME": "/runtime/home",
        "CUDA_VISIBLE_DEVICES": "0",
        "LECTURE_STUDY_PUBLIC_SETTING": "enabled",
        QUALITY_HMAC_KEY_ENV: "DO_NOT_INHERIT",
    }
    monkeypatch.setattr(process_environment.os, "environ", source_environment)

    sanitized_environment = sanitized_child_process_environment()

    assert sanitized_environment == {
        "LANG": "pt_BR.UTF-8",
        "LC_ALL": "pt_BR.UTF-8",
        "LC_CTYPE": "pt_BR.UTF-8",
        "PATH": "/usr/bin",
        "TEMP": "/runtime/temp",
        "TMP": "/runtime/tmp",
        "TMPDIR": "/runtime/tmpdir",
    }


def test_sanitized_environment_excludes_common_and_project_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_environment = {
        "PATH": "/custom/bin",
        "AWS_ACCESS_KEY_ID": "excluded",
        "AWS_SECRET_ACCESS_KEY": "excluded",
        "AWS_SESSION_TOKEN": "excluded",
        "DATABASE_URL": "excluded",
        "GITHUB_TOKEN": "excluded",
        "GOOGLE_APPLICATION_CREDENTIALS": "excluded",
        "OPENAI_API_KEY": "excluded",
        "SESSION_COOKIE": "excluded",
        "SSH_AUTH_SOCK": "excluded",
        "PORTAL_USER": "excluded",
        "PORTAL_PASS": "excluded",
        QUALITY_HMAC_KEY_ENV: "excluded",
        f"{_QUALITY_ENVIRONMENT_PREFIX}FUTURE_SIGNING_KEY": "excluded",
    }
    monkeypatch.setattr(process_environment.os, "environ", source_environment)

    assert sanitized_child_process_environment() == {"PATH": "/custom/bin"}


def test_sanitized_environment_accepts_safe_explicit_child_values() -> None:
    explicit_environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "SAFE_CHILD_MODE": "enabled",
        "SERVICE_TOKEN": "excluded",
        "LECTURE_STUDY_QUALITY_SIGNING_KEY": "excluded",
    }

    assert sanitized_child_process_environment(explicit_environment) == {
        "LANG": "C",
        "LC_ALL": "C",
        "SAFE_CHILD_MODE": "enabled",
    }


def test_resolve_trusted_executable_returns_absolute_regular_file(
    tmp_path: Path,
) -> None:
    executable_path = tmp_path / "trusted-tool"
    executable_path.write_text("#!/bin/sh\n")
    executable_path.chmod(0o700)

    resolved_path = resolve_trusted_executable(executable_path)

    assert resolved_path == str(executable_path)


def test_resolve_trusted_executable_returns_canonical_symlink_target(
    tmp_path: Path,
) -> None:
    executable_path = tmp_path / "trusted-tool"
    executable_path.write_text("#!/bin/sh\n")
    executable_path.chmod(0o700)
    symlink_path = tmp_path / "linked-tool"
    symlink_path.symlink_to(executable_path)

    assert resolve_trusted_executable(symlink_path) == str(executable_path.resolve())


def test_resolve_trusted_executable_rejects_broken_symlink(tmp_path: Path) -> None:
    symlink_path = tmp_path / "linked-tool"
    symlink_path.symlink_to(tmp_path / "missing-tool")

    with pytest.raises(FileNotFoundError, match="does not resolve"):
        resolve_trusted_executable(symlink_path)


def test_resolve_trusted_executable_rejects_non_regular_file(
    tmp_path: Path,
) -> None:
    directory_path = tmp_path / "tool-directory"
    directory_path.mkdir()

    with pytest.raises(OSError, match="regular file"):
        resolve_trusted_executable(directory_path)


def test_resolve_trusted_executable_uses_explicit_search_path(
    tmp_path: Path,
) -> None:
    executable_path = tmp_path / "trusted-tool"
    executable_path.write_text("#!/bin/sh\n")
    executable_path.chmod(0o700)

    resolved_path = resolve_trusted_executable(
        "trusted-tool",
        search_path=str(tmp_path),
    )

    assert resolved_path == str(executable_path)


def test_all_external_process_calls_use_shared_environment_helper() -> None:
    call_sites = _external_process_call_sites()
    unprotected_call_sites = [
        call_site
        for call_site, call in call_sites
        if not _uses_sanitized_environment(call)
    ]

    assert call_sites
    assert unprotected_call_sites == []


def _external_process_call_sites() -> list[tuple[str, ast.Call]]:
    call_sites: list[tuple[str, ast.Call]] = []
    for source_path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        relative_path = source_path.relative_to(_PROJECT_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_external_process_call(node):
                call_sites.append((f"{relative_path}:{node.lineno}", node))
    return call_sites


def _is_external_process_call(call: ast.Call) -> bool:
    function = call.func
    return (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and (function.value.id, function.attr) in _EXTERNAL_PROCESS_FUNCTIONS
    )


def _uses_sanitized_environment(call: ast.Call) -> bool:
    environment_keywords = [
        keyword.value for keyword in call.keywords if keyword.arg == "env"
    ]
    if len(environment_keywords) != 1:
        return False
    environment_value = environment_keywords[0]
    return (
        isinstance(environment_value, ast.Call)
        and isinstance(environment_value.func, ast.Name)
        and environment_value.func.id == "sanitized_child_process_environment"
        and len(environment_value.args) <= 1
        and not environment_value.keywords
    )
