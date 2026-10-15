#!/usr/bin/env python3
"""Signed-in page check for the synthetic demo site (ADR 0047).

The check performs the real browser flow against the site app: it fetches the
lecture page anonymously, loads the sign-in form, parses the anti-forgery
field, submits the credentials and fetches the page again with the session
cookie. It passes only when the anonymous request is refused, the sign-in
answers 303, the signed-in page answers 200 and carries the standing
uncertified-material notice. No Basic Auth shortcut and no forged cookie.

`httpx` is a development dependency, so this tooling lives in `scripts/` and
never in `src/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import httpx

from lecture_study.site.catalog import read_catalog
from lecture_study.site.login_csrf import LOGIN_CSRF_FIELD
from lecture_study.site.renderer import UNCERTIFIED_NOTICE

LOGIN_PATH = "/entrar"
_SIGNED_IN_REDIRECT_STATUS = 303
_PAGE_OK_STATUS = 200


class SignedInViewError(RuntimeError):
    """The sign-in flow could not even be attempted."""


@dataclass(frozen=True, slots=True)
class SignedInView:
    """What the four requests of the sign-in flow answered."""

    anonymous_status: int
    login_status: int
    page_status: int
    notice_present: bool

    def passed(self) -> bool:
        return (
            self.anonymous_status != _PAGE_OK_STATUS
            and self.login_status == _SIGNED_IN_REDIRECT_STATUS
            and self.page_status == _PAGE_OK_STATUS
            and self.notice_present
        )


class _CsrfFieldParser(HTMLParser):
    """Collects the value of the hidden anti-forgery input."""

    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == LOGIN_CSRF_FIELD:
            self.token = attributes.get("value")


def lecture_page_path(study_guides_root: Path, lecture_id: str) -> str:
    """Site path of a published lecture page; raises when it is not published."""

    catalog = read_catalog(study_guides_root)
    for lecture in catalog.lectures:
        if lecture.lecture_id == lecture_id:
            return f"/{lecture.relative_path}/index.html"
    raise LookupError(
        f"lecture {lecture_id!r} is not published under {study_guides_root}"
    )


def check_signed_in_view(
    client: httpx.Client, *, email: str, password: str, page_path: str
) -> SignedInView:
    """Run the sign-in flow on `client` and report each step's outcome."""

    anonymous = client.get(page_path, follow_redirects=False)
    csrf_token = _csrf_token(client.get(LOGIN_PATH, follow_redirects=False).text)
    login = client.post(
        LOGIN_PATH,
        data={
            "email": email,
            "password": password,
            LOGIN_CSRF_FIELD: csrf_token,
            "next": page_path,
        },
        follow_redirects=False,
    )
    page = client.get(page_path, follow_redirects=False)
    return SignedInView(
        anonymous_status=anonymous.status_code,
        login_status=login.status_code,
        page_status=page.status_code,
        notice_present=UNCERTIFIED_NOTICE in page.text,
    )


def _csrf_token(login_form_html: str) -> str:
    parser = _CsrfFieldParser()
    parser.feed(login_form_html)
    if parser.token is None:
        raise SignedInViewError("the sign-in form carries no anti-forgery field")
    return parser.token
