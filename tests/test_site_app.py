"""Site app tests (SITE-04): every request, static files included, is
authenticated before it can reach content."""

import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient

from lecture_study.domain.models import MockExam, MockQuestion
from lecture_study.site import throttle
from lecture_study.site.access import Credential, hash_password
from lecture_study.site.app import create_app
from lecture_study.site.login_csrf import (
    LOGIN_CSRF_COOKIE,
    LOGIN_CSRF_FIELD,
    login_form_token,
)
from lecture_study.site.sessions import COOKIE_NAME, issue_session_token


@pytest.fixture
def site_directory(tmp_path: Path) -> Path:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text("<h1>Biblioteca</h1>", encoding="utf-8")
    (site_dir / "secreto.pdf").write_bytes(b"conteudo-confidencial")
    return site_dir


@pytest.fixture
def video_library_directory(tmp_path: Path) -> Path:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "aula.mp4").write_bytes(b"video-confidencial")
    return video_dir


@pytest.fixture(autouse=True)
def brief_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    """The growing wait is asserted in the throttle's own tests; here it is
    shortened so the suite exercises the behaviour without sleeping for it."""
    monkeypatch.setattr(throttle, "FIRST_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(throttle, "MAX_DELAY_SECONDS", 0.4)


_TEST_SIGNING_SECRET = b"chave-de-assinatura-sintetica-para-testes-0001"

# Every client starts out holding this login nonce cookie, as a browser
# does once it has loaded the form; `_with_csrf` adds the matching token.
# The anti-forgery check itself is asserted in test_site_login_csrf.py.
_TEST_LOGIN_NONCE = "n" * 43


def _with_csrf(form: dict[str, str]) -> dict[str, str]:
    token = login_form_token(_TEST_LOGIN_NONCE, _TEST_SIGNING_SECRET)
    return {**form, LOGIN_CSRF_FIELD: token}


@pytest.fixture(autouse=True)
def signing_secret_path(tmp_path: Path) -> Path:
    """Autouse: every client needs a signing secret, the same way the
    deployed site always has one on disk."""
    path = tmp_path / "site-session-key"
    path.write_bytes(_TEST_SIGNING_SECRET)
    return path


@pytest.fixture
def credentials_path(tmp_path: Path) -> Path:
    path = tmp_path / "credentials.txt"
    path.write_text(
        f"aluna@example.com:{hash_password('senha123')}\n", encoding="utf-8"
    )
    return path


def _client(
    site_directory: Path,
    credentials_path: Path,
    video_library_directory: Path | None = None,
    signing_secret_path: Path | None = None,
    mock_exams_directory: Path | None = None,
    base_url: str = "http://testserver",
) -> TestClient:
    return TestClient(
        create_app(
            site_directory,
            credentials_path,
            video_library_directory
            if video_library_directory is not None
            else site_directory,
            signing_secret_path
            if signing_secret_path is not None
            else credentials_path.parent / "site-session-key",
            mock_exams_directory
            if mock_exams_directory is not None
            else credentials_path.parent / "mock-exams",
        ),
        follow_redirects=False,
        base_url=base_url,
        cookies={LOGIN_CSRF_COOKIE: _TEST_LOGIN_NONCE},
    )


def _seconds_spent(client: TestClient, form: dict[str, str]) -> float:
    started = time.monotonic()
    client.post("/entrar", data=_with_csrf(form))
    return time.monotonic() - started


def _seconds_spent_on_basic(client: TestClient, auth: tuple[str, str]) -> float:
    started = time.monotonic()
    client.get("/secreto.pdf", auth=auth)
    return time.monotonic() - started


@asynccontextmanager
async def _async_client(site_directory: Path, credentials_path: Path):
    application = create_app(
        site_directory,
        credentials_path,
        site_directory,
        credentials_path.parent / "site-session-key",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={LOGIN_CSRF_COOKIE: _TEST_LOGIN_NONCE},
    ) as client:
        yield client


class TestAuthentication:
    def test_no_credentials_is_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/index.html")

        assert response.status_code == 401

    def test_wrong_password_is_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/index.html", auth=("aluna@example.com", "errada"))

        assert response.status_code == 401

    def test_correct_credentials_reach_the_page(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/index.html", auth=("aluna@example.com", "senha123"))

        assert response.status_code == 200
        assert "Biblioteca" in response.text

    def test_a_material_file_requires_authentication_too(
        self, site_directory: Path, credentials_path: Path
    ):
        """The defect this guards: an authenticated index page whose linked
        files are reachable unauthenticated would leak every material."""
        client = _client(site_directory, credentials_path)

        unauthenticated = client.get("/secreto.pdf")
        authenticated = client.get(
            "/secreto.pdf", auth=("aluna@example.com", "senha123")
        )

        assert unauthenticated.status_code == 401
        assert authenticated.status_code == 200
        assert authenticated.content == b"conteudo-confidencial"

    def test_missing_credentials_file_denies_rather_than_errors_openly(
        self, site_directory: Path, tmp_path: Path
    ):
        client = _client(site_directory, tmp_path / "nao-existe.txt")

        response = client.get("/index.html", auth=("aluna@example.com", "senha123"))

        assert response.status_code == 503

    def test_a_video_requires_authentication_too(
        self,
        site_directory: Path,
        video_library_directory: Path,
        credentials_path: Path,
    ):
        """ADR 0029: video is served from its own mount, separate from the
        site tree. That mount must sit behind the same auth boundary as
        every other route, not slip through because it is configured
        differently."""
        client = _client(site_directory, credentials_path, video_library_directory)

        unauthenticated = client.get("/videos/aula.mp4")
        authenticated = client.get(
            "/videos/aula.mp4", auth=("aluna@example.com", "senha123")
        )

        assert unauthenticated.status_code == 401
        assert authenticated.status_code == 200
        assert authenticated.content == b"video-confidencial"

    def test_the_health_check_path_is_reachable_without_credentials(
        self, site_directory: Path, credentials_path: Path
    ):
        """The deploy tool's health check carries no credentials; it must
        still be able to tell the container is up."""
        client = _client(site_directory, credentials_path)

        response = client.get("/up")

        assert response.status_code == 200

    def test_removing_a_person_denies_the_next_request(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)
        assert (
            client.get(
                "/index.html", auth=("aluna@example.com", "senha123")
            ).status_code
            == 200
        )

        credentials_path.write_text("")

        assert (
            client.get(
                "/index.html", auth=("aluna@example.com", "senha123")
            ).status_code
            == 401
        )


class TestPublicLanding:
    def test_unauthenticated_root_serves_the_public_landing(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/")

        assert response.status_code == 200
        assert 'property="og:title"' in response.text
        assert "Pós em Veterinária" in response.text
        assert (
            "Materiais de estudo por aula: apostilas, simulados, flashcards, "
            "mapas e podcasts." in response.text
        )
        assert "<h1>Biblioteca</h1>" not in response.text

    def test_authenticated_root_serves_the_real_index(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/", auth=("aluna@example.com", "senha123"))

        assert response.status_code == 200
        assert "<h1>Biblioteca</h1>" in response.text

    def test_landing_links_only_to_the_entry_page(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/")

        assert response.text.count("href=") == 1
        assert 'href="/entrar"' in response.text

    def test_missing_credentials_file_fails_closed_when_credentials_arrive(
        self, site_directory: Path, tmp_path: Path
    ):
        client = _client(site_directory, tmp_path / "nao-existe.txt")

        response = client.get("/", auth=("aluna@example.com", "senha123"))

        assert response.status_code == 503


class TestSessionLogin:
    def test_the_login_form_is_reachable_without_credentials(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/entrar")

        assert response.status_code == 200
        assert 'action="/entrar"' in response.text
        assert "senha" in response.text

    def test_wrong_password_re_renders_the_form_with_the_error(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "errada"}),
        )

        assert response.status_code == 200
        assert "e-mail ou senha não autorizados" in response.text

    def test_correct_credentials_redirect_with_a_session_cookie(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"
        assert f"{COOKIE_NAME}=" in response.headers["set-cookie"]

    def test_a_session_cookie_reaches_content_without_basic_auth(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)
        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        session_cookie = login.headers["set-cookie"].split(";")[0]
        content = client.get("/secreto.pdf", headers={"Cookie": session_cookie})

        assert content.status_code == 200
        assert content.content == b"conteudo-confidencial"

    def test_rotating_the_allowlist_invalidates_issued_sessions(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)
        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        session_cookie = login.headers["set-cookie"].split(";")[0]

        credentials_path.write_text(
            f"aluna@example.com:{hash_password('outra-senha')}\n", encoding="utf-8"
        )

        denied = client.get("/secreto.pdf", headers={"Cookie": session_cookie})
        assert denied.status_code == 401


class TestPhoneBrowsersReachTheForm:
    def test_browser_without_session_is_sent_to_the_login_form(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/index.html", headers={"accept": "text/html"})

        assert response.status_code == 303
        assert response.headers["location"] == "/entrar?next=%2Findex.html"

    def test_script_without_session_still_gets_the_basic_auth_challenge(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/index.html", headers={"accept": "*/*"})

        assert response.status_code == 401
        assert "Basic" in response.headers["www-authenticate"]


class TestAuthorizedContentIsNeverCached:
    """A caching CDN in front of the site stores by file extension and
    ignores the cookie, so an authorized response without an explicit
    directive leaks to whoever asks the edge for the same URL next."""

    def test_authorized_content_is_cached_per_cookie(
        self, site_directory: Path, credentials_path: Path
    ):
        """The same URL answers differently with and without a session, so a
        shared cache must key on the cookie rather than on the path alone."""
        client = _client(site_directory, credentials_path)

        response = client.get("/secreto.pdf", auth=("aluna@example.com", "senha123"))

        assert response.headers["vary"] == "Cookie"

    def test_authorized_content_forbids_shared_caching(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/secreto.pdf", auth=("aluna@example.com", "senha123"))

        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"

    def test_the_redirect_to_the_login_form_forbids_shared_caching(
        self, site_directory: Path, credentials_path: Path
    ):
        """The CDN caches by extension: a cached redirect on a PDF's URL would
        bounce the authorized reader to the form instead of serving it."""
        client = _client(site_directory, credentials_path)

        response = client.get("/secreto.pdf", headers={"accept": "text/html"})

        assert response.status_code == 303
        assert response.headers["cache-control"] == "private, no-store"

    def test_the_public_landing_is_cached_per_cookie(
        self, site_directory: Path, credentials_path: Path
    ):
        """ "/" answers with the landing or with the library index depending on
        the cookie, so a shared cache must key on it."""
        client = _client(site_directory, credentials_path)

        response = client.get("/")

        assert response.status_code == 200
        assert response.headers["vary"] == "Cookie"

    def test_the_login_form_forbids_shared_caching(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/entrar")

        assert response.headers["cache-control"] == "private, no-store"

    def test_signing_in_forbids_shared_caching(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        assert response.headers["cache-control"] == "private, no-store"


class TestSessionCookieAttributes:
    """Asserted through the client's own cookie jar: injecting a `Cookie`
    header by hand passes even when `secure`, `path` or `samesite` are
    wrong, which is how those attributes went unverified."""

    def test_the_jar_carries_the_session_back_over_https(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(
            site_directory, credentials_path, base_url="https://testserver"
        )
        client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        content = client.get("/secreto.pdf")

        assert content.status_code == 200

    def test_the_session_is_marked_secure_httponly_and_lax(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        set_cookie = login.headers["set-cookie"].lower()
        assert "secure" in set_cookie
        assert "httponly" in set_cookie
        assert "samesite=lax" in set_cookie
        assert "path=/" in set_cookie

    def test_the_session_cookie_is_host_only_by_its_prefix(
        self, site_directory: Path, credentials_path: Path
    ):
        """The `__Host-` prefix makes a browser refuse the cookie unless it
        is Secure, Path=/ and has no Domain -- so neither a plain-HTTP
        response nor a sibling subdomain can plant or shadow a session."""
        client = _client(site_directory, credentials_path)

        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        set_cookie = login.headers["set-cookie"]
        attributes = [part.strip().lower() for part in set_cookie.split(";")]
        assert set_cookie.startswith("__Host-")
        assert "secure" in attributes
        assert "path=/" in attributes
        assert not any(part.startswith("domain=") for part in attributes)


class TestRemovingSomeoneRevokesOnlyThatPerson:
    def test_a_session_dies_when_its_line_leaves_the_allowlist(
        self, site_directory: Path, credentials_path: Path
    ):
        credentials_path.write_text(
            f"aluna@example.com:{hash_password('senha123')}\n"
            f"outra@example.com:{hash_password('outra-senha')}\n",
            encoding="utf-8",
        )
        client = _client(site_directory, credentials_path)
        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        session_cookie = login.headers["set-cookie"].split(";")[0]

        credentials_path.write_text(
            f"outra@example.com:{hash_password('outra-senha')}\n", encoding="utf-8"
        )

        denied = client.get("/secreto.pdf", headers={"Cookie": session_cookie})
        assert denied.status_code == 401

    def test_adding_somebody_leaves_existing_sessions_alone(
        self, site_directory: Path, credentials_path: Path
    ):
        """A key derived from the whole allowlist logged everyone out every
        time one person was added, which ADR 0029 forbids."""
        client = _client(site_directory, credentials_path)
        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        session_cookie = login.headers["set-cookie"].split(";")[0]

        with credentials_path.open("a", encoding="utf-8") as allowlist:
            allowlist.write(f"nova@example.com:{hash_password('mais-uma')}\n")

        content = client.get("/secreto.pdf", headers={"Cookie": session_cookie})
        assert content.status_code == 200


class TestTheLoginFormResistsAbuse:
    def test_repeated_failures_slow_down_without_locking_the_owner_out(
        self, site_directory: Path, credentials_path: Path
    ):
        """A lockout is aimed as easily at the rightful owner as at an
        attacker, and there is no password reset to recover through, so
        wrong guesses buy a wait rather than a closed door."""
        client = _client(site_directory, credentials_path)

        for _ in range(4):
            client.post(
                "/entrar",
                data=_with_csrf({"email": "aluna@example.com", "password": "errada"}),
            )
        accepted = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        assert accepted.status_code == 303

    def test_a_wrong_guess_costs_more_time_than_the_first_one(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        first = _seconds_spent(
            client, {"email": "aluna@example.com", "password": "errada"}
        )
        for _ in range(5):
            client.post(
                "/entrar",
                data=_with_csrf({"email": "aluna@example.com", "password": "errada"}),
            )
        later = _seconds_spent(
            client, {"email": "aluna@example.com", "password": "errada"}
        )

        assert later > first + 0.1

    def test_guessing_one_address_never_delays_another(
        self, site_directory: Path, credentials_path: Path
    ):
        credentials_path.write_text(
            f"aluna@example.com:{hash_password('senha123')}\n"
            f"outra@example.com:{hash_password('outra-senha')}\n",
            encoding="utf-8",
        )
        client = _client(site_directory, credentials_path)

        for _ in range(6):
            client.post(
                "/entrar",
                data=_with_csrf({"email": "aluna@example.com", "password": "errada"}),
            )
        other = _seconds_spent(
            client, {"email": "outra@example.com", "password": "outra-senha"}
        )

        assert other < 0.5

    def test_basic_auth_is_slowed_like_the_form(
        self, site_directory: Path, credentials_path: Path
    ):
        """The limits live on the shared gate, so guessing cannot simply
        switch doors."""
        client = _client(site_directory, credentials_path)

        first = _seconds_spent_on_basic(client, ("aluna@example.com", "errada"))
        for _ in range(5):
            client.get("/secreto.pdf", auth=("aluna@example.com", "errada"))
        later = _seconds_spent_on_basic(client, ("aluna@example.com", "errada"))

        assert later > first + 0.1

    def test_a_valid_session_is_never_slowed_by_somebody_else_guessing(
        self, site_directory: Path, credentials_path: Path
    ):
        """Waits guard password checking, not people already signed in."""
        client = _client(site_directory, credentials_path)
        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        session_cookie = login.headers["set-cookie"].split(";")[0]

        for _ in range(6):
            client.get("/secreto.pdf", auth=("aluna@example.com", "errada"))

        started = time.monotonic()
        content = client.get("/secreto.pdf", headers={"Cookie": session_cookie})
        assert content.status_code == 200
        assert time.monotonic() - started < 0.5

    def test_an_oversized_body_is_refused_as_too_large(
        self, site_directory: Path, credentials_path: Path
    ):
        """Its own status, not a credential rejection: asserting the generic
        rejection passed even with the size limit removed."""
        client = _client(site_directory, credentials_path)

        response = client.post("/entrar", content=b"email=" + b"a" * 8192)

        assert response.status_code == 413

    def test_an_oversized_body_spends_no_attempt_budget(
        self, site_directory: Path, credentials_path: Path
    ):
        """A body nobody read must not count as a guess, or refusing to read
        it becomes a way to lock somebody out."""
        client = _client(site_directory, credentials_path)

        for _ in range(12):
            client.post("/entrar", content=b"email=" + b"a" * 8192)
        accepted = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        assert accepted.status_code == 303

    def test_an_accented_address_is_denied_rather_than_crashing(
        self, site_directory: Path, credentials_path: Path
    ):
        """`compare_digest` raises TypeError on non-ASCII text, which turned
        any accented address into an unhandled 500."""
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf({"email": "josé@example.com", "password": "senha123"}),
        )

        assert response.status_code == 200
        assert "e-mail ou senha não autorizados" in response.text

    def test_an_accented_address_over_basic_auth_is_denied_too(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/secreto.pdf", auth=("josé@example.com", "senha123"))

        assert response.status_code == 401


class TestAccentedAddressesMatchEitherSpelling:
    def test_a_decomposed_address_reaches_its_composed_line(
        self, site_directory: Path, credentials_path: Path
    ):
        """An iPhone keyboard types decomposed accents while the allowlist on
        disk is usually composed; the same address must match either way."""
        credentials_path.write_text(
            f"jos\u00e9@example.com:{hash_password('senha123')}\n", encoding="utf-8"
        )
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {"email": "jose\u0301@example.com", "password": "senha123"}
            ),
        )

        assert response.status_code == 303


class TestAMissingSigningSecretDeniesEveryone:
    def test_content_is_refused_when_the_secret_is_absent(
        self, site_directory: Path, credentials_path: Path, signing_secret_path: Path
    ):
        """The secret is a credential: without it the site cannot prove
        anybody's identity, so it must deny rather than fall back."""
        signing_secret_path.unlink()
        client = _client(site_directory, credentials_path)

        response = client.get("/secreto.pdf", auth=("aluna@example.com", "senha123"))

        assert response.status_code == 503

    def test_a_short_secret_is_refused(
        self, site_directory: Path, credentials_path: Path, signing_secret_path: Path
    ):
        signing_secret_path.write_bytes(b"curta")
        client = _client(site_directory, credentials_path)

        response = client.get("/secreto.pdf", auth=("aluna@example.com", "senha123"))

        assert response.status_code == 503

    def test_rotating_the_secret_invalidates_issued_sessions(
        self, site_directory: Path, credentials_path: Path, signing_secret_path: Path
    ):
        client = _client(site_directory, credentials_path)
        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        session_cookie = login.headers["set-cookie"].split(";")[0]

        signing_secret_path.write_bytes(b"outra-chave-de-assinatura-sintetica-0002")

        denied = client.get("/secreto.pdf", headers={"Cookie": session_cookie})
        assert denied.status_code == 401


class TestAMissingAllowlistDeniesEveryone:
    def test_a_request_carrying_a_session_fails_closed_too(
        self, site_directory: Path, credentials_path: Path
    ):
        """Deriving the signing key read the allowlist without guarding the
        read, so a request *with* a cookie raised instead of returning 503 --
        the case the original missing-file test never exercised."""
        client = _client(site_directory, credentials_path)
        login = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        session_cookie = login.headers["set-cookie"].split(";")[0]

        credentials_path.unlink()

        response = client.get("/secreto.pdf", headers={"Cookie": session_cookie})
        assert response.status_code == 503


class TestABurstOfGuessesIsShed:
    """Driven through an in-loop ASGI client: the burst has to be genuinely
    concurrent inside one event loop, which a threaded client cannot be."""

    def test_guesses_beyond_the_waiting_room_get_429(
        self, site_directory: Path, credentials_path: Path
    ):
        """Sleeping requests hold a connection, so a flood must be refused
        rather than parked."""

        async def scenario() -> list[int]:
            async with _async_client(site_directory, credentials_path) as client:
                for _ in range(6):
                    await client.post(
                        "/entrar",
                        data=_with_csrf(
                            {"email": "aluna@example.com", "password": "errada"}
                        ),
                    )
                responses = await asyncio.gather(
                    *(
                        client.post(
                            "/entrar",
                            data=_with_csrf(
                                {"email": "aluna@example.com", "password": "errada"}
                            ),
                        )
                        for _ in range(40)
                    )
                )
                return [response.status_code for response in responses]

        assert 429 in asyncio.run(scenario())

    def test_the_owner_gets_in_while_her_address_is_being_guessed_at(
        self, site_directory: Path, credentials_path: Path, monkeypatch
    ):
        """The one property the whole mechanism exists for: a correct
        password is slowed, never refused. A cap an attacker could fill by
        naming her address would be a lockout with extra steps."""
        # Margin above the burst on purpose: 8 guesses plus her own login
        # left no slack against the default budget, so the check itself
        # could lose the race to the shared limiter and turn flaky.
        monkeypatch.setattr(throttle, "MAX_QUEUED_CHECKS", 20)

        async def scenario() -> int:
            async with _async_client(site_directory, credentials_path) as client:
                guesses = [
                    client.post(
                        "/entrar",
                        data=_with_csrf(
                            {"email": "aluna@example.com", "password": "errada"}
                        ),
                    )
                    for _ in range(8)
                ]
                signing_in = client.post(
                    "/entrar",
                    data=_with_csrf(
                        {"email": "aluna@example.com", "password": "senha123"}
                    ),
                )
                results = await asyncio.gather(signing_in, *guesses)
                return results[0].status_code

        assert asyncio.run(scenario()) == 303

    def test_parallel_basic_auth_fetches_all_succeed(
        self, site_directory: Path, credentials_path: Path
    ):
        """A browser opens several connections at once and scripts fetch in
        parallel; correct credentials must not be refused for arriving
        together."""

        async def scenario() -> list[int]:
            async with _async_client(site_directory, credentials_path) as client:
                responses = await asyncio.gather(
                    *(
                        client.get(
                            "/secreto.pdf", auth=("aluna@example.com", "senha123")
                        )
                        for _ in range(6)
                    )
                )
                return [response.status_code for response in responses]

        assert asyncio.run(scenario()) == [200] * 6

    def test_a_shed_attempt_never_inflates_its_target_delay(
        self, site_directory: Path, credentials_path: Path, monkeypatch
    ):
        """An attempt this gate discards for saturation costs its sender
        nothing -- no PBKDF2, no sleep -- and must not be able to spend the
        one thing it exists to protect: the delay budget of the address it
        named."""
        monkeypatch.setattr(throttle, "MAX_WAITING_ATTEMPTS", 1)

        async def scenario() -> tuple[list[int], float]:
            async with _async_client(site_directory, credentials_path) as client:
                held = client.post(
                    "/entrar",
                    data=_with_csrf(
                        {"email": "aluna@example.com", "password": "errada"}
                    ),
                )
                await asyncio.sleep(0)
                shed = await asyncio.gather(
                    *(
                        client.post(
                            "/entrar",
                            data=_with_csrf(
                                {"email": "aluna@example.com", "password": "errada"}
                            ),
                        )
                        for _ in range(20)
                    )
                )
                await held
                started = time.monotonic()
                await client.post(
                    "/entrar",
                    data=_with_csrf(
                        {"email": "aluna@example.com", "password": "senha123"}
                    ),
                )
                elapsed = time.monotonic() - started
                return [response.status_code for response in shed], elapsed

        shed_statuses, owner_elapsed = asyncio.run(scenario())

        assert 429 in shed_statuses
        assert owner_elapsed < 0.15

    def test_a_concurrent_flood_past_the_check_budget_never_inflates_the_delay(
        self, site_directory: Path, credentials_path: Path, monkeypatch
    ):
        """The room alone is not enough: a burst that all arrive before any
        of them suspends would all pass a saturation check read after an
        await, since none of them had yet claimed a place. The budget that
        matters is reserved synchronously, before the first await, so the
        Nth concurrent attempt sees exactly what the N-1 before it
        reserved."""
        monkeypatch.setattr(throttle, "MAX_CONCURRENT_CHECKS", 1)
        monkeypatch.setattr(throttle, "MAX_QUEUED_CHECKS", 2)

        async def scenario() -> tuple[list[int], float]:
            async with _async_client(site_directory, credentials_path) as client:
                flood = await asyncio.gather(
                    *(
                        client.post(
                            "/entrar",
                            data=_with_csrf(
                                {"email": "aluna@example.com", "password": "errada"}
                            ),
                        )
                        for _ in range(24)
                    )
                )
                started = time.monotonic()
                await client.post(
                    "/entrar",
                    data=_with_csrf(
                        {"email": "aluna@example.com", "password": "senha123"}
                    ),
                )
                elapsed = time.monotonic() - started
                return [response.status_code for response in flood], elapsed

        flood_statuses, owner_elapsed = asyncio.run(scenario())

        assert 429 in flood_statuses
        assert owner_elapsed < 0.15

    def test_a_burst_against_one_address_never_blocks_another(
        self, site_directory: Path, credentials_path: Path, monkeypatch
    ):
        # Same margin as the sibling test above, for the same reason: 8
        # guesses plus one more login left no slack against the default
        # budget.
        monkeypatch.setattr(throttle, "MAX_QUEUED_CHECKS", 20)
        credentials_path.write_text(
            f"aluna@example.com:{hash_password('senha123')}\n"
            f"outra@example.com:{hash_password('outra-senha')}\n",
            encoding="utf-8",
        )

        async def scenario() -> int:
            async with _async_client(site_directory, credentials_path) as client:
                guesses = [
                    client.post(
                        "/entrar",
                        data=_with_csrf(
                            {"email": "aluna@example.com", "password": "errada"}
                        ),
                    )
                    for _ in range(8)
                ]
                signing_in = client.post(
                    "/entrar",
                    data=_with_csrf(
                        {"email": "outra@example.com", "password": "outra-senha"}
                    ),
                )
                results = await asyncio.gather(signing_in, *guesses)
                return results[0].status_code

        assert asyncio.run(scenario()) == 303


class TestEveryShedAttemptIsLogged:
    """Sequential requests never saturate either layer: each one is
    admitted and released before the next arrives. Only genuine
    concurrency, driven through the in-loop ASGI client, exercises the
    shedding these tests are about."""

    def test_a_burst_the_waiting_room_sheds_still_leaves_a_trace(
        self, site_directory: Path, credentials_path: Path, monkeypatch, caplog
    ):
        """The room and the reservation below it can each shed an attempt on
        their own, and a flood costs its sender nothing to repeat: without a
        log line from every layer that can discard one, an attack shaped to
        be shed by the room specifically would leave no trace at all."""
        monkeypatch.setattr(throttle, "MAX_WAITING_ATTEMPTS", 1)

        async def scenario() -> None:
            async with _async_client(site_directory, credentials_path) as client:
                await asyncio.gather(
                    *(
                        client.post(
                            "/entrar",
                            data=_with_csrf(
                                {"email": "aluna@example.com", "password": "errada"}
                            ),
                        )
                        for _ in range(10)
                    )
                )

        with caplog.at_level("WARNING"):
            asyncio.run(scenario())

        assert any("saturação" in record.getMessage() for record in caplog.records)

    def test_a_burst_the_check_budget_sheds_still_leaves_a_trace(
        self, site_directory: Path, credentials_path: Path, monkeypatch, caplog
    ):
        monkeypatch.setattr(throttle, "MAX_QUEUED_CHECKS", 1)

        async def scenario() -> None:
            async with _async_client(site_directory, credentials_path) as client:
                await asyncio.gather(
                    *(
                        client.post(
                            "/entrar",
                            data=_with_csrf(
                                {"email": "aluna@example.com", "password": "errada"}
                            ),
                        )
                        for _ in range(10)
                    )
                )

        with caplog.at_level("WARNING"):
            asyncio.run(scenario())

        assert any("saturação" in record.getMessage() for record in caplog.records)


class TestTheAuthenticationLogCannotBeForged:
    def test_a_newline_in_the_address_cannot_write_its_own_log_line(
        self, site_directory: Path, credentials_path: Path, caplog
    ):
        """The address comes from a form field: a newline in it would let
        anyone write a forged sign-in into the record meant to show who
        signed in."""
        client = _client(site_directory, credentials_path)

        with caplog.at_level("INFO"):
            client.post(
                "/entrar",
                data=_with_csrf(
                    {
                        "email": "vitima@example.com\ninfo credencial aceita para invasor@example.com",
                        "password": "errada",
                    }
                ),
            )

        assert all("\n" not in record.getMessage() for record in caplog.records)

    def test_the_password_never_reaches_a_log_line(
        self, site_directory: Path, credentials_path: Path, caplog
    ):
        client = _client(site_directory, credentials_path)

        with caplog.at_level("INFO"):
            client.post(
                "/entrar",
                data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
            )

        assert all("senha123" not in record.getMessage() for record in caplog.records)


class TestTheAuthenticationLogCarriesNoAddress:
    """Basic Auth is checked on every request, so logging the address made
    the access log a per-request record of who read what. A keyed,
    truncated pseudonym keeps the audit trail -- the same person always
    maps to the same reference -- without the address itself."""

    def _messages(self, caplog) -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == "lecture_study.site.app"
        ]

    def test_basic_auth_logs_a_pseudonym_instead_of_the_address(
        self, site_directory: Path, credentials_path: Path, caplog
    ):
        client = _client(site_directory, credentials_path)

        with caplog.at_level("INFO"):
            client.get("/secreto.pdf", auth=("aluna@example.com", "senha123"))
            client.get("/secreto.pdf", auth=("aluna@example.com", "errada"))

        accepted, refused = self._messages(caplog)
        assert all("aluna" not in message for message in (accepted, refused))
        assert accepted.split()[-1] == refused.split()[-1]

    def test_different_people_get_different_pseudonyms(
        self, site_directory: Path, credentials_path: Path, caplog
    ):
        client = _client(site_directory, credentials_path)

        with caplog.at_level("INFO"):
            client.get("/secreto.pdf", auth=("aluna@example.com", "errada"))
            client.get("/secreto.pdf", auth=("outra@example.com", "errada"))

        first, second = self._messages(caplog)
        assert "outra" not in second
        assert first.split()[-1] != second.split()[-1]

    def test_the_form_sign_in_logs_no_address_either(
        self, site_directory: Path, credentials_path: Path, caplog
    ):
        client = _client(site_directory, credentials_path)

        with caplog.at_level("INFO"):
            client.post(
                "/entrar",
                data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
            )

        (message,) = self._messages(caplog)
        assert "aluna" not in message


class TestMalformedCookiesAreDeniedNotCrashed:
    def test_a_cookie_with_a_non_ascii_signature_is_denied(
        self, site_directory: Path, credentials_path: Path
    ):
        """Header values decode as latin-1, so comparing the signature as
        str raised TypeError instead of simply failing to match."""
        client = _client(site_directory, credentials_path)
        encoded_email = (
            base64.urlsafe_b64encode(b"aluna@example.com").decode("ascii").rstrip("=")
        )

        # Sent as raw bytes: a header carrying anything above 127 is what
        # reached `compare_digest` as non-ASCII str and raised.
        forged = (
            f"{COOKIE_NAME}={encoded_email}.4102444800.".encode("ascii") + b"\xe9" * 5
        )
        response = client.get("/secreto.pdf", headers={b"Cookie": forged})

        assert response.status_code == 401


class TestSigningInClearsWhatWasEarned:
    def test_the_wait_resets_after_a_correct_password(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)
        for _ in range(8):
            client.post(
                "/entrar",
                data=_with_csrf({"email": "aluna@example.com", "password": "errada"}),
            )

        client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        after = _seconds_spent(
            client, {"email": "aluna@example.com", "password": "errada"}
        )

        assert after < 0.5


class TestALinkKeepsItsDestinationThroughSignIn:
    def test_a_denied_request_carries_its_path_to_the_login_form(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get(
            "/2026/materia/apostila_completa.html",
            headers={"Accept": "text/html"},
        )

        assert response.status_code == 303
        assert response.headers["location"] == (
            "/entrar?next=%2F2026%2Fmateria%2Fapostila_completa.html"
        )

    def test_the_login_form_repeats_next_as_a_hidden_field(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/entrar?next=%2F2026%2Fmateria%2Fapostila_completa.html")

        assert response.status_code == 200
        assert (
            '<input type="hidden" name="next" '
            'value="/2026/materia/apostila_completa.html">' in response.text
        )

    def test_signing_in_with_a_valid_next_returns_to_it(
        self, site_directory: Path, credentials_path: Path
    ):
        (site_directory / "2026").mkdir()
        (site_directory / "2026" / "apostila.html").write_text(
            "<h1>Apostila</h1>", encoding="utf-8"
        )
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/2026/apostila.html",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/2026/apostila.html"

    def test_signing_in_with_no_next_still_lands_on_the_index(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_an_absolute_url_in_next_is_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "https://evil.example/phish",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_a_protocol_relative_next_is_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "//evil.example/phish",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_a_next_with_a_parent_traversal_is_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/../etc/passwd",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_a_next_with_a_backslash_is_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        """A browser reads a backslash as a path separator, so
        `/\\evil.example` is a scheme-relative redirect in disguise."""
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/\\evil.example",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_a_next_with_an_embedded_newline_is_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        """A raw CR/LF in the redirect target would let it split the
        Location header into extra ones instead of just failing to
        redirect."""
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/index.html\r\nSet-Cookie: forjado=1",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_next_pointing_at_logout_is_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        """A same-site path that just signs the person right back out is
        allowed by the shape check alone, so it is excluded by name."""
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/sair",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_next_pointing_at_logout_with_a_query_string_is_still_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        """Route matching only looks at the path, so a query string or
        fragment tacked onto "/sair" still dispatches to sign-out -- the
        exclusion has to see through that the same way routing does."""
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/sair?x=1",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_next_pointing_at_logout_with_a_fragment_is_still_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/sair#frag",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_next_pointing_at_logout_percent_encoded_is_still_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        """Routing decodes the path before matching a route, so a
        percent-encoded letter still reaches "/sair" even though the raw
        string never equals it."""
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/sa%69r",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_next_pointing_at_logout_via_a_dot_segment_is_still_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        """A browser resolves "/./sair" to "/sair" before it ever sends the
        request, the same way it resolves any relative reference."""
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/./sair",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_next_pointing_at_logout_double_percent_encoded_is_still_rejected(
        self, site_directory: Path, credentials_path: Path
    ):
        """One decode pass turns "%2573air" into "%73air" -- still an
        escape, not yet "sair" -- so a single `unquote` is not enough to
        see that this also names the logout route."""
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/%2573air",
                }
            ),
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/index.html"

    def test_signing_in_through_a_double_encoded_logout_spelling_keeps_the_session(
        self, site_directory: Path, credentials_path: Path
    ):
        """Belt and braces on top of the Location assertion above: once the
        redirect target is forced back to the fallback, following it must
        land on ordinary content with the session intact, never anywhere
        that clears the cookie this same sign-in just issued."""
        client = _client(site_directory, credentials_path)

        signed_in = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "senha123",
                    "next": "/%2573air",
                }
            ),
        )
        assert signed_in.headers["location"] == "/index.html"
        session_cookie = signed_in.headers["set-cookie"].split(";")[0]

        followed = client.get(
            signed_in.headers["location"], headers={"Cookie": session_cookie}
        )

        assert followed.status_code == 200

    def test_a_malicious_next_on_the_login_form_falls_back_in_the_hidden_field(
        self, site_directory: Path, credentials_path: Path
    ):
        """The GET form must never echo an unvalidated `next` back to the
        browser, even though it is only headed into a hidden field."""
        client = _client(site_directory, credentials_path)

        response = client.get("/entrar", params={"next": "https://evil.example/phish"})

        assert response.status_code == 200
        assert '<input type="hidden" name="next" value="/index.html">' in response.text
        assert "evil.example" not in response.text

    def test_a_wrong_password_re_renders_the_form_with_next_preserved(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.post(
            "/entrar",
            data=_with_csrf(
                {
                    "email": "aluna@example.com",
                    "password": "errada",
                    "next": "/2026/apostila.html",
                }
            ),
        )

        assert response.status_code == 200
        assert (
            '<input type="hidden" name="next" value="/2026/apostila.html">'
            in response.text
        )


class TestSigningOut:
    def test_signing_out_redirects_to_the_public_landing(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/sair")

        assert response.status_code == 303
        assert response.headers["location"] == "/"

    def test_signing_out_clears_the_session_cookie(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)
        client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        assert client.cookies.get(COOKIE_NAME) is not None

        client.get("/sair")

        assert client.cookies.get(COOKIE_NAME) is None

    def test_after_signing_out_the_old_session_no_longer_reaches_content(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)
        client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )

        client.get("/sair")
        response = client.get("/secreto.pdf")

        assert response.status_code in (401, 303)

    def test_signing_out_with_no_cookie_does_not_error(
        self, site_directory: Path, credentials_path: Path
    ):
        client = _client(site_directory, credentials_path)

        response = client.get("/sair")

        assert response.status_code == 303


def _write_exam(directory: Path, lecture_id: str, correct_answer: str = "B") -> None:
    exam = MockExam(
        lecture_id=lecture_id,
        questions=[
            MockQuestion(
                question_text="Cão de 8 anos com FC 40bpm. Qual a conduta?",
                options={
                    "A": "Nenhuma conduta.",
                    "B": "Monitorar e investigar causa.",
                    "C": "Eutanasia imediata.",
                    "D": "Aumentar a dose de anestesico.",
                },
                correct_answer=correct_answer,
                explanation="Bradicardia sinusal exige monitoramento contínuo.",
            )
        ],
    )
    exam_dir = directory / lecture_id
    exam_dir.mkdir(parents=True)
    (exam_dir / "exam.json").write_text(
        json.dumps(exam.model_dump(mode="json")), encoding="utf-8"
    )


@pytest.fixture
def mock_exams_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "mock-exams"
    directory.mkdir()
    _write_exam(directory, "01aula")
    _write_exam(directory, "02aula", correct_answer="C")
    return directory


class TestQuiz:
    def _signed_in_client(
        self,
        site_directory: Path,
        credentials_path: Path,
        mock_exams_directory: Path,
    ) -> TestClient:
        client = _client(
            site_directory,
            credentials_path,
            mock_exams_directory=mock_exams_directory,
            base_url="https://testserver",
        )
        client.post(
            "/entrar",
            data=_with_csrf({"email": "aluna@example.com", "password": "senha123"}),
        )
        return client

    def test_the_quiz_form_requires_authentication(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        client = _client(
            site_directory, credentials_path, mock_exams_directory=mock_exams_directory
        )

        response = client.get("/simulado/01aula")

        assert response.status_code in (401, 303)

    def test_grading_requires_authentication(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        client = _client(
            site_directory, credentials_path, mock_exams_directory=mock_exams_directory
        )

        response = client.post("/simulado/01aula", data={"q0": "A"})

        assert response.status_code in (401, 303)

    def test_the_form_never_reveals_the_correct_answer(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        client = self._signed_in_client(
            site_directory, credentials_path, mock_exams_directory
        )

        response = client.get("/simulado/01aula")

        assert response.status_code == 200
        # Grep-testable invariant: neither the field name nor the
        # explanation text (the two answer-bearing values) may ever
        # appear in the GET response body.
        assert "correct_answer" not in response.text
        assert "Bradicardia sinusal" not in response.text

    def test_grading_reveals_the_answer_key_after_submission(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        client = self._signed_in_client(
            site_directory, credentials_path, mock_exams_directory
        )

        response = client.post("/simulado/01aula", data={"q0": "B"})

        assert response.status_code == 200
        assert "Nota: 1 de 1" in response.text
        assert "Bradicardia sinusal" in response.text

    def test_grading_one_lecture_does_not_leak_into_another(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        """01aula's correct answer is B, 02aula's is C. Submitting B against
        02aula must be graded wrong -- proof the grading endpoint reads the
        *claimed* lecture's own exam rather than trusting any lecture/answer
        association implied by the request."""
        client = self._signed_in_client(
            site_directory, credentials_path, mock_exams_directory
        )

        response = client.post("/simulado/02aula", data={"q0": "B"})

        assert "Nota: 0 de 1" in response.text

    def test_unknown_lecture_id_is_a_clean_404(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        client = self._signed_in_client(
            site_directory, credentials_path, mock_exams_directory
        )

        response = client.get("/simulado/naoexiste")

        assert response.status_code == 404

    def test_path_traversal_in_the_lecture_id_is_rejected(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        client = self._signed_in_client(
            site_directory, credentials_path, mock_exams_directory
        )

        response = client.get("/simulado/..%2F..%2Fetc%2Fpasswd")

        # Refused as an ambiguous path before routing ever sees the id.
        assert response.status_code == 400

    def test_repeated_submissions_measurably_slow_down(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        """Empirical reproduction, mirroring ADR 0039's own before/after
        measurement of the login throttle: the Nth grading submission in a
        row from the same authorized person must cost measurably more wall
        time than the first, proving the growing wait is actually paid."""
        client = self._signed_in_client(
            site_directory, credentials_path, mock_exams_directory
        )

        def _seconds_for_one_submission() -> float:
            started = time.monotonic()
            client.post("/simulado/01aula", data={"q0": "A"})
            return time.monotonic() - started

        first = _seconds_for_one_submission()
        for _ in range(4):
            _seconds_for_one_submission()
        fifth_onward = _seconds_for_one_submission()

        assert fifth_onward > first
        assert fifth_onward >= 0.05  # shortened FIRST_DELAY_SECONDS fixture

    def test_the_throttle_is_scoped_per_lecture_not_per_person(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        """Retaking one quiz repeatedly earns a wait; that must not spill
        onto a *different* lecture's quiz the same person opens next --
        an ordinary study session moving between lectures must stay fast."""
        client = self._signed_in_client(
            site_directory, credentials_path, mock_exams_directory
        )
        for _ in range(5):
            client.post("/simulado/01aula", data={"q0": "A"})

        started = time.monotonic()
        client.post("/simulado/02aula", data={"q0": "A"})
        seconds = time.monotonic() - started

        assert seconds < 0.05  # still within 02aula's own free attempts

    def test_an_oversized_submission_is_refused_with_its_own_message(
        self, site_directory: Path, credentials_path: Path, mock_exams_directory: Path
    ):
        client = self._signed_in_client(
            site_directory, credentials_path, mock_exams_directory
        )

        response = client.post("/simulado/01aula", data={"q0": "A" * 20_000})

        assert response.status_code == 413
        assert "simulado" in response.text


class TestRestrictedPerson:
    """A credential carrying path prefixes reaches only those prefixes."""

    _LECTURE = "aulas/tcc"

    @pytest.fixture
    def restricted_client(
        self,
        site_directory: Path,
        video_library_directory: Path,
        credentials_path: Path,
    ) -> TestClient:
        (site_directory / "aulas" / "tcc").mkdir(parents=True)
        (site_directory / "aulas" / "tcc" / "index.html").write_text("tcc")
        (site_directory / "aulas" / "outra").mkdir()
        (site_directory / "aulas" / "outra" / "index.html").write_text("outra")
        (site_directory / "estilo.css").write_text("body{}")
        (video_library_directory / "aulas" / "tcc").mkdir(parents=True)
        (video_library_directory / "aulas" / "tcc" / "aula.mp4").write_bytes(b"v")
        prefixes = "/aulas/tcc,/videos/aulas/tcc,/estilo.css"
        with credentials_path.open("a", encoding="utf-8") as handle:
            handle.write(f"tcc@example.com:{hash_password('tcc-senha')}:{prefixes}\n")
        return _client(site_directory, credentials_path, video_library_directory)

    _AUTH = ("tcc@example.com", "tcc-senha")

    def test_reaches_its_own_lecture_video_and_stylesheet(
        self, restricted_client: TestClient
    ):
        for path in (
            "/aulas/tcc/index.html",
            "/videos/aulas/tcc/aula.mp4",
            "/estilo.css",
        ):
            assert restricted_client.get(path, auth=self._AUTH).status_code == 200

    def test_other_lectures_and_the_index_are_refused(
        self, restricted_client: TestClient
    ):
        for path in ("/aulas/outra/index.html", "/index.html", "/secreto.pdf"):
            assert restricted_client.get(path, auth=self._AUTH).status_code == 403

    def test_a_prefix_matches_whole_segments_only(self, restricted_client: TestClient):
        response = restricted_client.get("/aulas/tcc-extra/x", auth=self._AUTH)

        assert response.status_code == 403

    def test_dot_segments_cannot_walk_out_of_the_prefix(
        self, restricted_client: TestClient
    ):
        response = restricted_client.get(
            "/aulas/tcc/../outra/index.html", auth=self._AUTH
        )

        assert response.status_code == 403

    def test_a_browser_outside_the_scope_lands_on_its_lecture(
        self, restricted_client: TestClient
    ):
        response = restricted_client.get(
            "/index.html", auth=self._AUTH, headers={"accept": "text/html"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/aulas/tcc/index.html"

    def test_an_unrestricted_person_still_reaches_everything(
        self, restricted_client: TestClient
    ):
        response = restricted_client.get(
            "/aulas/outra/index.html", auth=("aluna@example.com", "senha123")
        )

        assert response.status_code == 200


# --- Ambiguous request paths -------------------------------------------------
#
# Starlette re-parses the decoded scope path to build request.url, so a
# percent-encoded "?" or "#" truncates request.url.path while static-file
# serving still resolves the full scope path. Each of these paths once let an
# anonymous or scope-restricted visitor read content the access check never
# saw; they must now be refused before any access decision.

_AMBIGUOUS_ANONYMOUS_PATHS = (
    "/entrar%3F/../secreto.pdf",
    "/entrar%3F/../index.html",
    "/sair%3F/../secreto.pdf",
    "/up%3F/../secreto.pdf",
    "/entrar%3F%2F..%2Fsecreto.pdf",
    "/entrar%23/../secreto.pdf",
    "/entrar%5C..%5Csecreto.pdf",
    "/entrar%00/../secreto.pdf",
)


async def _raw_get(app, raw_path: str, cookie: str | None = None) -> tuple[int, bytes]:
    """Drive the ASGI app with an undecoded path, the way a server hands a
    request over, without a client library normalizing it first."""
    headers = [(b"host", b"testserver")]
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(raw_path),
        "raw_path": raw_path.encode(),
        "root_path": "",
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "headers": headers,
    }
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return status, body


@pytest.mark.parametrize("raw_path", _AMBIGUOUS_ANONYMOUS_PATHS)
def test_ambiguous_paths_are_refused_before_authentication(
    raw_path: str,
    site_directory: Path,
    credentials_path: Path,
    signing_secret_path: Path,
) -> None:
    app = create_app(
        site_directory, credentials_path, site_directory, signing_secret_path
    )

    status, body = asyncio.run(_raw_get(app, raw_path))

    assert status == 400
    assert b"conteudo-confidencial" not in body
    assert b"Biblioteca" not in body


def test_exempt_paths_still_answer_without_credentials(
    site_directory: Path, credentials_path: Path, signing_secret_path: Path
) -> None:
    app = create_app(
        site_directory, credentials_path, site_directory, signing_secret_path
    )

    assert asyncio.run(_raw_get(app, "/up"))[0] == 200
    assert asyncio.run(_raw_get(app, "/entrar"))[0] == 200


@pytest.mark.parametrize(
    "raw_path",
    (
        "/permitido/x%3F/../../outro/segredo.html",
        "/permitido/x%23/../../outro/segredo.html",
    ),
)
def test_ambiguous_paths_cannot_escape_a_restricted_scope(
    raw_path: str, tmp_path: Path, signing_secret_path: Path
) -> None:
    site_dir = tmp_path / "escopo"
    (site_dir / "permitido").mkdir(parents=True)
    (site_dir / "outro").mkdir()
    (site_dir / "permitido" / "index.html").write_text("PERMITIDO", encoding="utf-8")
    (site_dir / "outro" / "segredo.html").write_text("FORA-DO-ESCOPO", encoding="utf-8")
    password_hash = hash_password("senha123")
    credentials = tmp_path / "restrito.txt"
    credentials.write_text(
        f"restrita@example.com:{password_hash}:/permitido\n", encoding="utf-8"
    )
    app = create_app(site_dir, credentials, site_dir, signing_secret_path)
    token = issue_session_token(
        Credential("restrita@example.com", password_hash, ("/permitido",)),
        signing_secret_path.read_bytes(),
    )
    cookie = f"{COOKIE_NAME}={token}"

    assert asyncio.run(_raw_get(app, "/permitido/index.html", cookie))[0] == 200
    status, body = asyncio.run(_raw_get(app, raw_path, cookie))

    assert status == 400
    assert b"FORA-DO-ESCOPO" not in body


@pytest.mark.parametrize(
    ("method", "path", "allowed_methods"),
    (
        ("HEAD", "/entrar", "GET, POST"),
        ("PUT", "/entrar", "GET, POST"),
        ("DELETE", "/entrar", "GET, POST"),
        ("PUT", "/sair", "GET"),
        ("POST", "/sair", "GET"),
        ("HEAD", "/sair", "GET"),
        ("POST", "/up", "GET, HEAD"),
    ),
)
def test_exempt_paths_refuse_other_methods_without_redirecting(
    method: str,
    path: str,
    allowed_methods: str,
    site_directory: Path,
    credentials_path: Path,
) -> None:
    """The unauthenticated exemptions name a method as well as a path, so
    any other method is refused outright -- never handed to the static
    files behind it, and never redirected to the login form, which a
    client that keeps HEAD across redirects would follow forever."""
    client = _client(site_directory, credentials_path)

    response = client.request(
        method, path, headers={"Accept": "text/html"}, follow_redirects=False
    )

    assert response.status_code == 405
    assert response.headers["Allow"] == allowed_methods
    assert response.headers["X-Frame-Options"] == "DENY"


def test_exempt_methods_still_answer_without_credentials(
    site_directory: Path, credentials_path: Path
) -> None:
    client = _client(site_directory, credentials_path)

    assert client.get("/up").status_code == 200
    assert client.head("/up").status_code == 200
    assert client.get("/entrar").status_code == 200
    assert client.post("/entrar", data=_with_csrf({})).status_code == 200
    assert client.get("/sair").status_code == 303
