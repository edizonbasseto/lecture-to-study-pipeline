"""Protective response headers for every response the study site sends.

Applied as a pure ASGI wrapper around the finished app -- outside even
Starlette's ServerErrorMiddleware -- so that early refusals (400, 401, 403,
503), unhandled-exception 500s, the login pages and every static file get
exactly the same headers: none of them can return before the headers are
added, and no shared module-level Response object is ever mutated.

The Content-Security-Policy admits inline code only by hash. The site's
inline scripts and styles are fixed constants, so their hashes are computed
from those very constants: changing one changes the policy with it, and a
generated page that still carries an older script must be regenerated.
"""

import base64
import hashlib
from collections.abc import Iterable

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Only meaningful over HTTPS, where the site is served; a browser ignores
# the header on a plain-HTTP response, so it is safe to always send.
_HSTS_MAX_AGE_SECONDS = 31_536_000


def content_security_policy(
    inline_scripts: Iterable[str], inline_styles: Iterable[str]
) -> str:
    """The policy for the site's own pages: same-origin everything, plus the
    exact inline scripts and styles given, and nothing that could frame the
    site, rebase its relative links or post a form elsewhere.

    `object-src` is deliberately left to `default-src 'self'` instead of
    'none': a browser renders a top-level PDF through an embedded viewer
    that a stricter `object-src` can block, and the site serves PDFs.
    """
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src {_sources_with_hashes(inline_scripts)}",
            f"style-src {_sources_with_hashes(inline_styles)}",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        )
    )


def _sources_with_hashes(inline_contents: Iterable[str]) -> str:
    return " ".join(("'self'", *map(_inline_source_hash, inline_contents)))


def _inline_source_hash(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


class SecurityHeadersMiddleware:
    """Sets the protective headers on every HTTP response, overriding any
    value an inner layer may already have chosen."""

    def __init__(self, app: ASGIApp, policy: str) -> None:
        self._app = app
        self._headers = {
            "Content-Security-Policy": policy,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "Strict-Transport-Security": f"max-age={_HSTS_MAX_AGE_SECONDS}",
            "X-Frame-Options": "DENY",
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._headers.items():
                    headers[name] = value
            await send(message)

        await self._app(scope, receive, send_with_headers)
