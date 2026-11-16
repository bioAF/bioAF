"""Ordered whole-object MD5 shared by every storage backend (01B.2).

A backend supplies a blocking ``read(n)`` over exactly one object version and a ``close``. Each
chunk is read and hashed in a worker thread, in order, so a large FASTQ never blocks the event loop
and nothing is staged on the host. The byte count must equal the identity's size, or the object
changed under the read. The stream is closed on every exit, cancellation included.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable

from app.adapters.models import DIGEST_CHUNK_BYTES, ObjectChanged, ObjectDigest

Progress = Callable[[int], Awaitable[None]]


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
