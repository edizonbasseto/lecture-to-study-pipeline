"""Security headers (site hardening): every response the site sends --
refusals and static files included -- carries the same protective headers,
and the Content-Security-Policy admits exactly the inline code the site's
own pages contain."""

import base64
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp

from lecture_study.site import app as site_app
from lecture_study.site.access import hash_password
from lecture_study.site.app import create_app
from lecture_study.site.catalog import SiteCatalog
from lecture_study.site.quiz import render_quiz_form
from lecture_study.site.renderer import render_home

_EXPECTED_STATIC_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "strict-transport-security": "max-age=31536000",
    "x-frame-options": "DENY",
}


def _application(tmp_path: Path) -> ASGIApp:
    site_directory = tmp_path / "site"
    site_directory.mkdir()
    (site_directory / "index.html").write_text("<h1>Biblioteca</h1>", encoding="utf-8")
    (site_directory / "secreto.pdf").write_bytes(b"conteudo-confidencial")
    credentials_path = tmp_path / "credentials.txt"
    credentials_path.write_text(
        f"aluna@example.com:{hash_password('senha123')}\n", encoding="utf-8"
    )
    signing_secret_path = tmp_path / "site-session-key"
    signing_secret_path.write_bytes(b"chave-de-assinatura-sintetica-para-testes-0001")
    return create_app(
        site_directory,
        credentials_path,
        site_directory,
        signing_secret_path,
        tmp_path / "mock-exams",
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(_application(tmp_path), follow_redirects=False)


def _content_security_policy(client: TestClient) -> dict[str, list[str]]:
    header = client.get("/up").headers["content-security-policy"]
    directives = (directive.split() for directive in header.split(";"))
    return {tokens[0]: tokens[1:] for tokens in directives if tokens}


def _source_hash(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def _inline_blocks(html: str, tag: str) -> list[str]:
    pattern = rf"<{tag}(?P<attributes>[^>]*)>(?P<content>.*?)</{tag}>"
    return [
        match["content"]
        for match in re.finditer(pattern, html, flags=re.DOTALL)
        if "application/json" not in match["attributes"]
    ]


@pytest.mark.parametrize(
    ("method", "path", "auth", "expected_status"),
    (
        ("GET", "/up", None, 200),
        ("GET", "/", None, 200),
        ("GET", "/entrar", None, 200),
        ("GET", "/secreto.pdf", None, 401),
        ("GET", "/secreto.pdf", ("aluna@example.com", "errada"), 401),
        ("GET", "/secreto.pdf", ("aluna@example.com", "senha123"), 200),
        ("GET", "/index.html", ("aluna@example.com", "senha123"), 200),
        ("GET", "/x%3Fy", None, 400),
        ("POST", "/entrar", None, 403),
        ("GET", "/sair", None, 303),
    ),
)
def test_every_response_carries_the_security_headers(
    client: TestClient,
    method: str,
    path: str,
    auth: tuple[str, str] | None,
    expected_status: int,
) -> None:
    response = client.request(method, path, auth=auth)

    assert response.status_code == expected_status
    for name, value in _EXPECTED_STATIC_HEADERS.items():
        assert response.headers[name] == value
    assert response.headers["content-security-policy"]


def test_an_unhandled_exception_response_carries_the_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starlette answers an unhandled exception from its outermost
    ServerErrorMiddleware, outside every middleware added to the app, so
    the headers must be applied by a wrapper around the finished app."""

    async def fail_unexpectedly(self: object) -> bytes:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(site_app._AccessGate, "signing_secret", fail_unexpectedly)
    client = TestClient(
        _application(tmp_path), follow_redirects=False, raise_server_exceptions=False
    )

    response = client.get("/entrar")

    assert response.status_code == 500
    for name, value in _EXPECTED_STATIC_HEADERS.items():
        assert response.headers[name] == value
    assert response.headers["content-security-policy"]


def test_the_policy_locks_down_everything_the_pages_do_not_use(
    client: TestClient,
) -> None:
    policy = _content_security_policy(client)

    assert policy["default-src"] == ["'self'"]
    assert policy["base-uri"] == ["'none'"]
    assert policy["form-action"] == ["'self'"]
    assert policy["frame-ancestors"] == ["'none'"]
    assert "'unsafe-inline'" not in policy["script-src"]
    assert "'unsafe-inline'" not in policy["style-src"]


def test_the_policy_admits_the_library_home_search_script(client: TestClient) -> None:
    allowed_scripts = _content_security_policy(client)["script-src"]
    home_page = render_home(SiteCatalog(years=()), recent=())

    scripts = _inline_blocks(home_page, "script")

    assert scripts
    for script in scripts:
        assert _source_hash(script) in allowed_scripts


def test_the_policy_admits_every_inline_style_the_site_serves(
    client: TestClient,
) -> None:
    allowed_styles = _content_security_policy(client)["style-src"]
    pages = (
        client.get("/").text,
        client.get("/entrar").text,
        render_quiz_form("01aula", ()),
    )

    styles = [style for page in pages for style in _inline_blocks(page, "style")]

    assert len(styles) == len(pages)
    for style in styles:
        assert _source_hash(style) in allowed_styles
