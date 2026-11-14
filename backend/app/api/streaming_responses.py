"""Streamed and downloadable responses shared by routes.

- ``ClosingStreamingResponse`` closes its body iterator on every exit, so a body's own cleanup (stopping
  Terraform, committing a session) never waits for garbage collection after a client disconnects.
- ``attachment_disposition`` builds a ``Content-Disposition`` header any HTTP stack can encode.
"""

from __future__ import annotations

import unicodedata
from urllib.parse import quote

import anyio
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

_FALLBACK_FILENAME = "download"
# Never in a filename parameter: quoting/parameter syntax and path separators.
_HEADER_UNSAFE = set('"\\/;,')


class ClosingStreamingResponse(StreamingResponse):
    """A streaming response that closes its body on every exit.

    Starlette abandons the body generator when a client disconnects, which would
    leave stopping Terraform and committing the session to garbage collection.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


def _clean(name: str) -> str:
    """The name without controls, quotes, separators or surrounding whitespace."""
    kept = "".join(
        "_" if (c in _HEADER_UNSAFE or unicodedata.category(c).startswith("C")) else c
        for c in unicodedata.normalize("NFC", name or "")
    )
    return kept.strip(" ._") or _FALLBACK_FILENAME


def attachment_disposition(filename: str) -> str:
    """``attachment`` with a quoted printable-ASCII ``filename`` and a UTF-8 ``filename*``.

    Clients that read RFC 6266 extended parameters (every current browser, and the frontend's
    parser, which prefers it) get the real name; anything else gets the ASCII fallback. The header
    value is pure ASCII, so it can never fail to encode after work has been committed.
    """
    clean = _clean(filename)
    ascii_name = "".join(c if 32 < ord(c) < 127 else "_" for c in clean).strip("_") or _FALLBACK_FILENAME
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean, safe='')}"
