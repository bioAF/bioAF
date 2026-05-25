import pytest
from httpx import AsyncClient


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_layout_unconfigured(client: AsyncClient, admin_token: str):
    """A user who has never configured gets configured=false and no widgets, so
    the frontend knows to seed role defaults."""
    resp = await client.get("/api/dashboard/layout", headers=_auth(admin_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["widgets"] == []


@pytest.mark.asyncio
async def test_put_then_get_roundtrip(client: AsyncClient, admin_token: str):
    payload = {
        "widgets": [
            {"key": "experiments_status", "settings": {}},
            {"key": "failed_runs", "settings": {"window": "24h"}},
        ]
    }
    put = await client.put("/api/dashboard/layout", json=payload, headers=_auth(admin_token))
    assert put.status_code == 200
    assert put.json()["configured"] is True

    get = await client.get("/api/dashboard/layout", headers=_auth(admin_token))
    data = get.json()
    assert data["configured"] is True
    assert [w["key"] for w in data["widgets"]] == ["experiments_status", "failed_runs"]
    assert data["widgets"][1]["settings"] == {"window": "24h"}


@pytest.mark.asyncio
async def test_widget_settings_default_empty(client: AsyncClient, admin_token: str):
    """A widget item without settings round-trips with an empty settings dict."""
    payload = {"widgets": [{"key": "queue_depth"}]}
    await client.put("/api/dashboard/layout", json=payload, headers=_auth(admin_token))
    get = await client.get("/api/dashboard/layout", headers=_auth(admin_token))
    assert get.json()["widgets"][0]["settings"] == {}


@pytest.mark.asyncio
async def test_put_is_upsert(client: AsyncClient, admin_token: str):
    """A second PUT overwrites the first; only one layout per user."""
    await client.put(
        "/api/dashboard/layout",
        json={"widgets": [{"key": "infra_health"}]},
        headers=_auth(admin_token),
    )
    await client.put(
        "/api/dashboard/layout",
        json={"widgets": [{"key": "cost_budget"}, {"key": "activity_feed"}]},
        headers=_auth(admin_token),
    )
    get = await client.get("/api/dashboard/layout", headers=_auth(admin_token))
    assert [w["key"] for w in get.json()["widgets"]] == ["cost_budget", "activity_feed"]


@pytest.mark.asyncio
async def test_empty_array_is_configured(client: AsyncClient, admin_token: str):
    """Saving an empty list is a real 'no widgets' state, distinct from never
    configured."""
    await client.put("/api/dashboard/layout", json={"widgets": []}, headers=_auth(admin_token))
    get = await client.get("/api/dashboard/layout", headers=_auth(admin_token))
    data = get.json()
    assert data["configured"] is True
    assert data["widgets"] == []


@pytest.mark.asyncio
async def test_layout_is_per_user(client: AsyncClient, admin_token: str, viewer_token: str):
    """One user's saved layout never leaks into another user's GET."""
    await client.put(
        "/api/dashboard/layout",
        json={"widgets": [{"key": "infra_health"}]},
        headers=_auth(admin_token),
    )
    get = await client.get("/api/dashboard/layout", headers=_auth(viewer_token))
    data = get.json()
    assert data["configured"] is False
    assert data["widgets"] == []


@pytest.mark.asyncio
async def test_get_layout_requires_auth(client: AsyncClient):
    resp = await client.get("/api/dashboard/layout")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_put_layout_requires_auth(client: AsyncClient):
    resp = await client.put("/api/dashboard/layout", json={"widgets": []})
    assert resp.status_code == 401
