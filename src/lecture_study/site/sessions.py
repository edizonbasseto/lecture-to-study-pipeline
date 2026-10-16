"""Cookie sessions for the study site (SITE-04).

Basic Auth covers scripts and the deploy tool, but in-app browsers
(WhatsApp on iOS among them) never surface the Basic Auth prompt, which
left people on phones with no way past the 401. A signed cookie fills
that gap without a second credential store or a new dependency.

Each token is signed with a key that mixes the server's own signing
secret with the stored password hash of the person it was issued to.
The per-person half gives what ADR 0029 asks for and a key derived from
the whole allowlist broke: removing a line revokes only that person,
changing one password invalidates only that person's sessions, and
adding somebody logs nobody out. The server secret is what keeps the
password hash a verifier rather than a bearer key -- reading the
allowlist alone must not be enough to forge a cookie. Validating a
token also requires finding its address in the current allowlist, so a
token outlives neither the line that issued it nor the password behind
it.

This module never touches the filesystem: it is handed the credentials
the caller already read, so a missing allowlist fails closed at the one
place that reads it instead of raising an unhandled error here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from lecture_study.site.access import Credential, credential_for

# The `__Host-` prefix makes a browser accept the cookie only when it is
# Secure, Path=/ and carries no Domain, so neither a plain-HTTP response
# nor a sibling subdomain can plant or shadow a session. Renaming it from
# the earlier "pos_session" signs every existing session out once.
COOKIE_NAME = "__Host-pos_session"
SESSION_LIFETIME_SECONDS = 30 * 24 * 3600


def issue_session_token(credential: Credential, signing_secret: bytes) -> str:
    """Sign one authorized person with an expiry, producing a tamper-proof
    token."""
    expires_at = int(time.time() + SESSION_LIFETIME_SECONDS)
    return (
        f"{_encode_email(credential.email)}.{expires_at}."
        f"{_signature(credential, expires_at, signing_secret)}"
    )


def session_email(
    token: str | None,
    credentials: tuple[Credential, ...],
    signing_secret: bytes,
    now: float | None = None,
) -> str | None:
    """Return the email a token was issued to, or None when it is absent,
    malformed, forged, expired, or no longer on the allowlist."""
    parsed = _parse_token(token)
    if parsed is None:
        return None
    email, expires_at, signature = parsed
    moment = time.time() if now is None else now
    if moment > expires_at:
        return None
    credential = credential_for(email, credentials)
    if credential is None:
        return None
    expected = _signature(credential, expires_at, signing_secret)
    # Compared as bytes: header values decode as latin-1, so a cookie
    # carrying any byte above 127 would make `compare_digest` raise on
    # str instead of simply failing to match.
    if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
        return None
    return credential.email


def _parse_token(token: str | None) -> tuple[str, int, str] | None:
    if token is None:
        return None
    encoded_email, first_separator, remainder = token.partition(".")
    expiry_text, second_separator, signature = remainder.partition(".")
    if not first_separator or not second_separator:
        return None
    email = _decode_email(encoded_email)
    expires_at = _parse_expiry(expiry_text)
    if email is None or expires_at is None:
        return None
    return email, expires_at, signature


def _parse_expiry(expiry_text: str) -> int | None:
    """ASCII digits only: `str.isdigit` also accepts characters such as
    superscripts that `int` rejects, and `int` itself refuses absurdly long
    digit strings; both must read as a forged cookie, never raise."""
    if not (expiry_text.isascii() and expiry_text.isdigit()):
        return None
    try:
        return int(expiry_text)
    except ValueError:
        return None


def _encode_email(email: str) -> str:
    return base64.urlsafe_b64encode(email.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_email(encoded: str) -> str | None:
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _signature(credential: Credential, expires_at: int, signing_secret: bytes) -> str:
    payload = f"{credential.email}.{expires_at}".encode("utf-8")
    key = _signing_key(credential, signing_secret)
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _signing_key(credential: Credential, signing_secret: bytes) -> bytes:
    material = b"pos-session-v1." + credential.password_hash.encode("utf-8")
    return hmac.new(signing_secret, material, hashlib.sha256).digest()
