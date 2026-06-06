"""SDRs surface in global search (AC-C12, F-LKC-09)."""

import pytest

from app.services.sdr_service import SdrService
from app.services.search_service import FULL_SEARCH_TYPES, SearchService


async def _make_sdr(session, org_id, uid, **kw):
    defaults = dict(
        title="Use STARsolo over CellRanger",
        decision="Standardize alignment on STARsolo.",
        justification="Better multimapping handling at our depths.",
    )
    defaults.update(kw)
    return await SdrService.create_sdr(session, org_id=org_id, user_id=uid, **defaults)


@pytest.mark.asyncio
async def test_sdr_is_a_full_search_type():
    # AC-C12
    assert "sdr" in FULL_SEARCH_TYPES


@pytest.mark.asyncio
async def test_full_search_finds_sdr_by_title_and_content(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await _make_sdr(session, org_id, uid)
    by_title, total, counts = await SearchService.full_search(
        session, org_id, "STARsolo", entity_types=["sdr"], count_types=["sdr"]
    )
    assert total == 1
    assert by_title[0]["entity_type"] == "sdr"
    assert by_title[0]["url"].startswith("/lab-knowledge/decision-records")
    assert counts["sdr"] == 1
    by_just, total_just, _ = await SearchService.full_search(
        session, org_id, "multimapping", entity_types=["sdr"], count_types=["sdr"]
    )
    assert total_just == 1


@pytest.mark.asyncio
async def test_full_search_matches_sdr_number_string(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid)
    results, total, _ = await SearchService.full_search(
        session, org_id, f"SDR-{sdr.sdr_number:03d}", entity_types=["sdr"], count_types=["sdr"]
    )
    assert total == 1 and results[0]["entity_id"] == sdr.id


@pytest.mark.asyncio
async def test_quick_search_finds_sdr(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await _make_sdr(session, org_id, uid, title="Viability threshold at 70 percent")
    hits = await SearchService.quick_search(session, org_id, "Viability")
    assert any(h["entity_type"] == "sdr" for h in hits)
