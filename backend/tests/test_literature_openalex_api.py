"""OpenAlex research context and open-access endpoints. OpenAlex is mocked; no live egress."""

from __future__ import annotations

import httpx
import pytest

from app.services.literature.sources import openalex

_WORK = {
    "id": "https://openalex.org/W99",
    "is_retracted": False,
    "cited_by_count": 7,
    "counts_by_year": [{"year": 2024, "cited_by_count": 7}],
    "locations": [],
}


async def _paper(client, token, **fields) -> int:
    r = await client.post(
        "/api/literature/papers",
        json={"title": "A paper", "authors": [{"given": "A", "family": "B"}], **fields},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.fixture
def lookups(monkeypatch):
    calls: list[dict] = []

    async def fake_fetch_work(*, doi, pmid, api_key):
        calls.append({"doi": doi, "pmid": pmid, "api_key": api_key})
        return _WORK

    monkeypatch.setattr(openalex, "fetch_work", fake_fetch_work)
    return calls


@pytest.mark.asyncio
async def test_context_for_a_matched_paper(client, admin_token, viewer_token, lookups):
    pid = await _paper(client, admin_token, doi="10.1/ctx")

    r = await client.get(f"/api/literature/papers/{pid}/openalex", headers={"Authorization": f"Bearer {viewer_token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["openalex_id"] == "W99"
    assert body["is_retracted"] is False
    assert body["cited_by_count"] == 7
    assert body["counts_by_year"] == [{"year": 2024, "cited_by_count": 7}]
    assert lookups[-1]["doi"] == "10.1/ctx"


@pytest.mark.asyncio
async def test_context_uses_the_orgs_openalex_key(client, admin_token, admin_user, session, lookups):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.patch("/api/literature/sources/openalex", json={"api_key": "org-key"}, headers=headers)
    pid = await _paper(client, admin_token, doi="10.1/key")

    await client.get(f"/api/literature/papers/{pid}/openalex", headers=headers)

    assert lookups[-1]["api_key"] == "org-key"


@pytest.mark.asyncio
async def test_context_unavailable_without_identifier_or_match(client, admin_token, monkeypatch):
    async def no_match(*, doi, pmid, api_key):
        return None

    monkeypatch.setattr(openalex, "fetch_work", no_match)
    headers = {"Authorization": f"Bearer {admin_token}"}
    for pid in (await _paper(client, admin_token), await _paper(client, admin_token, doi="10.1/none")):
        r = await client.get(f"/api/literature/papers/{pid}/openalex", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["available"] is False


@pytest.mark.asyncio
async def test_context_outage_is_a_502_with_a_plain_message(client, admin_token, monkeypatch):
    async def outage(*, doi, pmid, api_key):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(openalex, "fetch_work", outage)
    pid = await _paper(client, admin_token, doi="10.1/down")

    r = await client.get(f"/api/literature/papers/{pid}/openalex", headers={"Authorization": f"Bearer {admin_token}"})

    assert r.status_code == 502
    assert r.json()["detail"] == "OpenAlex could not be reached right now."


@pytest.mark.asyncio
async def test_context_requires_authentication(client, admin_token, lookups):
    pid = await _paper(client, admin_token, doi="10.1/auth")

    r = await client.get(f"/api/literature/papers/{pid}/openalex")

    assert r.status_code == 401
    assert lookups == []


@pytest.mark.asyncio
async def test_context_never_reads_another_orgs_paper(client, admin_token, session, lookups):
    from app.models.organization import Organization
    from app.services.literature import paper_service

    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    paper = await paper_service.create_paper(
        session,
        org_id=other.id,
        user_id=None,
        title="Theirs",
        authors=[],
        doi="10.1/theirs",
        provenance="source_search",
    )
    await session.commit()

    r = await client.get(
        f"/api/literature/papers/{paper.id}/openalex", headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert r.status_code == 404
    assert lookups == []


# --- POST /papers/{id}/fetch-open-access ------------------------------------

from tests.test_literature_upload_api import _build_test_pdf, fake_literature_bucket  # noqa: E402, F401

_OWN_DOI = "10.1038/s41592-uptest-1"  # the DOI printed in _build_test_pdf()


def _loc(url, version="publishedVersion", is_oa=True):
    return {"is_oa": is_oa, "pdf_url": url, "landing_page_url": None, "version": version, "license": "cc-by"}


@pytest.fixture
def oa(monkeypatch):
    """Serve a configurable OpenAlex work and per-URL download results."""
    from app.services import lab_document_upload_service as lab
    from app.services.literature import upload_service

    state = {"work": None, "bodies": {}, "fetched": [], "scheduled": []}

    async def fake_fetch_work(*, doi, pmid, api_key):
        if isinstance(state["work"], Exception):
            raise state["work"]
        return state["work"]

    async def fake_fetch_url(url):
        state["fetched"].append(url)
        body = state["bodies"].get(url)
        if isinstance(body, Exception):
            raise body
        if callable(body):
            return await body()
        return body, "x.pdf", "application/pdf"

    async def fake_schedule(*, paper_id, pdf_bytes, user_id, api_key_id=None):
        state["scheduled"].append(paper_id)

    monkeypatch.setattr(openalex, "fetch_work", fake_fetch_work)
    monkeypatch.setattr(lab.LabDocumentUploadService, "_fetch_url", staticmethod(fake_fetch_url))
    monkeypatch.setattr(upload_service, "schedule_extraction", fake_schedule)
    return state


async def _fetch(client, token, pid):
    return await client.post(
        f"/api/literature/papers/{pid}/fetch-open-access", headers={"Authorization": f"Bearer {token}"}
    )


@pytest.mark.asyncio
async def test_fetch_prefers_the_published_version_and_records_it(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, doi=_OWN_DOI)
    oa["work"] = {
        "id": "https://openalex.org/W1",
        "locations": [_loc("https://r.example/sub.pdf", "submittedVersion"), _loc("https://j.example/pub.pdf")],
    }
    oa["bodies"] = {"https://j.example/pub.pdf": _build_test_pdf(), "https://r.example/sub.pdf": _build_test_pdf()}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 200, r.text
    body = r.json()
    assert oa["fetched"] == ["https://j.example/pub.pdf"]
    assert body["has_pdf"] is True
    assert body["full_text_version"] == "publishedVersion"
    assert body["full_text_source"] == "openalex"
    assert body["full_text_source_url"] == "https://j.example/pub.pdf"
    assert body["full_text_acquired_at"] is not None
    assert oa["scheduled"] == [pid]


@pytest.mark.asyncio
async def test_fetch_skips_html_corrupt_and_failed_copies(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    from app.exceptions import ValidationError

    pid = await _paper(client, admin_token, doi=_OWN_DOI)
    oa["work"] = {
        "id": "https://openalex.org/W1",
        "locations": [
            _loc("https://a.example/landing.pdf"),
            _loc("https://b.example/broken.pdf"),
            _loc("https://c.example/accepted.pdf", "acceptedVersion"),
        ],
    }
    oa["bodies"] = {
        "https://a.example/landing.pdf": b"<html>Sign in</html>",
        "https://b.example/broken.pdf": b"%PDF-1.4 not really a pdf",
        "https://c.example/accepted.pdf": _build_test_pdf(),
    }

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 200, r.text
    assert r.json()["full_text_version"] == "acceptedVersion"

    oa["bodies"]["https://c.example/accepted.pdf"] = ValidationError("Could not fetch URL (HTTP 403)")
    pid2 = await _paper(client, admin_token, title="Second", doi="10.1/second")
    r = await _fetch(client, admin_token, pid2)
    assert r.status_code == 404
    assert r.json()["detail"] == "No openly downloadable copy was found."


@pytest.mark.asyncio
async def test_fetch_tries_at_most_three_copies(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, doi=_OWN_DOI)
    urls = [f"https://h{i}.example/x.pdf" for i in range(5)]
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc(u) for u in urls]}
    oa["bodies"] = {u: b"<html></html>" for u in urls}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 404
    assert oa["fetched"] == urls[:3]
    assert oa["scheduled"] == []


@pytest.mark.asyncio
async def test_fetch_ignores_copies_not_marked_open_access(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, doi=_OWN_DOI)
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://p.example/x.pdf", is_oa=False)]}
    oa["bodies"] = {"https://p.example/x.pdf": _build_test_pdf()}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 404
    assert oa["fetched"] == []


@pytest.mark.asyncio
async def test_fetch_time_budget_is_a_504(client, admin_token, fake_literature_bucket, oa, monkeypatch):  # noqa: F811
    import asyncio

    from app.api.literature import papers

    monkeypatch.setattr(papers, "_OPEN_ACCESS_BUDGET_SECONDS", 0.05)
    pid = await _paper(client, admin_token, doi=_OWN_DOI)

    async def slow():
        await asyncio.sleep(1)

    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://slow.example/x.pdf")]}
    oa["bodies"] = {"https://slow.example/x.pdf": slow}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 504
    assert oa["scheduled"] == []


@pytest.mark.asyncio
async def test_fetch_lookup_outage_is_a_502(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, doi=_OWN_DOI)
    oa["work"] = httpx.ConnectError("down")

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 502
    assert r.json()["detail"] == "OpenAlex could not be reached right now."


@pytest.mark.asyncio
async def test_fetch_rejects_a_copy_of_a_different_paper(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, doi="10.1/expected")
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://w.example/x.pdf")]}
    oa["bodies"] = {"https://w.example/x.pdf": _build_test_pdf()}  # carries a different DOI

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "identity_mismatch"
    after = (
        await client.get(f"/api/literature/papers/{pid}", headers={"Authorization": f"Bearer {admin_token}"})
    ).json()
    assert after["has_pdf"] is False and after["full_text_source"] is None
    assert oa["scheduled"] == []


@pytest.mark.asyncio
async def test_fetch_never_merges_into_another_paper(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    holder = await _paper(client, admin_token, title="Holder", doi=_OWN_DOI)
    pid = await _paper(client, admin_token, title="PMID only", pmid="123")
    oa["work"] = {
        "id": "https://openalex.org/W1",
        "doi": f"https://doi.org/{_OWN_DOI}",
        "locations": [_loc("https://w.example/x.pdf")],
    }
    oa["bodies"] = {"https://w.example/x.pdf": _build_test_pdf()}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "doi_conflict"
    assert r.json()["detail"]["other_paper_id"] == holder
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert (await client.get(f"/api/literature/papers/{holder}", headers=headers)).status_code == 200
    assert oa["scheduled"] == []


@pytest.mark.asyncio
async def test_fetch_backfills_the_openalex_doi_for_a_pmid_only_paper(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, pmid="123")
    oa["work"] = {
        "id": "https://openalex.org/W1",
        "doi": f"https://doi.org/{_OWN_DOI.upper()}",
        "locations": [_loc("https://w.example/x.pdf")],
    }
    oa["bodies"] = {"https://w.example/x.pdf": _build_test_pdf()}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 200, r.text
    assert r.json()["doi"] == _OWN_DOI


@pytest.mark.asyncio
async def test_fetch_needs_storage_before_downloading(client, admin_token, oa):
    pid = await _paper(client, admin_token, doi=_OWN_DOI)
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://w.example/x.pdf")]}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 503
    assert oa["fetched"] == []


@pytest.mark.asyncio
async def test_fetch_requires_upload_permission(client, admin_token, viewer_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, doi=_OWN_DOI)

    r = await _fetch(client, viewer_token, pid)

    assert r.status_code == 403
    assert oa["fetched"] == []


@pytest.mark.asyncio
async def test_fetch_never_touches_another_orgs_paper(client, admin_token, session, fake_literature_bucket, oa):  # noqa: F811
    from app.models.organization import Organization
    from app.services.literature import paper_service

    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    paper = await paper_service.create_paper(
        session, org_id=other.id, user_id=None, title="Theirs", authors=[], doi=_OWN_DOI, provenance="source_search"
    )
    await session.commit()
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://w.example/x.pdf")]}

    r = await _fetch(client, admin_token, paper.id)

    assert r.status_code == 404
    assert oa["fetched"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data.pdf", "http://127.0.0.1:8000/x.pdf"])
async def test_fetch_refuses_internal_addresses_with_the_real_downloader(
    client,
    admin_token,
    fake_literature_bucket,  # noqa: F811
    monkeypatch,
    url,
):
    from app.services.literature import upload_service

    scheduled = []

    async def fake_fetch_work(*, doi, pmid, api_key):
        return {"id": "https://openalex.org/W1", "locations": [_loc(url)]}

    async def fake_schedule(**kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(openalex, "fetch_work", fake_fetch_work)
    monkeypatch.setattr(upload_service, "schedule_extraction", fake_schedule)
    pid = await _paper(client, admin_token, doi=_OWN_DOI)

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 404
    assert scheduled == []


# --- Identity evidence: only DOIs printed on the first page belong to the copy ---


def _pdf(*pages: str) -> bytes:
    pytest.importorskip("fitz")
    import fitz  # type: ignore

    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    out = doc.tobytes()
    doc.close()
    return out


async def _unchanged(client, token, pid):
    after = (await client.get(f"/api/literature/papers/{pid}", headers={"Authorization": f"Bearer {token}"})).json()
    assert after["has_pdf"] is False
    assert after["full_text_source"] is None
    assert after["extraction_status"] != "pending"


@pytest.mark.asyncio
async def test_fetch_rejects_a_paper_that_cites_this_one(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, doi="10.5555/paper-a")
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://w.example/b.pdf")]}
    oa["bodies"] = {
        "https://w.example/b.pdf": _pdf("doi:10.5555/paper-b\nPaper B", "References\n1. doi:10.5555/paper-a")
    }

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "identity_mismatch"
    await _unchanged(client, admin_token, pid)
    assert oa["scheduled"] == []


@pytest.mark.asyncio
async def test_fetch_does_not_attach_a_copy_with_several_first_page_dois(
    client,
    admin_token,
    fake_literature_bucket,  # noqa: F811
    oa,
):
    pid = await _paper(client, admin_token, doi="10.5555/paper-a")
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://w.example/b.pdf")]}
    oa["bodies"] = {"https://w.example/b.pdf": _pdf("doi:10.5555/paper-b\nAs shown in doi:10.5555/paper-a")}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "identity_unconfirmed"
    await _unchanged(client, admin_token, pid)
    assert oa["scheduled"] == []


@pytest.mark.asyncio
async def test_fetch_attaches_a_copy_with_no_doi_on_its_first_page(client, admin_token, fake_literature_bucket, oa):  # noqa: F811
    pid = await _paper(client, admin_token, doi="10.5555/paper-a")
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://w.example/a.pdf")]}
    oa["bodies"] = {"https://w.example/a.pdf": _pdf("Paper A, accepted manuscript", "References\n1. doi:10.5555/other")}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 200, r.text
    assert r.json()["full_text_source"] == "openalex"
    assert oa["scheduled"] == [pid]


# --- Storage: an acquired copy is attached only once it is saved ---


@pytest.mark.asyncio
@pytest.mark.parametrize("replacing", [False, True])
async def test_fetch_storage_failure_changes_nothing(
    client,
    admin_token,
    session,
    fake_literature_bucket,  # noqa: F811
    oa,
    monkeypatch,
    replacing,
):
    from app.models.literature import LiteraturePaper
    from app.services.literature import upload_service

    pid = await _paper(client, admin_token, pmid="123")
    paper = await session.get(LiteraturePaper, pid)
    if replacing:
        paper.gcs_pdf_uri = "gs://bioaf-literature-test/papers/old.pdf"
        paper.full_text_version = "submittedVersion"
        paper.full_text_source = "openalex"
        paper.extraction_status = "complete"
    prior = {
        k: getattr(paper, k) for k in ("gcs_pdf_uri", "full_text_version", "full_text_source", "extraction_status")
    }
    await session.commit()

    async def failed_write(_session, *, paper_id, pdf_bytes):
        return None

    monkeypatch.setattr(upload_service, "upload_pdf_to_gcs", failed_write)
    oa["work"] = {
        "id": "https://openalex.org/W1",
        "doi": f"https://doi.org/{_OWN_DOI}",
        "locations": [_loc("https://w.example/x.pdf")],
    }
    oa["bodies"] = {"https://w.example/x.pdf": _build_test_pdf()}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 503
    assert r.json()["detail"] == "The PDF could not be saved, so it was not attached. Try again later."
    session.expire_all()
    paper = await session.get(LiteraturePaper, pid)
    assert {k: getattr(paper, k) for k in prior} == prior
    assert paper.doi is None
    assert oa["scheduled"] == []


# --- Parsing runs off the event loop ---


@pytest.mark.asyncio
async def test_fetch_parse_overrunning_the_budget_is_a_504_and_attaches_nothing(
    client,
    admin_token,
    fake_literature_bucket,  # noqa: F811
    oa,
    monkeypatch,
):
    import time

    from app.api.literature import papers
    from app.services.literature import fulltext_service

    monkeypatch.setattr(
        fulltext_service, "_PARSE_WORKER_ARGS", ("-c", "import sys, time; sys.stdin.buffer.read(); time.sleep(30)")
    )
    monkeypatch.setattr(papers, "_OPEN_ACCESS_BUDGET_SECONDS", 0.5)
    pid = await _paper(client, admin_token, doi=_OWN_DOI)
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://w.example/x.pdf")]}
    oa["bodies"] = {"https://w.example/x.pdf": _build_test_pdf()}

    started = time.monotonic()
    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 504
    assert time.monotonic() - started < 2
    await _unchanged(client, admin_token, pid)
    assert oa["scheduled"] == []
    assert fulltext_service._parses_running == 0


@pytest.mark.asyncio
async def test_fetch_when_parsing_is_at_capacity_is_a_429(client, admin_token, fake_literature_bucket, oa, monkeypatch):  # noqa: F811
    from app.services.literature import fulltext_service

    monkeypatch.setattr(fulltext_service, "_parses_running", fulltext_service.MAX_CONCURRENT_PDF_PARSES)
    pid = await _paper(client, admin_token, doi=_OWN_DOI)
    oa["work"] = {"id": "https://openalex.org/W1", "locations": [_loc("https://w.example/x.pdf")]}
    oa["bodies"] = {"https://w.example/x.pdf": _build_test_pdf()}

    r = await _fetch(client, admin_token, pid)

    assert r.status_code == 429
    assert r.json()["detail"] == "bioAF is busy reading other PDFs. Try again in a minute."
    await _unchanged(client, admin_token, pid)
    assert oa["scheduled"] == []
