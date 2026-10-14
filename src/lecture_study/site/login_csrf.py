"""Login CSRF protection for the sign-in form (site hardening).

Without it, a third-party page could post its own credentials to the form
and sign a visitor in to an account the attacker controls (login CSRF).

Stateless signed double submit: the form page sets a random nonce in a
`__Host-` cookie -- which neither another site nor a sibling subdomain can
set -- and embeds an HMAC of that nonce, keyed with the server's signing
secret, as a hidden field. A submission is genuine only when the field is
the HMAC of the nonce cookie that came with it. An attacker can neither
plant the cookie nor compute the field for a nonce of their own choosing.
"""

import hashlib
import hmac
import re
import secrets
from typing import TypeGuard

LOGIN_CSRF_COOKIE = "__Host-pos_login_csrf"
LOGIN_CSRF_FIELD = "csrf_token"

_NONCE_BYTES = 32
# `secrets.token_urlsafe(32)` yields 43 URL-safe characters; anything else
# in the cookie did not come from this module and is never echoed back.
_WELL_FORMED_NONCE = re.compile(r"[A-Za-z0-9_-]{43}")
_TOKEN_DOMAIN = b"pos-login-csrf-v1."


def issue_login_nonce() -> str:
    """A fresh random nonce for one browser's login form cookie."""
    return secrets.token_urlsafe(_NONCE_BYTES)


def is_well_formed_nonce(candidate: str | None) -> TypeGuard[str]:
    """Whether a nonce cookie value could have been issued here, and is
    therefore safe to reuse and to echo back in a Set-Cookie header."""
    return candidate is not None and _WELL_FORMED_NONCE.fullmatch(candidate) is not None


def login_form_token(nonce: str, signing_secret: bytes) -> str:
    """The hidden-field value that proves a form was served for `nonce`."""
    message = _TOKEN_DOMAIN + nonce.encode("utf-8")
    return hmac.new(signing_secret, message, hashlib.sha256).hexdigest()


def is_genuine_login_submission(
    nonce: str | None, submitted_token: str | None, signing_secret: bytes
) -> bool:
    """Whether a login POST carries the token minted for its own nonce
    cookie under this server's signing secret."""
    if submitted_token is None or not is_well_formed_nonce(nonce):
        return False
    expected = login_form_token(nonce, signing_secret)
    # Compared as bytes: a form field may carry any character, and
    # `compare_digest` raises on non-ASCII str instead of failing to match.
    return hmac.compare_digest(
        submitted_token.encode("utf-8"), expected.encode("ascii")
    )
