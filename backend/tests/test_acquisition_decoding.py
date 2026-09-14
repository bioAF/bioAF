"""plan_8_2 section 1.2 through acquisition: the decoded artifact records what it was decoded from, and an
author-result table that arrived but could not be interpreted never costs the matrix acquired beside it.
"""

import gzip

import httpx
import pytest

from app.services.validation_driver_service import ValidationDriverService
from tests.test_input_choice_driver import (
    _BASE,
    _MATRIX,
    _MATRIX_TEXT,
    _TABLE,
    _TABLE_TEXT,
    _Client,
    _listing,
    _mapping,
    _patch,
    _stage2_study,
    _Storage,
    _stream,
)


def _fetcher(table_bytes: bytes):
    async def fetch(url):
        if url == _BASE + _MATRIX:
            return gzip.compress(_MATRIX_TEXT.encode())
        if url == _BASE + _TABLE:
            return table_bytes
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))

    return fetch


async def _acquire(session, study, storage, table_bytes):
    await ValidationDriverService._handle_acquiring_processed(
        session,
        study,
        fetcher=_fetcher(table_bytes),
        storage_adapter=storage,
        inventory_fetcher=_listing,
        stream=_stream,
    )


def _choice(monkeypatch):
    _patch(monkeypatch, _Client({"primary_matrix": _MATRIX, "author_table": _TABLE, "mapping": _mapping()}))


@pytest.mark.asyncio
async def test_a_utf16_author_table_is_acquired_decoded_with_its_provenance(session, admin_user, monkeypatch):
    _choice(monkeypatch)
    study = await _stage2_study(session, admin_user)
    storage = _Storage()
    await _acquire(session, study, storage, gzip.compress(b"\xff\xfe" + _TABLE_TEXT.encode("utf-16-le")))

    files = {f["filename"]: f for f in study.evidence_json["deposit"]["files"]}
    table = files[_TABLE]
    assert storage.files[table["storage_uri"]] == _TABLE_TEXT
    assert table["decoding"]["encoding"] == "utf-16-le"
    assert table["decoding"]["compression"] == "gzip"
    assert table["decoding"]["decoder_version"] >= 1
    assert len(table["decoding"]["source_checksum"]) == 64
    assert study.state == "inspecting_deposit"


@pytest.mark.asyncio
async def test_an_uninterpretable_author_table_keeps_the_acquired_matrix(session, admin_user, monkeypatch):
    _choice(monkeypatch)
    study = await _stage2_study(session, admin_user)
    storage = _Storage()
    await _acquire(session, study, storage, gzip.compress(_TABLE_TEXT.encode("utf-16-le")))  # no byte-order mark

    evidence = study.evidence_json
    assert [f["filename"] for f in evidence["deposit"]["files"]] == [_MATRIX]
    failed = evidence["author_table_failed"]
    assert failed["filename"] == _TABLE
    assert failed["arrived"] is True
    assert "arrived but could not be interpreted" in failed["reason"]
    assert failed["decoding"]["status"] == "unresolved"
    assert study.state == "inspecting_deposit"
