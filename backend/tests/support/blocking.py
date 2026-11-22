"""A provider SDK call that blocks in its worker thread until a test releases it."""

from __future__ import annotations

import asyncio
import threading


class BlockingCall:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def __call__(self, *args, **kwargs):
        self.started.set()
        try:
            self.release.wait(10)
        finally:
            self.finished.set()

    async def wait_until_started(self):
        assert await asyncio.to_thread(self.started.wait, 5)

    async def stop(self):
        self.release.set()
        assert await asyncio.to_thread(self.finished.wait, 5)
