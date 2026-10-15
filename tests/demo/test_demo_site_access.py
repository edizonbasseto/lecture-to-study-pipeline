"""Demo site access material: pure logic and the prepare-access command."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from types import ModuleType

import pytest

from lecture_study.demo.site_access import (
    DEMO_EMAIL,
    SiteAccessPaths,
    certificate_command,
    credentials_line,
    environment_file_text,
    new_demo_password,
    new_session_secret,
    require_inside,
)
from lecture_study.site.access import verify_password

OWNER_ONLY = 0o600
OWNER_ONLY_DIRECTORY = 0o700


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_credentials_line_holds_a_verifiable_hash_and_not_the_password() -> None:
    password = new_demo_password()

    line = credentials_line(DEMO_EMAIL, password)

    email, _, password_hash = line.partition(":")
    assert email == DEMO_EMAIL
    assert "\n" not in line
    assert verify_password(password, password_hash)
    assert password not in line


def test_certificate_command_covers_loopback_for_two_days(tmp_path: Path) -> None:
    paths = SiteAccessPaths(tmp_path)

    command = certificate_command("openssl", paths)

    assert command[:3] == ["openssl", "req", "-x509"]
    assert command[command.index("-days") + 1] == "2"
    assert command[command.index("-subj") + 1] == "/CN=127.0.0.1"
    assert (
        command[command.index("-addext") + 1]
        == "subjectAltName=IP:127.0.0.1,DNS:localhost"
    )
    assert command[command.index("-keyout") + 1] == str(paths.private_key)
    assert command[command.index("-out") + 1] == str(paths.certificate)


def test_environment_file_text_has_exactly_the_two_sign_in_lines() -> None:
    text = environment_file_text(DEMO_EMAIL, "senha-de-teste")

    assert text.splitlines() == [
        f"DEMO_SITE_EMAIL={DEMO_EMAIL}",
        "DEMO_SITE_PASSWORD=senha-de-teste",
    ]


def test_new_session_secret_is_long_and_never_repeats() -> None:
    first, second = new_session_secret(), new_session_secret()

    assert len(first) >= 32
    assert first != second


def test_require_inside_refuses_a_path_that_leaves_the_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    assert require_inside(root / "demo", root) == (root / "demo").resolve()
    with pytest.raises(ValueError):
        require_inside(root / ".." / "elsewhere", root)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not installed")
def test_prepare_access_writes_owner_only_files_and_keeps_the_password_in_env(
    demo_site: ModuleType, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspace"
    directory = workspace_root / "demo" / "site-access"

    paths = demo_site.prepare_access(directory, workspace_root=workspace_root)

    generated = [
        paths.credentials,
        paths.session_key,
        paths.environment_file,
        paths.certificate,
        paths.private_key,
    ]
    assert all(mode_of(path) == OWNER_ONLY for path in generated)
    assert mode_of(directory) == OWNER_ONLY_DIRECTORY
    password = demo_site.read_sign_in_values(paths)[1]
    assert password not in paths.credentials.read_text(encoding="utf-8")


def test_prepare_access_refuses_a_directory_outside_the_workspace(
    demo_site: ModuleType, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(ValueError):
        demo_site.prepare_access(tmp_path / "elsewhere", workspace_root=workspace_root)

    assert not (tmp_path / "elsewhere").exists()
