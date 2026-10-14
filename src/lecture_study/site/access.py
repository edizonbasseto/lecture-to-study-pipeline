"""Email allowlist for the study site (SITE-04).

Access is granted to named people, never to a shared secret: the
credentials file maps one authorized email address to one password hash,
and it is read on every check so that removing a line revokes that person
immediately, with no redeploy and no restart (ADR 0029).

The file lives outside the container image and outside version control.
Its format is one `email:hash` pair per line, `#` starting a comment, where
the hash is produced by `hash_password` below. An optional third field,
`email:hash:/prefix-a,/prefix-b`, restricts that person to those path
prefixes; without it the person reaches the whole site.
"""

import hashlib
import hmac
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from posixpath import normpath

CREDENTIALS_PATH = Path(
    os.environ.get("LECTURE_STUDY_SITE_CREDENTIALS", "config/site-credentials.txt")
)

_HASH_NAME = "sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16


class CredentialsError(RuntimeError):
    """Raised when the credentials file is missing or unreadable."""


@dataclass(frozen=True, slots=True)
class Credential:
    """One authorized person."""

    email: str
    password_hash: str
    allowed_prefixes: tuple[str, ...] = ()

    @property
    def is_restricted(self) -> bool:
        return bool(self.allowed_prefixes)

    def permits(self, request_path: str) -> bool:
        """Whether this person may open the path. An unrestricted person
        may open everything; a restricted one only the listed prefixes,
        matched on whole path segments, over a path that is already in its
        normalized spelling so a dot-segment cannot walk out of a prefix.
        """
        if not self.is_restricted:
            return True
        if normpath(request_path) != request_path.rstrip("/") and request_path != "/":
            return False
        return any(
            request_path == prefix or request_path.startswith(prefix + "/")
            for prefix in self.allowed_prefixes
        )

    @property
    def landing_path(self) -> str:
        """Where a restricted person is sent instead of the library index."""
        return self.allowed_prefixes[0] + "/index.html"


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash one password for storage in the credentials file."""
    resolved_salt = os.urandom(_SALT_BYTES) if salt is None else salt
    derived = hashlib.pbkdf2_hmac(
        _HASH_NAME, password.encode("utf-8"), resolved_salt, _ITERATIONS
    )
    return f"pbkdf2_{_HASH_NAME}${_ITERATIONS}${resolved_salt.hex()}${derived.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check of one password against one stored hash."""
    try:
        algorithm, iterations, salt_hex, expected_hex = password_hash.split("$")
        hash_name = algorithm.removeprefix("pbkdf2_")
        derived = hashlib.pbkdf2_hmac(
            hash_name,
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived.hex(), expected_hex)


def read_credentials(path: Path | None = None) -> tuple[Credential, ...]:
    """Read the allowlist. Raises when the file is absent: an unreadable
    allowlist must deny everyone loudly, never admit everyone quietly."""
    resolved_path = CREDENTIALS_PATH if path is None else path
    if not resolved_path.is_file():
        raise CredentialsError(
            f"nenhum arquivo de credenciais em {resolved_path}: o site não pode "
            "autorizar ninguém sem a lista de acessos"
        )
    try:
        text = resolved_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CredentialsError(
            f"não foi possível ler o arquivo de credenciais em {resolved_path}"
        ) from error
    credentials = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        email, separator, remainder = entry.partition(":")
        password_hash, _, scope = remainder.partition(":")
        if not separator or not email.strip() or not password_hash.strip():
            continue
        credentials.append(
            Credential(
                email=normalize_email(email),
                password_hash=password_hash.strip(),
                allowed_prefixes=_parse_prefixes(scope),
            )
        )
    return tuple(credentials)


def _parse_prefixes(scope: str) -> tuple[str, ...]:
    """Comma-separated absolute path prefixes, trailing slash dropped."""
    prefixes = (item.strip().rstrip("/") for item in scope.split(","))
    return tuple(prefix for prefix in prefixes if prefix.startswith("/"))


def normalize_email(email: str) -> str:
    """The one spelling of an address the whole site compares against.

    Composed form matters: an iPhone keyboard and a file edited on macOS
    both produce decomposed accents, and an address written one way would
    otherwise never match the same address written the other.
    """
    return unicodedata.normalize("NFC", email.strip().lower())


def credential_for(
    email: str, credentials: tuple[Credential, ...]
) -> Credential | None:
    """Return the allowlisted credential for one address, or None.

    Comparison runs over bytes because `compare_digest` raises TypeError
    on non-ASCII text, and an accented address reaching it turned every
    request into an unhandled 500.
    """
    wanted = normalize_email(email).encode("utf-8")
    found: Credential | None = None
    for credential in credentials:
        if hmac.compare_digest(credential.email.encode("utf-8"), wanted):
            found = credential
    return found


def verify_credentials(
    email: str, password: str, credentials: tuple[Credential, ...]
) -> Credential | None:
    """Return the matching credential, or None when access is denied.

    Every candidate is checked even after a match is found, so a denied
    attempt costs the same as an accepted one regardless of where the
    address sits in the file.
    """
    authorized: Credential | None = None
    wanted = normalize_email(email).encode("utf-8")
    for credential in credentials:
        matches_email = hmac.compare_digest(credential.email.encode("utf-8"), wanted)
        matches_password = verify_password(password, credential.password_hash)
        if matches_email and matches_password:
            authorized = credential
    return authorized
