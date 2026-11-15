"""The experiments list as a picker pages through it (issues #104, #105).

Every eligible experiment is reachable page by page: the order is stable (newest first, then id) so offset pages
never skip or repeat rows that share a creation time; ``name`` matches names only; a page holds at most 100; and
deleted experiments are left out unless asked for by status. Only the caller's organization is listed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app.models.experiment import Experiment
from app.models.organization import Organization

pytestmark = pytest.mark.asyncio


async def _seed(session, org_id: int, names: list[str], **fields) -> list[Experiment]:
    made = [Experiment(name=n, organization_id=org_id, **fields) for n in names]
    session.add_all(made)
    await session.flush()
    return made


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_pages_cover_rows_that_share_a_creation_time_exactly_once(client, session, admin_token, admin_user):
    made = await _seed(session, admin_user.organization_id, [f"Same {i}" for i in range(7)])
    same = datetime(2026, 11, 1, tzinfo=timezone.utc)
    await session.execute(update(Experiment).where(Experiment.id.in_([e.id for e in made])).values(created_at=same))
    await session.commit()
    seen: list[int] = []
    for page in range(1, 5):
        r = await client.get(f"/api/experiments?page={page}&page_size=2", headers=_auth(admin_token))
        seen += [e["id"] for e in r.json()["experiments"]]
    assert sorted(seen) == sorted(e.id for e in made) and len(seen) == len(set(seen))


async def test_name_matches_names_only(client, session, admin_token, admin_user):
    await _seed(session, admin_user.organization_id, ["Liver atlas"])
    await _seed(session, admin_user.organization_id, ["Other"], hypothesis="liver regeneration")
    await session.commit()
    r = await client.get("/api/experiments?name=liver", headers=_auth(admin_token))
    assert [e["name"] for e in r.json()["experiments"]] == ["Liver atlas"]


@pytest.mark.parametrize("size", [0, -1, 101, 500])
async def test_a_page_holds_one_to_one_hundred(client, admin_token, size):
    r = await client.get(f"/api/experiments?page_size={size}", headers=_auth(admin_token))
    assert r.status_code == 422


async def test_deleted_experiments_are_listed_only_when_asked_for(client, session, admin_token, admin_user):
    await _seed(session, admin_user.organization_id, ["Kept"])
    await _seed(session, admin_user.organization_id, ["Gone"], status="deleted")
    await session.commit()
    default = await client.get("/api/experiments?page_size=100", headers=_auth(admin_token))
    deleted = await client.get("/api/experiments?status=deleted", headers=_auth(admin_token))
    assert [e["name"] for e in default.json()["experiments"]] == ["Kept"] and default.json()["total"] == 1
    assert [e["name"] for e in deleted.json()["experiments"]] == ["Gone"]


async def test_another_organizations_experiments_never_appear(client, session, admin_token, admin_user):
    other = Organization(name="Elsewhere", setup_complete=True)
    session.add(other)
    await session.flush()
    await _seed(session, other.id, ["Theirs"])
    await _seed(session, admin_user.organization_id, ["Mine"])
    await session.commit()
    r = await client.get("/api/experiments?name=e&page_size=100", headers=_auth(admin_token))
    names = [e["name"] for e in r.json()["experiments"]]
    assert "Mine" in names and "Theirs" not in names
