"""Session token tests (SITE-04): the cookie is the only credential a
phone ever presents, so forgery, expiry and revocation are asserted here
rather than inferred from the app's behaviour."""

import time

from lecture_study.site.access import Credential, hash_password
from lecture_study.site.sessions import (
    SESSION_LIFETIME_SECONDS,
    _parse_expiry,
    issue_session_token,
    session_email,
)


_SECRET = b"chave-de-assinatura-sintetica-para-testes-0001"


def _credential(
    email: str = "aluna@example.com", password: str = "senha123"
) -> Credential:
    return Credential(email=email, password_hash=hash_password(password))


class TestIssuedTokens:
    def test_a_fresh_token_names_the_person_it_was_issued_to(self):
        credential = _credential()

        token = issue_session_token(credential, _SECRET)

        assert session_email(token, (credential,), _SECRET) == "aluna@example.com"

    def test_an_address_with_a_separator_survives_the_round_trip(self):
        """The email is base64url-encoded precisely so that an address can
        never be confused with the token's own field separators."""
        credential = _credential(email="aluna.colega@example.com")

        token = issue_session_token(credential, _SECRET)

        assert (
            session_email(token, (credential,), _SECRET) == "aluna.colega@example.com"
        )


class TestRejectedTokens:
    def test_no_cookie_is_nobody(self):
        assert session_email(None, (_credential(),), _SECRET) is None

    def test_a_malformed_token_is_rejected(self):
        credential = _credential()

        assert session_email("nao-e-um-token", (credential,), _SECRET) is None
        assert session_email("a.b", (credential,), _SECRET) is None
        assert session_email("!!!.123.deadbeef", (credential,), _SECRET) is None

    def test_a_non_numeric_expiry_is_rejected(self):
        credential = _credential()
        token = issue_session_token(credential, _SECRET)
        encoded_email, _, remainder = token.partition(".")
        _, _, signature = remainder.partition(".")

        forged = f"{encoded_email}.depois.{signature}"

        assert session_email(forged, (credential,), _SECRET) is None

    def test_digits_that_int_rejects_are_refused_by_the_parser(self):
        """`str.isdigit` accepts what `int` refuses (superscripts, Arabic-Indic
        digits, absurdly long runs); the parser itself must return None for
        each, before any signature check runs."""
        assert _parse_expiry("²") is None
        assert _parse_expiry("٣٣") is None
        assert _parse_expiry("9" * 5000) is None

    def test_a_tampered_signature_is_rejected(self):
        credential = _credential()
        token = issue_session_token(credential, _SECRET)

        forged = token[:-1] + ("0" if token[-1] != "0" else "1")

        assert session_email(forged, (credential,), _SECRET) is None

    def test_extending_the_expiry_breaks_the_signature(self):
        """The expiry is inside the signed payload, so a cookie cannot be
        made to outlive what it was issued for."""
        credential = _credential()
        token = issue_session_token(credential, _SECRET)
        encoded_email, _, remainder = token.partition(".")
        expiry_text, _, signature = remainder.partition(".")

        forged = f"{encoded_email}.{int(expiry_text) + 86_400}.{signature}"

        assert session_email(forged, (credential,), _SECRET) is None

    def test_an_expired_token_is_rejected(self):
        credential = _credential()
        token = issue_session_token(credential, _SECRET)

        later = time.time() + SESSION_LIFETIME_SECONDS + 1

        assert session_email(token, (credential,), _SECRET, now=later) is None

    def test_a_token_for_somebody_off_the_allowlist_is_rejected(self):
        """Signature alone is not access: removing a line must revoke the
        cookie it issued, which is what ADR 0029 promises."""
        removed = _credential(email="antiga@example.com")
        token = issue_session_token(removed, _SECRET)

        remaining = _credential(email="aluna@example.com")

        assert session_email(token, (remaining,), _SECRET) is None

    def test_changing_one_password_revokes_only_that_person(self):
        primary = _credential()
        outra = _credential(email="outra@example.com", password="outra-senha")
        primary_token = issue_session_token(primary, _SECRET)
        outra_token = issue_session_token(outra, _SECRET)

        primary_after_rotation = _credential(password="senha-nova")
        allowlist = (primary_after_rotation, outra)

        assert session_email(primary_token, allowlist, _SECRET) is None
        assert session_email(outra_token, allowlist, _SECRET) == "outra@example.com"


class TestTheServerSecretIsPartOfTheKey:
    def test_another_secret_cannot_validate_the_token(self):
        """The stored password hash is a verifier, not a bearer key: reading
        the allowlist must not be enough to forge a cookie."""
        credential = _credential()
        token = issue_session_token(credential, _SECRET)

        other_secret = b"outra-chave-de-assinatura-sintetica-0002"

        assert session_email(token, (credential,), other_secret) is None
