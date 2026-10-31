"""PDF parsing for open-access copies runs in a separate, killable process, a bounded number at a time.

PyMuPDF does not support multithreaded use, so each parse gets its own short-lived process. These
tests use real small PDFs for the library path and small stand-in worker scripts for the slow,
crashing and overrunning cases."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.services import lab_document_upload_service as lab
from app.services.literature import fulltext_service
from tests.test_literature_upload_api import _build_test_pdf

_SLOW = (
    "-c",
    "import sys, os, time; sys.stdin.buffer.read(); open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)",
)


def _slow_worker(monkeypatch, tmp_path, name="pid"):
    pidfile = tmp_path / name
    monkeypatch.setattr(fulltext_service, "_PARSE_WORKER_ARGS", (*_SLOW, str(pidfile)))
    return pidfile


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_a_real_pdf_is_parsed_in_a_worker_process():
    extracted = await fulltext_service.parse_pdf(_build_test_pdf())

    assert extracted["page_count"] == 1
    assert extracted["doi"] == "10.1038/s41592-uptest-1"
    assert extracted["full_text"].startswith("[Page 1]")


@pytest.mark.asyncio
async def test_concurrent_real_parses_each_succeed():
    pdf = _build_test_pdf()

    results = await asyncio.gather(
        *(fulltext_service.parse_pdf(pdf) for _ in range(fulltext_service.MAX_CONCURRENT_PDF_PARSES))
    )

    assert [r["page_count"] for r in results] == [1] * fulltext_service.MAX_CONCURRENT_PDF_PARSES


@pytest.mark.asyncio
async def test_a_slow_parse_does_not_block_other_work(monkeypatch, tmp_path):
    _slow_worker(monkeypatch, tmp_path)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    t = asyncio.create_task(ticker())
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            await fulltext_service.parse_pdf(b"%PDF-1.4")
    t.cancel()

    assert ticks >= 20


@pytest.mark.asyncio
async def test_an_overrunning_parse_is_killed_and_frees_its_slot(monkeypatch, tmp_path):
    pidfile = _slow_worker(monkeypatch, tmp_path)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            await fulltext_service.parse_pdf(b"%PDF-1.4")

    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 2
    while _alive(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert not _alive(pid)
    assert fulltext_service._parses_running == 0


@pytest.mark.asyncio
async def test_parses_beyond_the_limit_are_refused_not_queued(monkeypatch, tmp_path):
    _slow_worker(monkeypatch, tmp_path)
    running = [
        asyncio.create_task(fulltext_service.parse_pdf(b"%PDF-1.4"))
        for _ in range(fulltext_service.MAX_CONCURRENT_PDF_PARSES)
    ]
    await asyncio.sleep(0.1)

    with pytest.raises(fulltext_service.PdfParserBusy):
        await fulltext_service.parse_pdf(b"%PDF-1.4")

    for t in running:
        t.cancel()
    await asyncio.gather(*running, return_exceptions=True)
    assert fulltext_service._parses_running == 0


@pytest.mark.asyncio
async def test_a_crashed_worker_is_an_unusable_copy(monkeypatch):
    monkeypatch.setattr(fulltext_service, "_PARSE_WORKER_ARGS", ("-c", "import sys; sys.exit(3)"))

    with pytest.raises(fulltext_service.PdfParseError):
        await fulltext_service.parse_pdf(b"%PDF-1.4")

    urls = ["https://a.example/x.pdf", "https://b.example/y.pdf"]
    fetched = []

    async def fake_fetch_url(url):
        fetched.append(url)
        return b"%PDF-1.4", "x.pdf", "application/pdf"

    monkeypatch.setattr(lab.LabDocumentUploadService, "_fetch_url", staticmethod(fake_fetch_url))
    copy = await fulltext_service.fetch_open_access_pdf([{"pdf_url": u, "version": None} for u in urls])

    assert copy is None
    assert fetched == urls
    assert fulltext_service._parses_running == 0
