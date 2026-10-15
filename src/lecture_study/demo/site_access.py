"""Access material for the locally served demo site (ADR 0047).

The demo site is served over local HTTPS because its session cookies are
`Secure` with the `__Host-` prefix. Its sign-in values, session secret and
throwaway certificate are generated at run time, never committed; this module
holds only the pure logic (names, formats, command line). Writing the files
and running the certificate command belongs to ``scripts/demo_site.py``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from lecture_study.site.access import hash_password

__all__ = [
    "DEMO_EMAIL",
    "DEMO_SCHEDULE_PATH",
    "DEMO_SITE_URL",
    "SITE_ACCESS_DIR",
    "SiteAccessPaths",
    "certificate_command",
    "credentials_line",
    "environment_file_text",
    "new_demo_password",
    "new_session_secret",
    "require_inside",
]

SITE_ACCESS_DIR = Path("workspace/demo/site-access")
DEMO_SCHEDULE_PATH = Path("demo/site/academic-calendar.yaml")
DEMO_SITE_URL = "https://127.0.0.1:8443"
# Reserved example domain: the demo account is not a real person.
DEMO_EMAIL = "aluno.demo@example.com"

_PASSWORD_BYTES = 24
_SESSION_SECRET_BYTES = 48
_CERTIFICATE_SUBJECT = "/CN=127.0.0.1"
_CERTIFICATE_ALTERNATIVE_NAMES = "subjectAltName=IP:127.0.0.1,DNS:localhost"
_CERTIFICATE_KEY_BITS = 2048


@dataclass(frozen=True)
class SiteAccessPaths:
    """Where the generated access files live inside one directory."""

    directory: Path

    @property
    def credentials(self) -> Path:
        return self.directory / "site-credentials.txt"

    @property
    def session_key(self) -> Path:
        return self.directory / "site-session-key"

    @property
    def certificate(self) -> Path:
        return self.directory / "cert.pem"

    @property
    def private_key(self) -> Path:
        return self.directory / "key.pem"

    @property
    def environment_file(self) -> Path:
        return self.directory / ".env"


def credentials_line(email: str, password: str) -> str:
    """One allowlist line, `email:hash`; the password itself is not kept."""

    return f"{email}:{hash_password(password)}"


def new_demo_password() -> str:
    return secrets.token_urlsafe(_PASSWORD_BYTES)


def new_session_secret() -> bytes:
    return secrets.token_bytes(_SESSION_SECRET_BYTES)


def environment_file_text(email: str, password: str) -> str:
    """The `.env` content the sign-in check reads: exactly two lines."""

    return f"DEMO_SITE_EMAIL={email}\nDEMO_SITE_PASSWORD={password}\n"


def certificate_command(
    openssl: str, paths: SiteAccessPaths, *, valid_days: int = 2
) -> list[str]:
    """Argument list for a short-lived self-signed certificate for loopback."""

    return [
        openssl,
        "req",
        "-x509",
        "-newkey",
        f"rsa:{_CERTIFICATE_KEY_BITS}",
        "-nodes",
        "-keyout",
        str(paths.private_key),
        "-out",
        str(paths.certificate),
        "-days",
        str(valid_days),
        "-subj",
        _CERTIFICATE_SUBJECT,
        "-addext",
        _CERTIFICATE_ALTERNATIVE_NAMES,
    ]


def require_inside(path: Path, root: Path) -> Path:
    """The resolved `path`, or ValueError when it leaves the resolved `root`."""

    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(root.resolve()):
        raise ValueError(f"{path} is outside {root}")
    return resolved_path
