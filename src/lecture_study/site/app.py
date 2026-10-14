"""The study site's serving app (SITE-04, SITE-05, SITE-08).

Every request, including every static file, is authenticated against the
email allowlist before Starlette's static-file handler ever resolves a
path: the check runs in ASGI middleware wrapped around the whole app,
which is what makes it apply to a mounted StaticFiles app the same way it
applies to any other route. Two trees are served, `workspace/site/` (text
and PDF materials, mounted at "/") and `workspace/site-videos/` (video,
mounted at "/videos", ADR 0029) -- both generated ahead of time by
`site.generator`. This app never writes to either, and never touches
`workspace/study-guides/` directly -- the generator is the only seam
between the two (SITE-01).
"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path
from posixpath import normpath
from typing import NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp
from starlette.concurrency import run_in_threadpool

from lecture_study.domain.models import MockExam
from lecture_study.site.access import (
    CREDENTIALS_PATH,
    Credential,
    CredentialsError,
    credential_for,
    normalize_email,
    read_credentials,
    verify_credentials,
)
from lecture_study.site.generator import SITE_OUTPUT_DIR, VIDEO_LIBRARY_DIR
from lecture_study.site.login_csrf import (
    LOGIN_CSRF_COOKIE,
    LOGIN_CSRF_FIELD,
    is_genuine_login_submission,
    is_well_formed_nonce,
    issue_login_nonce,
    login_form_token,
)
from lecture_study.site.quiz import (
    MOCK_EXAMS_DIR,
    QUIZ_STYLE,
    ExamNotFoundError,
    grade,
    parse_submitted_answers,
    public_view,
    read_exam,
    render_quiz_form,
    render_quiz_results,
)
from lecture_study.site.renderer import HOME_SCRIPT
from lecture_study.site.security_headers import (
    SecurityHeadersMiddleware,
    content_security_policy,
)
from lecture_study.site.sessions import (
    COOKIE_NAME,
    SESSION_LIFETIME_SECONDS,
    issue_session_token,
    session_email,
)
from lecture_study.site.signing import SIGNING_SECRET_PATH, read_signing_secret
from lecture_study.site.throttle import (
    AttemptDelay,
    PasswordCheckLimiter,
    SuppressingLogGate,
    TooManyAttempts,
    WaitingRoom,
)

# Pseudonymous references and outcomes only: neither a password nor an
# address may reach a log line (see `_account_reference`).
_LOGGER = logging.getLogger(__name__)
_ACCOUNT_REFERENCE_DOMAIN = b"pos-log-account-v1."
_ACCOUNT_REFERENCE_HEX_DIGITS = 16

_HEALTH_CHECK_PATH = "/up"
_LOGIN_PATH = "/entrar"
_LOGOUT_PATH = "/sair"
# The only (path, method) pairs answered without credentials. Any other
# method on these paths -- HEAD, PUT, DELETE -- is refused with 405 before
# authentication (see `_method_not_allowed_response`) instead of falling
# through to the static-file handler mounted at "/".
_UNAUTHENTICATED_METHODS_BY_PATH = {
    _HEALTH_CHECK_PATH: frozenset({"GET", "HEAD"}),
    _LOGIN_PATH: frozenset({"GET", "POST"}),
    _LOGOUT_PATH: frozenset({"GET"}),
}
_QUIZ_PATH_PREFIX = "/simulado/"

# A submitted quiz answer sheet is small and fixed-shape (one letter per
# question); reading an unbounded body here would cost an authenticated
# but abusive caller nothing and the server a growing allocation.
_MAX_QUIZ_BODY_BYTES = 16_384

# Where a signed-in visitor lands when the login form was reached with no
# destination to return to -- the same fallback an invalid or absent `next`
# resolves to (see `_safe_redirect_target`).
_DEFAULT_POST_LOGIN_TARGET = "/index.html"
_NEXT_FIELD = "next"

# Every authorized response carries this. The site sits behind a caching
# CDN that stores by file extension regardless of the request's cookie,
# so a PDF fetched once by an authorized person would otherwise be served
# from the edge to anyone who knows the URL, with this app never running.
_PRIVATE_CACHE_DIRECTIVE = "private, no-store"

# A login form submission never needs more than a short email and
# password; reading an unbounded body from an unauthenticated caller is
# free memory for them and none for anyone else.
_MAX_LOGIN_BODY_BYTES = 4096

# A stray "%25" left after one decode pass turns into a literal "%" that a
# second pass would then read as the start of a new escape -- one decode
# is not enough to see past double encoding when validating a `next`
# target (see `_fully_decoded`). Iterating to a fixed point is what a
# chain of decoders -- and there can genuinely be more than one between
# here and the browser -- would eventually settle on; this bound guards
# only against a pathological input looping forever, not against any real
# path depth this site ever produces. `unquote` never lengthens its input,
# so the loop is guaranteed to reach a fixed point; a candidate that still
# hasn't after this many passes is returned partially decoded rather than
# rejected outright, which is safe here only because the caller then
# re-checks the *result* against the logout path and against
# normalization, rather than trusting "ran out of passes" as its own
# verdict.
_MAX_DECODE_PASSES = 10

# One message for a wrong address and for a wrong password alike: telling
# them apart tells an attacker which addresses exist.
_REJECTED_MESSAGE = "e-mail ou senha não autorizados"

_UNAUTHORIZED_RESPONSE = Response(
    content=_REJECTED_MESSAGE,
    status_code=401,
    headers={
        "WWW-Authenticate": 'Basic realm="Biblioteca de estudo"',
        "Cache-Control": _PRIVATE_CACHE_DIRECTIVE,
    },
)
_MISCONFIGURED_RESPONSE = Response(
    content="o acesso ao site não está configurado no servidor",
    status_code=503,
    headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE},
)
_OVERSIZED_LOGIN_RESPONSE = Response(
    content="envio grande demais para um formulário de entrada",
    status_code=413,
    headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE},
)
_TOO_MANY_ATTEMPTS_RESPONSE = Response(
    content="tentativas demais; aguarde alguns minutos antes de tentar de novo",
    status_code=429,
    headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE},
)
_EXAM_NOT_FOUND_RESPONSE = Response(
    content="nenhum simulado disponível para esta aula",
    status_code=404,
    headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE},
)
_OVERSIZED_QUIZ_RESPONSE = Response(
    content="envio grande demais para um simulado",
    status_code=413,
    headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE},
)

_LANDING_PATH = "/"

_LANDING_STYLE = """
  body { margin: 0; font-family: system-ui, sans-serif; background: #10151c; color: #e6e9ee; display: flex; min-height: 100vh; align-items: center; justify-content: center; }
  main { max-width: 34rem; padding: 2.5rem 1.5rem; text-align: center; }
  h1 { font-size: 1.6rem; margin: 0 0 0.75rem; }
  p { color: #aab3bf; line-height: 1.5; margin: 0 0 0.5rem; }
  small { color: #67707c; }
  a.enter { display: inline-block; margin-top: 1.25rem; padding: 0.7rem 1.6rem; border-radius: 8px; background: #2563eb; color: #fff; font-weight: 600; text-decoration: none; }
"""

_LANDING_HTML = (
    """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pós em Veterinária — Biblioteca de estudo</title>
<meta property="og:title" content="Pós em Veterinária — Biblioteca de estudo">
<meta property="og:description" content="Materiais de estudo por aula: apostilas, simulados, flashcards, mapas e podcasts.">
<meta property="og:site_name" content="Pós em Veterinária">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary">
<style>"""
    + _LANDING_STYLE
    + """</style>
</head>
<body>
<main>
  <h1>Pós em Veterinária — Biblioteca de estudo</h1>
  <p>Materiais de estudo por aula: apostilas, simulados, flashcards, mapas e podcasts.</p>
  <p>Acesso restrito a pessoas autorizadas — ao entrar, o site pede e-mail e senha cadastrados.</p>
  <a class="enter" href="/entrar">Entrar na biblioteca</a>
  <p><small>study.example.com</small></p>
</main>
</body>
</html>
"""
)

_PUBLIC_LANDING_RESPONSE = Response(
    content=_LANDING_HTML,
    status_code=200,
    media_type="text/html",
    # "/" answers with this page or with the library's index depending on
    # the cookie, so a shared cache must key on it rather than hand one
    # visitor's answer to the next.
    headers={"Cache-Control": "public, max-age=300", "Vary": "Cookie"},
)

_LOGIN_FORM_STYLE = """
  body { margin: 0; font-family: system-ui, sans-serif; background: #10151c; color: #e6e9ee; display: flex; min-height: 100vh; align-items: center; justify-content: center; }
  form { max-width: 22rem; padding: 2rem 1.5rem; display: flex; flex-direction: column; gap: 0.75rem; }
  h1 { font-size: 1.3rem; margin: 0 0 0.5rem; text-align: center; }
  input { padding: 0.65rem 0.75rem; border-radius: 8px; border: 1px solid #2c3745; background: #171e28; color: #e6e9ee; font-size: 1rem; }
  button { padding: 0.7rem; border: 0; border-radius: 8px; background: #2563eb; color: #fff; font-size: 1rem; font-weight: 600; }
  p.error { color: #f87171; margin: 0; text-align: center; }
"""


class _FormRefusal(NamedTuple):
    """Why a login submission was turned back to the form, and with what
    status."""

    message: str
    status_code: int


_REJECTED_CREDENTIALS = _FormRefusal(_REJECTED_MESSAGE, 200)
# The form's anti-forgery token is missing or does not match its cookie --
# for a real visitor, almost always a form left open across a cookie
# reset, so the message invites a retry on the fresh form returned with it.
_EXPIRED_FORM = _FormRefusal(
    "o formulário de entrada expirou; tente entrar de novo", 403
)


def _login_form_response(
    request: Request,
    signing_secret: bytes,
    next_target: str,
    refusal: _FormRefusal | None = None,
) -> Response:
    """The login form, carrying the anti-forgery token for this browser's
    nonce cookie -- the one it already holds, so two open tabs both stay
    valid, or a fresh one set alongside the form."""
    held_nonce = request.cookies.get(LOGIN_CSRF_COOKIE)
    nonce = held_nonce if is_well_formed_nonce(held_nonce) else issue_login_nonce()
    response = Response(
        content=_login_form_html(
            next_target,
            login_form_token(nonce, signing_secret),
            refusal.message if refusal else None,
        ),
        status_code=refusal.status_code if refusal else 200,
        media_type="text/html",
        headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE},
    )
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        nonce,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response


def _login_form_html(
    next_target: str, csrf_token: str, error_message: str | None
) -> str:
    error_html = (
        f'<p class="error">{escape(error_message)}</p>' if error_message else ""
    )
    # `next_target` reaches here only after `_safe_redirect_target` has
    # already reduced it to a same-site relative path, so escaping it is
    # solely about safe HTML embedding -- not a second layer of redirect
    # validation, which the POST handler still repeats on its own.
    next_field = (
        f'<input type="hidden" name="{_NEXT_FIELD}" value="{escape(next_target)}">'
    )
    csrf_field = (
        f'<input type="hidden" name="{LOGIN_CSRF_FIELD}" value="{escape(csrf_token)}">'
    )
    return (
        "<!DOCTYPE html>"
        '<html lang="pt-BR">'
        '<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Entrar — Pós em Veterinária</title>"
        f"<style>{_LOGIN_FORM_STYLE}</style>"
        "</head>"
        "<body>"
        '<form method="post" action="/entrar">'
        "<h1>Entrar na biblioteca</h1>"
        f"{error_html}"
        '<input type="email" name="email" placeholder="e-mail" autocomplete="username" required>'
        '<input type="password" name="password" placeholder="senha" autocomplete="current-password" required>'
        f"{next_field}"
        f"{csrf_field}"
        '<button type="submit">Entrar</button>'
        "</form>"
        "</body></html>"
    )


class _ServerSecrets(NamedTuple):
    """The allowlist and the signing secret, from one read of both."""

    credentials: tuple[Credential, ...]
    signing_secret: bytes


class _AccessGate:
    """Decides whether one request may see content.

    Both doors -- the session cookie and Basic Auth -- pass through here,
    so the limits that protect password checking cannot be walked around
    by picking the other one.
    """

    def __init__(self, credentials_path: Path, signing_secret_path: Path) -> None:
        self._credentials_path = credentials_path
        self._signing_secret_path = signing_secret_path
        self._delay = AttemptDelay()
        self._limiter = PasswordCheckLimiter()
        self._room = WaitingRoom()
        self._shed_log = SuppressingLogGate()

    async def authorizes(self, request: Request) -> bool:
        """Whether this request carries a valid session or valid Basic
        credentials. Raises TooManyAttempts when checking is saturated."""
        return await self.identify(request) is not None

    async def identify(self, request: Request) -> Credential | None:
        """The authorized person this request carries, by session or Basic
        credentials, or None when neither authorizes it. Raises
        TooManyAttempts when checking is saturated."""
        cookie = request.cookies.get(COOKIE_NAME)
        basic = _parse_basic_auth(request.headers.get("authorization"))
        if cookie is None and basic is None:
            return None
        server_secrets = await run_in_threadpool(self._read_secrets)
        session_holder = session_email(
            cookie, server_secrets.credentials, server_secrets.signing_secret
        )
        if session_holder is not None:
            return credential_for(session_holder, server_secrets.credentials)
        if basic is None:
            return None
        email, password = basic
        return await self._verify(email, password, server_secrets)

    async def signing_secret(self) -> bytes:
        """The server's signing secret, read fresh like every other secret
        here. Raises CredentialsError when it is absent or too short."""
        return await run_in_threadpool(read_signing_secret, self._signing_secret_path)

    async def sign_in(self, email: str, password: str) -> str | None:
        """Check one submitted pair and, when it holds, mint the session
        token for it -- from the one read of the secrets that check made."""
        server_secrets = await run_in_threadpool(self._read_secrets)
        credential = await self._verify(email, password, server_secrets)
        if credential is None:
            return None
        return issue_session_token(credential, server_secrets.signing_secret)

    async def _verify(
        self, email: str, password: str, server_secrets: _ServerSecrets
    ) -> Credential | None:
        account = normalize_email(email)
        try:
            # Both the room and the reservation below can shed an attempt,
            # and either one doing so must be logged the same way: neither
            # costs its sender anything, so nothing upstream limits how
            # often either fires.
            async with self._room.room_for_one():
                # Reserved synchronously, before the wait is claimed: an
                # attempt a concurrent flood is about to shed must not
                # still spend the one thing it exists to protect -- the
                # target address's own delay budget. Checking after an
                # await would let every attempt in the flood pass the
                # check before any of them had suspended.
                self._limiter.reserve()
                try:
                    # The wait is claimed before anything else is awaited,
                    # so attempts made side by side each pay their own
                    # place in the queue instead of sharing one. The room
                    # above bounds the waiting globally on purpose: a cap
                    # an attacker could fill by naming somebody else's
                    # address would be a way to lock that person out.
                    await asyncio.sleep(self._delay.claim(account))
                    async with self._limiter.run():
                        credential = await run_in_threadpool(
                            verify_credentials,
                            email,
                            password,
                            server_secrets.credentials,
                        )
                finally:
                    self._limiter.release()
        except TooManyAttempts:
            self._log_shed_attempt()
            raise
        reference = _account_reference(account, server_secrets.signing_secret)
        if credential is None:
            _LOGGER.warning("credencial recusada para conta %s", reference)
        else:
            self._delay.forget(account)
            _LOGGER.info("credencial aceita para conta %s", reference)
        return credential

    def _log_shed_attempt(self) -> None:
        """A shed attempt costs its caller nothing, so nothing upstream
        limits how often this fires; without its own limit a flood logging
        once per rejection buries the sign-ins and real refusals the log
        exists to show."""
        should_log, swallowed = self._shed_log.should_log()
        if should_log:
            _LOGGER.warning(
                "verificação recusada por saturação (%d silenciadas)", swallowed
            )

    def _read_secrets(self) -> _ServerSecrets:
        return _ServerSecrets(
            read_credentials(self._credentials_path),
            read_signing_secret(self._signing_secret_path),
        )


class _QuizGate:
    """Slows repeated grading submissions from one authorized person down,
    the same growing-wait shape `_AccessGate` already uses for password
    checking (ADR 0039) -- guessing a mock exam's answer key by resubmitting
    the form must cost an attacker throughput, exactly like guessing a
    password does. There is no PBKDF2 cost to amortize here, so this omits
    `_AccessGate`'s `PasswordCheckLimiter`/`SuppressingLogGate` pair and
    keeps only the two pieces that stop a flood of *waits* themselves from
    becoming the attack: the wait, and a global room bounding how many may
    be waited out at once.
    """

    def __init__(self) -> None:
        self._delay = AttemptDelay()
        self._room = WaitingRoom()

    async def throttled_wait(self, email: str, lecture_id: str) -> None:
        """Claim and sleep out this submission's wait, or raise
        TooManyAttempts when the global waiting room is full.

        Keyed by `(email, lecture_id)`, not by `email` alone: an ordinary
        study session that retakes one lecture's quiz a few times, or works
        through several different lectures back to back, must not share one
        free-attempts budget across all of them. Scoping per lecture keeps
        the growing wait aimed at repeatedly resubmitting the *same*
        answer key -- the actual brute-force shape this exists to slow --
        without also punishing ordinary practice across a study session.
        """
        account = f"{email}:{lecture_id}"
        async with self._room.room_for_one():
            await asyncio.sleep(self._delay.claim(account))


_AMBIGUOUS_PATH_CHARACTERS = frozenset("?#\\\x00")
_AMBIGUOUS_PATH_RESPONSE = Response(status_code=400)


def _unambiguous_served_path(request: Request) -> str | None:
    """The decoded path the router and static files will serve, or None
    when another layer could read it differently.

    request.url is rebuilt by re-parsing the decoded scope path, so an
    encoded "?" or "#" truncates request.url.path while StaticFiles still
    resolves the full scope path; dot segments let that truncated prefix
    walk anywhere. Every access decision must therefore be made on the
    scope path, and a path whose meaning depends on the reader is refused.
    """
    path: str = request.scope["path"]
    if any(character in _AMBIGUOUS_PATH_CHARACTERS for character in path):
        return None
    if any(segment in (".", "..") for segment in path.split("/")):
        return None
    return path


# Every inline script and style the site serves, by the constant it is
# rendered from: the policy admits these exact contents and nothing else.
_CONTENT_SECURITY_POLICY = content_security_policy(
    inline_scripts=(HOME_SCRIPT,),
    inline_styles=(_LANDING_STYLE, _LOGIN_FORM_STYLE, QUIZ_STYLE),
)


def create_app(
    site_directory: Path = SITE_OUTPUT_DIR,
    credentials_path: Path = CREDENTIALS_PATH,
    video_library_directory: Path = VIDEO_LIBRARY_DIR,
    signing_secret_path: Path = SIGNING_SECRET_PATH,
    mock_exams_directory: Path = MOCK_EXAMS_DIR,
) -> ASGIApp:
    """Build the app against an explicit site tree and credentials file --
    the seam that lets a test point both at a temporary directory instead
    of the real workspace paths.

    The security headers wrap the finished app rather than being added as
    one of its middlewares: Starlette answers an unhandled exception from
    ServerErrorMiddleware, which sits outside every added middleware, so
    only an outermost wrapper also covers those 500 responses.
    """
    application = _build_application(
        site_directory,
        credentials_path,
        video_library_directory,
        signing_secret_path,
        mock_exams_directory,
    )
    return SecurityHeadersMiddleware(application, policy=_CONTENT_SECURITY_POLICY)


def _build_application(
    site_directory: Path,
    credentials_path: Path,
    video_library_directory: Path,
    signing_secret_path: Path,
    mock_exams_directory: Path,
) -> FastAPI:
    application = FastAPI(
        title="Pós em Veterinária — Biblioteca de estudo", docs_url=None, redoc_url=None
    )
    gate = _AccessGate(credentials_path, signing_secret_path)
    quiz_gate = _QuizGate()

    async def admit_authorized_person(
        request: Request,
        served_path: str,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        try:
            identified = await gate.identify(request)
        except CredentialsError:
            return _MISCONFIGURED_RESPONSE
        except TooManyAttempts:
            return _TOO_MANY_ATTEMPTS_RESPONSE
        if identified is None:
            return _denial_for(request)
        if not identified.permits(served_path):
            return _out_of_scope_response(request, identified)
        identified_email = identified.email
        # Routes that must rate-limit per authorized person (the quiz
        # grading endpoint) read this instead of re-deriving identity --
        # re-running Basic Auth's PBKDF2 check a second time per request
        # would double its cost for no additional assurance.
        request.state.pos_email = identified_email
        response = await call_next(request)
        response.headers["Cache-Control"] = _PRIVATE_CACHE_DIRECTIVE
        response.headers["Vary"] = "Cookie"
        return response

    @application.middleware("http")
    async def require_authorized_person(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        served_path = _unambiguous_served_path(request)
        if served_path is None:
            return _AMBIGUOUS_PATH_RESPONSE
        if _is_exempt_path_with_other_method(served_path, request.method):
            return _method_not_allowed_response(served_path)
        if not _is_unauthenticated_route(served_path, request.method):
            return await admit_authorized_person(request, served_path, call_next)
        if served_path == _HEALTH_CHECK_PATH:
            # Deliberately unauthenticated: the deploy tool's health check
            # has no credentials and must be able to tell the container is
            # up. It returns no content -- nothing a health check can leak.
            return Response(status_code=200)
        # The login form is the one page an in-app browser must reach
        # without credentials: WhatsApp's embedded browser never shows the
        # Basic Auth prompt phones would otherwise need.
        #
        # Signing out must work for exactly the visitor who no longer has
        # -- or never had -- a valid session; requiring one first would
        # make a stale or already-cleared cookie unrecoverable without a
        # new sign-in just to sign out. Accepted trade-off: a bare GET with
        # no origin check means a third-party page can force this for a
        # visitor who has it open, but the only effect is an unwanted
        # sign-out -- no data is exposed and nothing is granted that
        # authentication would otherwise deny.
        return await call_next(request)

    @application.get(_LOGIN_PATH)
    async def show_login_form(request: Request) -> Response:
        next_target = _safe_redirect_target(request.query_params.get(_NEXT_FIELD))
        try:
            signing_secret = await gate.signing_secret()
        except CredentialsError:
            return _MISCONFIGURED_RESPONSE
        return _login_form_response(request, signing_secret, next_target)

    @application.post(_LOGIN_PATH)
    async def submit_login_form(request: Request) -> Response:
        body = await _read_bounded_body(request)
        if body is None:
            return _OVERSIZED_LOGIN_RESPONSE
        submitted = _parse_form_fields(body)
        next_target = _safe_redirect_target(submitted.get(_NEXT_FIELD))
        try:
            signing_secret = await gate.signing_secret()
        except CredentialsError:
            return _MISCONFIGURED_RESPONSE
        # Checked before any password is: a forged submission must spend
        # none of the throttling budget a real sign-in depends on.
        if not is_genuine_login_submission(
            request.cookies.get(LOGIN_CSRF_COOKIE),
            submitted.get(LOGIN_CSRF_FIELD),
            signing_secret,
        ):
            return _login_form_response(
                request, signing_secret, next_target, _EXPIRED_FORM
            )
        try:
            token = await gate.sign_in(
                submitted.get("email", ""), submitted.get("password", "")
            )
        except CredentialsError:
            return _MISCONFIGURED_RESPONSE
        except TooManyAttempts:
            return _TOO_MANY_ATTEMPTS_RESPONSE
        if token is None:
            return _login_form_response(
                request, signing_secret, next_target, _REJECTED_CREDENTIALS
            )
        return _signed_in_response(token, next_target)

    @application.get(_LOGOUT_PATH)
    async def sign_out() -> Response:
        return _signed_out_response()

    async def _load_exam(lecture_id: str) -> MockExam | None:
        try:
            return await run_in_threadpool(read_exam, lecture_id, mock_exams_directory)
        except ExamNotFoundError:
            return None

    @application.get(_QUIZ_PATH_PREFIX + "{lecture_id}")
    async def show_quiz_form(lecture_id: str) -> Response:
        exam = await _load_exam(lecture_id)
        if exam is None:
            return _EXAM_NOT_FOUND_RESPONSE
        return Response(
            content=render_quiz_form(lecture_id, public_view(exam)),
            media_type="text/html",
        )

    @application.post(_QUIZ_PATH_PREFIX + "{lecture_id}")
    async def submit_quiz(lecture_id: str, request: Request) -> Response:
        exam = await _load_exam(lecture_id)
        if exam is None:
            return _EXAM_NOT_FOUND_RESPONSE
        body = await _read_bounded_body(request, _MAX_QUIZ_BODY_BYTES)
        if body is None:
            return _OVERSIZED_QUIZ_RESPONSE
        # Rate-limited by the authorized person the middleware already
        # identified, never by network address (ADR 0039's own rationale
        # for keying password throttling this way applies identically
        # here: every visitor shares one peer address behind the proxy).
        # `request.state.pos_email` is set by the middleware for every
        # request this handler can ever run for -- this route is not in
        # the middleware's unauthenticated path list -- so a missing
        # attribute here would mean that wiring broke, and should fail
        # loudly rather than fall back to a shared "" throttle bucket.
        try:
            await quiz_gate.throttled_wait(request.state.pos_email, lecture_id)
        except TooManyAttempts:
            return _TOO_MANY_ATTEMPTS_RESPONSE
        submitted_answers = parse_submitted_answers(_parse_form_fields(body))
        graded = grade(lecture_id, exam, submitted_answers)
        return Response(
            content=render_quiz_results(graded),
            media_type="text/html",
        )

    # Two separate trees, per ADR 0029: the site tree is what a container
    # image build would copy in, and the video library is meant to live on
    # a volume mounted at run time -- kept apart here even though both are
    # served from the same host today, so that split stays real later.
    application.mount(
        "/videos",
        StaticFiles(directory=video_library_directory, check_dir=False),
        name="videos",
    )
    application.mount(
        "/",
        StaticFiles(directory=site_directory, html=True, check_dir=False),
        name="site",
    )
    return application


def _account_reference(account: str, signing_secret: bytes) -> str:
    """A stable, non-reversible stand-in for an address in the log.

    Keyed with the signing secret, so the reference cannot be recomputed
    from a guessed address without that secret -- a plain hash of an email
    is trivially reversed by hashing a list of candidates. The same person
    always maps to the same reference, which keeps the audit trail, and it
    changes when the secret is rotated. Being hex, it also cannot carry a
    newline that would forge a second log line.
    """
    message = _ACCOUNT_REFERENCE_DOMAIN + account.encode("utf-8")
    digest = hmac.new(signing_secret, message, hashlib.sha256).hexdigest()
    return digest[:_ACCOUNT_REFERENCE_HEX_DIGITS]


def _is_unauthenticated_route(served_path: str, method: str) -> bool:
    return method in _UNAUTHENTICATED_METHODS_BY_PATH.get(served_path, frozenset())


def _is_exempt_path_with_other_method(served_path: str, method: str) -> bool:
    return served_path in _UNAUTHENTICATED_METHODS_BY_PATH and not (
        _is_unauthenticated_route(served_path, method)
    )


def _method_not_allowed_response(served_path: str) -> Response:
    """A plain refusal rather than the usual denial: the login redirect
    that denial sends a browser to is itself an exempt path, so a client
    that keeps HEAD across redirects would follow it forever."""
    allowed_methods = sorted(_UNAUTHENTICATED_METHODS_BY_PATH[served_path])
    return Response(
        status_code=405,
        headers={
            "Allow": ", ".join(allowed_methods),
            "Cache-Control": _PRIVATE_CACHE_DIRECTIVE,
        },
    )


def _denial_for(request: Request) -> Response:
    if request.url.path == _LANDING_PATH:
        return _PUBLIC_LANDING_RESPONSE
    if _prefers_html(request):
        # A browser following a shared link gets the form instead of the
        # Basic Auth prompt, which in-app browsers never show -- and the
        # form is told what was actually requested, so signing in can
        # return there instead of always landing on the library index.
        target = quote(request.url.path, safe="")
        return RedirectResponse(
            f"{_LOGIN_PATH}?{_NEXT_FIELD}={target}",
            status_code=303,
            headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE},
        )
    return _UNAUTHORIZED_RESPONSE


def _out_of_scope_response(request: Request, person: Credential) -> Response:
    """A restricted person asking for anything outside their prefixes: a
    browser is sent back to their one page, a script gets a plain 403."""
    if _prefers_html(request):
        return RedirectResponse(
            person.landing_path,
            status_code=303,
            headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE},
        )
    return Response(
        status_code=403, headers={"Cache-Control": _PRIVATE_CACHE_DIRECTIVE}
    )


def _signed_in_response(token: str, next_target: str) -> Response:
    response = RedirectResponse(next_target, status_code=303)
    response.headers["Cache-Control"] = _PRIVATE_CACHE_DIRECTIVE
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_LIFETIME_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


def _signed_out_response() -> Response:
    response = RedirectResponse(_LANDING_PATH, status_code=303)
    response.headers["Cache-Control"] = _PRIVATE_CACHE_DIRECTIVE
    # Mirrors the attributes `_signed_in_response` set on the cookie: a
    # `delete_cookie` call that does not match path/secure/httponly/samesite
    # leaves the original cookie in place and instructs the browser to
    # store a second, unrelated one instead of clearing it.
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


def _safe_redirect_target(candidate: str | None) -> str:
    """The post-login redirect, reduced to what is safe to send a signed-in
    visitor to: a same-site relative path, never anything that could send
    them somewhere else. Anything short of that -- missing, an absolute
    URL, a scheme, a protocol-relative "//", a backslash a browser would
    still read as a path separator, or a ".." segment -- falls back to the
    library index rather than being reflected unchecked. The path is also
    checked against where it will actually be routed once decoded and
    normalized, not just as typed, so it cannot land on the logout route
    under a spelling ("/sa%69r", "/./sair") that reads differently from
    "/sair" but resolves to the same place.
    """
    if candidate is None:
        return _DEFAULT_POST_LOGIN_TARGET
    if not candidate.startswith("/"):
        return _DEFAULT_POST_LOGIN_TARGET
    if candidate.startswith("//"):
        return _DEFAULT_POST_LOGIN_TARGET
    if "\\" in candidate:
        return _DEFAULT_POST_LOGIN_TARGET
    if ".." in candidate:
        return _DEFAULT_POST_LOGIN_TARGET
    if "\r" in candidate or "\n" in candidate:
        return _DEFAULT_POST_LOGIN_TARGET
    if _resolves_outside_its_own_spelling(candidate):
        return _DEFAULT_POST_LOGIN_TARGET
    return candidate


def _resolves_outside_its_own_spelling(candidate: str) -> bool:
    """Whether decoding and normalizing the candidate's path -- what the
    ASGI router does before it matches a route, and what a browser does to
    a Location header before it ever sends the follow-up request -- yields
    something other than the candidate itself, or yields the logout route.
    A path that only reaches "/sair" once decoded or dot-segments are
    resolved is exactly the spelling this exists to catch; any other
    mismatch between the raw and the resolved path is treated the same
    way, since nothing here can then be sure which one a browser or the
    server will actually act on.
    """
    decoded_path = _fully_decoded(urlsplit(candidate).path)
    if normpath(decoded_path) != decoded_path:
        return True
    return decoded_path == _LOGOUT_PATH


def _fully_decoded(path: str) -> str:
    for _ in range(_MAX_DECODE_PASSES):
        decoded = unquote(path)
        if decoded == path:
            return path
        path = decoded
    return path


async def _read_bounded_body(
    request: Request, max_bytes: int = _MAX_LOGIN_BODY_BYTES
) -> bytes | None:
    """Read the request body, or None when the caller sends more than the
    form at hand could possibly need."""
    declared_length = request.headers.get("content-length")
    if declared_length is not None and declared_length.isdigit():
        if int(declared_length) > max_bytes:
            return None
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > max_bytes:
            return None
    return body


def _parse_basic_auth(header: str | None) -> tuple[str, str] | None:
    if header is None or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header.removeprefix("Basic ")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    email, separator, password = decoded.partition(":")
    if not separator:
        return None
    return email, password


def _prefers_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _parse_form_fields(body: bytes) -> dict[str, str]:
    fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {name: values[0] for name, values in fields.items()}


app = create_app()
