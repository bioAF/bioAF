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
_HEADER_UNSAFE = frozenset('"\\/;,')


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


class TransferStalled(Exception):
    """The client accepted no bytes for the stall limit."""


class GuardedFileResponse(StreamingResponse):
    """Streams one prepared file, then runs ``on_close`` however the transfer ends.

    For downloads that hold server resources while they are read (data exports). ``on_close`` runs
    on every exit (success, a failed header send before the body starts, a later send failure, a
    disconnect, cancellation, a stall, the deadline), shielded and bounded, so the file and
    whatever it holds are released without relying on the body generator's ``finally``, which never
    runs for a generator that never started.

    A body send that does not complete within ``stall_seconds`` (the downstream accepted nothing)
    closes the stream, as do the client disconnecting and reaching ``deadline_seconds`` while still
    progressing. The outcome passed to ``on_close`` says which. Progress is a
    completed non-empty body send; an empty message never resets it. After the headers are sent a
    failure can only close the stream; ``Content-Length`` lets the client see it was cut short.
    """

    def __init__(
        self,
        path: str,
        *,
        size: int,
        filename: str,
        media_type: str,
        on_close,
        stall_seconds: float,
        on_start=None,
        deadline_seconds: float,
        cleanup_seconds: float = 10,
        chunk_size: int = 256 * 1024,
    ) -> None:
        self._path = path
        self._chunk_size = chunk_size
        self._on_close = on_close
        self._on_start = on_start
        self._stall_seconds = stall_seconds
        self._deadline_seconds = deadline_seconds
        self._cleanup_seconds = cleanup_seconds
        self.outcome: str | None = None
        super().__init__(
            self._chunks(),
            media_type=media_type,
            headers={
                "Content-Disposition": attachment_disposition(filename),
                "Content-Length": str(size),
                # Stream through nginx instead of spooling the archive to its temporary files.
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-store",
            },
        )

    async def _chunks(self):
        with open(self._path, "rb") as f:
            while chunk := await anyio.to_thread.run_sync(f.read, self._chunk_size):
                yield chunk

    def _guard(self, send: Send) -> Send:
        async def guarded(message) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                try:
                    with anyio.fail_after(self._stall_seconds):
                        await send(message)
                except TimeoutError:
                    raise TransferStalled() from None
            else:
                await send(message)

        return guarded

    async def _stream(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send the body while watching for the client going away.

        A server may keep accepting sends after the peer has gone (uvicorn drops them silently), so
        a completed send loop alone does not mean the body was delivered; ``http.disconnect`` stops
        the stream and is recorded as such.
        """
        error: Exception | None = None
        async with anyio.create_task_group() as group:

            async def watch() -> None:
                while (await receive())["type"] != "http.disconnect":
                    pass
                self.outcome = "disconnected"
                group.cancel_scope.cancel()

            group.start_soon(watch)
            try:
                # Not super().__call__: under ASGI < 2.4 Starlette runs its own disconnect listener,
                # which would race this one and end the stream looking complete.
                await self.stream_response(self._guard(send))
                if self.outcome != "disconnected":
                    self.outcome = "sent"
            except TransferStalled:
                self.outcome = "stalled"
            except Exception as exc:
                error = exc
            finally:
                group.cancel_scope.cancel()
        if error is not None:
            raise error

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.outcome = "incomplete"
        if self._on_start is not None:
            self._on_start()
        try:
            with anyio.move_on_after(self._deadline_seconds) as deadline:
                await self._stream(scope, receive, send)
            if deadline.cancelled_caught:
                self.outcome = "deadline"
        except BaseException:
            self.outcome = "failed"
            raise
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
                with anyio.move_on_after(self._cleanup_seconds):
                    await self._on_close(self.outcome)
