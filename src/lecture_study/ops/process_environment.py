"""Environment isolation for external child processes."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Mapping
from pathlib import Path

_INHERITED_OPERATIONAL_VARIABLES = frozenset(
    {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "TEMP",
        "TMP",
        "TMPDIR",
    }
)
_SENSITIVE_ENVIRONMENT_NAMES = frozenset(
    {
        "PORTAL_PASS",
        "PORTAL_USER",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "DATABASE_URL",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "SSH_AUTH_SOCK",
    }
)
_SENSITIVE_ENVIRONMENT_PREFIXES = (
    "PORTAL_",
    "LECTURE_STUDY_QUALITY_",
)
_SENSITIVE_ENVIRONMENT_SUFFIXES = (
    "_API_KEY",
    "_COOKIE",
    "_CREDENTIALS",
    "_PASSWORD",
    "_SECRET",
    "_SESSION",
    "_TOKEN",
)


def sanitized_child_process_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an explicit child environment without ambient credentials."""

    source_environment = (
        _inherited_operational_environment() if environment is None else environment
    )
    return {
        name: value
        for name, value in source_environment.items()
        if not _is_sensitive_environment_name(name)
    }


def resolve_trusted_executable(
    executable: str | Path,
    *,
    search_path: str | None = None,
) -> str:
    """Resolve an executable to its canonical regular executable target."""
    executable_path = Path(executable)
    if executable_path.parent == Path("."):
        matched_path = shutil.which(
            str(executable_path),
            path=search_path,
        )
        if matched_path is None:
            raise FileNotFoundError(f"executable not found on PATH: {executable_path}")
        executable_path = Path(matched_path)

    try:
        absolute_path = executable_path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as error:
        raise FileNotFoundError(
            f"executable does not resolve to a file: {executable_path}"
        ) from error
    executable_status = absolute_path.lstat()
    if not stat.S_ISREG(executable_status.st_mode):
        raise OSError(f"executable must be a regular file: {absolute_path}")
    if not os.access(absolute_path, os.X_OK):
        raise PermissionError(f"executable is not executable: {absolute_path}")
    return str(absolute_path)


def _inherited_operational_environment() -> dict[str, str]:
    return {
        name: os.environ[name]
        for name in _INHERITED_OPERATIONAL_VARIABLES
        if name in os.environ
    }


def _is_sensitive_environment_name(name: str) -> bool:
    uppercase_name = name.upper()
    return (
        uppercase_name in _SENSITIVE_ENVIRONMENT_NAMES
        or uppercase_name.startswith(_SENSITIVE_ENVIRONMENT_PREFIXES)
        or uppercase_name.endswith(_SENSITIVE_ENVIRONMENT_SUFFIXES)
    )
