"""Login CSRF (site hardening): the sign-in form only accepts a submission
that carries a token bound both to the server's signing secret and to a
nonce cookie only this site can set, so a third-party page cannot sign a
visitor in to an account of its choosing."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lecture_study.site.access import hash_password
from lecture_study.site.app import create_app
from lecture_study.site.login_csrf import (
    LOGIN_CSRF_COOKIE,
    LOGIN_CSRF_FIELD,
    is_genuine_login_submission,
    issue_login_nonce,
    login_form_token,
)
from lecture_study.site.sessions import COOKIE_NAME

_SIGNING_SECRET = b"chave-de-assinatura-sintetica-para-testes-0001"
_OTHER_SECRET = b"outra-chave-de-assinatura-sintetica-0002"
_EXPIRED_FORM_MESSAGE = "o formulário de entrada expirou"
_VALID_CREDENTIALS = {"email": "aluna@example.com", "password": "senha123"}


class TestTheToken:
    def test_a_token_minted_for_the_nonce_is_genuine(self) -> None:
        nonce = issue_login_nonce()

        token = login_form_token(nonce, _SIGNING_SECRET)

        assert is_genuine_login_submission(nonce, token, _SIGNING_SECRET)

    def test_a_token_for_another_nonce_is_refused(self) -> None:
        token = login_form_token(issue_login_nonce(), _SIGNING_SECRET)

        assert not is_genuine_login_submission(
            issue_login_nonce(), token, _SIGNING_SECRET
        )

    def test_a_token_under_another_secret_is_refused(self) -> None:
        nonce = issue_login_nonce()

        token = login_form_token(nonce, _OTHER_SECRET)

        assert not is_genuine_login_submission(nonce, token, _SIGNING_SECRET)

    @pytest.mark.parametrize(
        ("nonce", "token"),
        ((None, "x"), ("x" * 43, None), (None, None), ("x" * 43, "é")),
    )
    def test_a_missing_or_malformed_part_is_refused(
        self, nonce: str | None, token: str | None
    ) -> None:
        assert not is_genuine_login_submission(nonce, token, _SIGNING_SECRET)

    def test_a_malformed_nonce_is_refused_even_with_its_own_token(self) -> None:
        nonce = "curto;\r\nSet-Cookie: x=1"

        token = login_form_token(nonce, _SIGNING_SECRET)

        assert not is_genuine_login_submission(nonce, token, _SIGNING_SECRET)

    def test_each_nonce_is_fresh(self) -> None:
        assert issue_login_nonce() != issue_login_nonce()


@pytest.fixture
def signing_secret_path(tmp_path: Path) -> Path:
    path = tmp_path / "site-session-key"
    path.write_bytes(_SIGNING_SECRET)
    return path


@pytest.fixture
def client(tmp_path: Path, signing_secret_path: Path) -> TestClient:
    site_directory = tmp_path / "site"
    site_directory.mkdir()
    (site_directory / "index.html").write_text("<h1>Biblioteca</h1>", encoding="utf-8")
    credentials_path = tmp_path / "credentials.txt"
    credentials_path.write_text(
        f"aluna@example.com:{hash_password('senha123')}\n", encoding="utf-8"
    )
    application = create_app(
        site_directory,
        credentials_path,
        site_directory,
        signing_secret_path,
        tmp_path / "mock-exams",
    )
    return TestClient(
        application, follow_redirects=False, base_url="https://testserver"
    )


def _form_token(form_page: str) -> str:
    marker = f'name="{LOGIN_CSRF_FIELD}" value="'
    start = form_page.index(marker) + len(marker)
    return form_page[start : form_page.index('"', start)]


class TestTheLoginForm:
    def test_the_form_sets_a_host_only_strict_nonce_cookie(
        self, client: TestClient
    ) -> None:
        response = client.get("/entrar")

        set_cookie = response.headers["set-cookie"]
        attributes = [part.strip().lower() for part in set_cookie.split(";")]
        assert set_cookie.startswith(f"{LOGIN_CSRF_COOKIE}=")
        assert "secure" in attributes
        assert "httponly" in attributes
        assert "samesite=strict" in attributes
        assert "path=/" in attributes
        assert not any(part.startswith("domain=") for part in attributes)

    def test_the_form_embeds_the_token_for_its_nonce(self, client: TestClient) -> None:
        response = client.get("/entrar")

        nonce = response.cookies[LOGIN_CSRF_COOKIE]
        assert is_genuine_login_submission(
            nonce, _form_token(response.text), _SIGNING_SECRET
        )

    def test_a_second_form_keeps_the_first_nonce(self, client: TestClient) -> None:
        """Two tabs opened on the form must both still submit: the second
        GET reuses the nonce the browser already holds."""
        first = client.get("/entrar")

        second = client.get("/entrar")

        assert second.cookies[LOGIN_CSRF_COOKIE] == first.cookies[LOGIN_CSRF_COOKIE]
        assert _form_token(second.text) == _form_token(first.text)

    def test_the_form_is_refused_without_the_signing_secret(
        self, client: TestClient, signing_secret_path: Path
    ) -> None:
        signing_secret_path.unlink()

        response = client.get("/entrar")

        assert response.status_code == 503


class TestTheLoginSubmission:
    def test_a_submission_with_the_form_token_signs_in(
        self, client: TestClient
    ) -> None:
        token = _form_token(client.get("/entrar").text)

        response = client.post(
            "/entrar", data={**_VALID_CREDENTIALS, LOGIN_CSRF_FIELD: token}
        )

        assert response.status_code == 303
        assert response.cookies[COOKIE_NAME]

    def test_a_submission_without_a_token_is_refused_with_the_form(
        self, client: TestClient
    ) -> None:
        client.get("/entrar")

        response = client.post("/entrar", data=_VALID_CREDENTIALS)

        assert response.status_code == 403
        assert _EXPIRED_FORM_MESSAGE in response.text
        assert 'action="/entrar"' in response.text
        assert COOKIE_NAME not in response.cookies

    def test_a_submission_without_the_nonce_cookie_is_refused(
        self, client: TestClient
    ) -> None:
        token = _form_token(client.get("/entrar").text)
        client.cookies.clear()

        response = client.post(
            "/entrar", data={**_VALID_CREDENTIALS, LOGIN_CSRF_FIELD: token}
        )

        assert response.status_code == 403
        assert COOKIE_NAME not in response.cookies

    def test_a_forged_token_is_refused(self, client: TestClient) -> None:
        client.get("/entrar")

        response = client.post(
            "/entrar", data={**_VALID_CREDENTIALS, LOGIN_CSRF_FIELD: "0" * 64}
        )

        assert response.status_code == 403
        assert COOKIE_NAME not in response.cookies

    def test_an_attacker_pair_from_another_secret_is_refused(
        self, client: TestClient
    ) -> None:
        nonce = issue_login_nonce()
        client.cookies.set(LOGIN_CSRF_COOKIE, nonce)

        response = client.post(
            "/entrar",
            data={
                **_VALID_CREDENTIALS,
                LOGIN_CSRF_FIELD: login_form_token(nonce, _OTHER_SECRET),
            },
        )

        assert response.status_code == 403

    def test_the_refused_form_can_be_resubmitted(self, client: TestClient) -> None:
        client.get("/entrar")
        refused = client.post("/entrar", data=_VALID_CREDENTIALS)

        response = client.post(
            "/entrar",
            data={**_VALID_CREDENTIALS, LOGIN_CSRF_FIELD: _form_token(refused.text)},
        )

        assert response.status_code == 303
