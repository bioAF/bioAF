"""fetch_open_access_pdf parses downloaded PDFs off the event loop, a bounded number at a time."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.services import lab_document_upload_service as lab
from app.services.literature import fulltext_service, upload_service


@pytest.fixture
def slow_parser(monkeypatch):
    state = {"running": 0, "peak": 0}
    lock = threading.Lock()

    async def fake_fetch_url(url):
        return b"%PDF-1.4", "x.pdf", "application/pdf"

    def slow_extract(pdf_bytes):
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.3)
        with lock:
            state["running"] -= 1
        return {"page_count": 1}

    monkeypatch.setattr(lab.LabDocumentUploadService, "_fetch_url", staticmethod(fake_fetch_url))
    monkeypatch.setattr(upload_service, "synchronous_extract_basic", slow_extract)
    return state


_LOC = [{"pdf_url": "https://w.example/x.pdf", "version": "publishedVersion"}]


@pytest.mark.asyncio
async def test_a_slow_parse_does_not_block_other_work(slow_parser):
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    t = asyncio.create_task(ticker())
    copy = await fulltext_service.fetch_open_access_pdf(_LOC)
    t.cancel()

    assert copy is not None
    assert ticks >= 10


@pytest.mark.asyncio
async def test_concurrent_parses_are_bounded(slow_parser):
    copies = await asyncio.gather(*(fulltext_service.fetch_open_access_pdf(_LOC) for _ in range(6)))

    assert all(copies)
    assert slow_parser["peak"] <= fulltext_service.MAX_CONCURRENT_PDF_PARSES
