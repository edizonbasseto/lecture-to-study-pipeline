#!/usr/bin/env python3
"""Access material, site generation and signed-in check for the demo site (ADR 0047).

Subcommands: `prepare-access` generates the sign-in values, the session secret
and a throwaway loopback certificate under `workspace/`; `generate` builds the
site from the published study guides with the demo schedule; `check` runs the
signed-in page check against the served site.

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

import argparse
import os
import ssl
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import httpx
from dotenv import dotenv_values

from lecture_study.demo.lecture_id import parse_lecture_id
from lecture_study.demo.site_access import (
    DEMO_EMAIL,
    DEMO_SCHEDULE_PATH,
    DEMO_SITE_URL,
    SITE_ACCESS_DIR,
    SiteAccessPaths,
    certificate_command,
    credentials_line,
    environment_file_text,
    new_demo_password,
    new_session_secret,
    require_inside,
)
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import run_bounded_process
from lecture_study.site.catalog import STUDY_GUIDES_ROOT, Lecture, read_catalog
from lecture_study.site.generator import generate_site
from lecture_study.site.login_csrf import LOGIN_CSRF_FIELD
from lecture_study.site.renderer import UNCERTIFIED_NOTICE

LOGIN_PATH = "/entrar"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPOSITORY_ROOT / "workspace"
_SIGNED_IN_REDIRECT_STATUS = 303
_PAGE_OK_STATUS = 200
_OWNER_ONLY_FILE = 0o600
_OWNER_ONLY_DIRECTORY = 0o700
_OPENSSL_TIMEOUT_SECONDS = 60
_OPENSSL_OUTPUT_LIMIT_BYTES = 64 * 1024


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

    for lecture in read_catalog(study_guides_root).lectures:
        if lecture.lecture_id == lecture_id:
            return page_path_of(lecture)
    raise LookupError(
        f"lecture {lecture_id!r} is not published under {study_guides_root}"
    )


def page_path_of(lecture: Lecture) -> str:
    return f"/{lecture.relative_path}/index.html"


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


def prepare_access(
    directory: Path, *, workspace_root: Path = WORKSPACE_ROOT
) -> SiteAccessPaths:
    """Generate the demo sign-in values, session secret and loopback certificate.

    The password is written only to the `.env` file of `directory`; the
    allowlist holds its hash. Refuses a directory outside `workspace_root`.
    """

    require_inside(directory, workspace_root)
    directory.mkdir(mode=_OWNER_ONLY_DIRECTORY, parents=True, exist_ok=True)
    directory.chmod(_OWNER_ONLY_DIRECTORY)
    paths = SiteAccessPaths(directory)
    password = new_demo_password()
    _write_owner_only(paths.credentials, credentials_line(DEMO_EMAIL, password))
    _write_owner_only(paths.session_key, new_session_secret())
    _write_owner_only(
        paths.environment_file, environment_file_text(DEMO_EMAIL, password)
    )
    _create_certificate(paths)
    return paths


def read_sign_in_values(paths: SiteAccessPaths) -> tuple[str, str]:
    """The demo email and password stored in the access directory's `.env`."""

    values = dotenv_values(paths.environment_file)
    email, password = values.get("DEMO_SITE_EMAIL"), values.get("DEMO_SITE_PASSWORD")
    if not email or not password:
        raise SignedInViewError(
            f"{paths.environment_file} lacks DEMO_SITE_EMAIL or DEMO_SITE_PASSWORD; "
            "run prepare-access first"
        )
    return email, password


def _write_owner_only(path: Path, content: str | bytes) -> None:
    data = content.encode("utf-8") if isinstance(content, str) else content
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_ONLY_FILE)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), _OWNER_ONLY_FILE)
        stream.write(data)


def _create_certificate(paths: SiteAccessPaths) -> None:
    environment = sanitized_child_process_environment()
    openssl = resolve_trusted_executable("openssl", search_path=environment.get("PATH"))
    run_bounded_process(
        certificate_command(openssl, paths),
        env=environment,
        timeout_seconds=_OPENSSL_TIMEOUT_SECONDS,
        maximum_stdout_bytes=_OPENSSL_OUTPUT_LIMIT_BYTES,
        maximum_stderr_bytes=_OPENSSL_OUTPUT_LIMIT_BYTES,
        check=True,
    )
    paths.private_key.chmod(_OWNER_ONLY_FILE)
    paths.certificate.chmod(_OWNER_ONLY_FILE)


def _run_prepare_access(arguments: argparse.Namespace) -> int:
    paths = prepare_access(arguments.directory)
    print(f"credentials: {paths.credentials}")
    print(f"session key: {paths.session_key}")
    print(f"certificate: {paths.certificate}")
    print(f"private key: {paths.private_key}")
    print(f"The sign-in values are in {paths.environment_file}.")
    return 0


def _run_generate(arguments: argparse.Namespace) -> int:
    catalog = generate_site(academic_schedule_path=DEMO_SCHEDULE_PATH)
    for lecture in catalog.lectures:
        print(f"{lecture.lecture_id} {page_path_of(lecture)}")
    return 0


def _run_check(arguments: argparse.Namespace) -> int:
    paths = SiteAccessPaths(arguments.directory)
    email, password = read_sign_in_values(paths)
    trusted_certificate = ssl.create_default_context(cafile=str(paths.certificate))
    with httpx.Client(
        base_url=arguments.base_url, verify=trusted_certificate
    ) as client:
        view = check_signed_in_view(
            client,
            email=email,
            password=password,
            page_path=lecture_page_path(STUDY_GUIDES_ROOT, arguments.lecture_id),
        )
    print(
        f"lecture={arguments.lecture_id} anonymous_status={view.anonymous_status} "
        f"login_status={view.login_status} page_status={view.page_status} "
        f"notice_present={view.notice_present}"
    )
    return 0 if view.passed() else 1


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare-access", help="generate sign-in material")
    prepare.add_argument("--directory", type=Path, default=SITE_ACCESS_DIR)
    prepare.set_defaults(run=_run_prepare_access)
    subcommands.add_parser("generate", help="build the demo site").set_defaults(
        run=_run_generate
    )
    check = subcommands.add_parser("check", help="signed-in page check")
    check.add_argument("--lecture-id", type=parse_lecture_id, required=True)
    check.add_argument("--base-url", default=DEMO_SITE_URL)
    check.add_argument("--directory", type=Path, default=SITE_ACCESS_DIR)
    check.set_defaults(run=_run_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.chdir(REPOSITORY_ROOT)
    arguments = _argument_parser().parse_args(argv)
    return int(arguments.run(arguments))


if __name__ == "__main__":
    sys.exit(main())
