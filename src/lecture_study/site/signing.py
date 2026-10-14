"""The site's session signing secret (SITE-04).

Sessions are signed with a secret that exists only on the server, mixed
with the stored password hash of the person the token was issued to. The
hash alone must never be the key: it is a verifier, and the whole point
of paying 240.000 PBKDF2 iterations is that holding it does not let you
authenticate. Keyed with a separate secret, reading the allowlist is not
enough to forge a cookie -- an attacker needs both files.

The secret is a file on the server, in a directory of its own, written
by the deploy script and deliberately kept apart from the allowlist so
that one stray copy of either carries only half of what forging a cookie
takes. A missing secret denies everyone loudly, exactly as a missing
allowlist does: this file is a credential, and the site cannot prove
anybody's identity without it.
"""

from __future__ import annotations

import os
from pathlib import Path

from lecture_study.site.access import CredentialsError

SIGNING_SECRET_PATH = Path(
    os.environ.get(
        "LECTURE_STUDY_SITE_SESSION_KEY", "config/session-key/site-session-key"
    )
)

MINIMUM_SECRET_BYTES = 32


def read_signing_secret(path: Path | None = None) -> bytes:
    """Read the session signing secret. Raises when it is absent or too
    short to be worth signing with."""
    resolved_path = SIGNING_SECRET_PATH if path is None else path
    try:
        secret = resolved_path.read_bytes().strip()
    except OSError as error:
        raise CredentialsError(
            f"nenhum segredo de sessão em {resolved_path}: o site não pode "
            "emitir nem validar sessões sem ele"
        ) from error
    if len(secret) < MINIMUM_SECRET_BYTES:
        raise CredentialsError(
            f"o segredo de sessão em {resolved_path} é curto demais: são "
            f"necessários ao menos {MINIMUM_SECRET_BYTES} bytes"
        )
    return secret
