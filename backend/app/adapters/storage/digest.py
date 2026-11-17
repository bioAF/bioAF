"""Ordered whole-object MD5 shared by every storage backend (01B.2).

A backend supplies a blocking ``read(n)`` over exactly one object version and a ``close``. Each
chunk is read and hashed in a worker thread, in order, so a large FASTQ never blocks the event loop
and nothing is staged on the host. The byte count must equal the identity's size, or the object
changed under the read. The stream is closed on every exit, cancellation included.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.adapters.models import DIGEST_CHUNK_BYTES, ObjectChanged, ObjectDigest
from app.exceptions import ValidationError

Progress = Callable[[int], Awaitable[None]]

_HEX_MD5 = re.compile(r"^[0-9a-f]{32}$")


def normalize_md5(value: Any, *, allow_base64: bool = False) -> str:
    """Lowercase 32-character hex MD5, or ValidationError.

    ``allow_base64`` accepts the base64 form GCS reports (``md5Hash``); an explicit expectation from a
    caller or CSV must already be hex.
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if _HEX_MD5.match(candidate):
            return candidate
        if allow_base64:
            try:
                raw = base64.b64decode(value.strip(), validate=True)
            except (binascii.Error, ValueError):
                raw = b""
            if len(raw) == 16:
                return raw.hex()
    raise ValidationError("MD5 checksum must be 32 hexadecimal characters")


async def ordered_md5(
    read: Callable[[int], bytes],
    close: Callable[[], None],
    *,
    uri: str,
    version: str,
    expected_size: int,
    chunk_size: int = DIGEST_CHUNK_BYTES,
    on_progress: Progress | None = None,
) -> ObjectDigest:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.md5(usedforsecurity=False)
    done = 0

    def step() -> int:
        chunk = read(chunk_size)
        if chunk:
            digest.update(chunk)
        return len(chunk)

    try:
        while True:
            n = await asyncio.to_thread(step)
            if n == 0:
                break
            done += n
            if done > expected_size:
                raise ObjectChanged(uri)
            if on_progress is not None:
                await on_progress(done)
    finally:
        try:
            close()
        except Exception:
            pass
    if done != expected_size:
        raise ObjectChanged(uri)
    return ObjectDigest(uri=uri, hex=digest.hexdigest(), size_bytes=done, version=version, method="streamed")
