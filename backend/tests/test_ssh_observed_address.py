"""Plan 03B: "Use this computer's address" returns only an address the managed edge vouches for.

nginx overwrites X-Real-IP with its observed peer and adds the installation's edge token; the
backend accepts the address only with that token. X-Forwarded-For and body fields are never used.
"""

import pytest

from app.config import settings

URL = "/api/v1/settings/ssh-access/observed-address"


@pytest.fixture
def edge_token(monkeypatch):
    monkeypatch.setattr(settings, "edge_token", "edge-secret-0123456789abcdef")
    return "edge-secret-0123456789abcdef"


def _h(admin_token, **extra):
    return {"Authorization": f"Bearer {admin_token}", **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("seen, expected", [("203.0.113.7", "203.0.113.7/32"), ("2001:db8::5", "2001:db8::5/128")])
async def test_the_edge_vouched_address_is_returned_as_a_single_host_range(
    client, admin_token, edge_token, seen, expected
):
    response = await client.get(URL, headers=_h(admin_token, **{"X-Real-IP": seen, "X-Bioaf-Edge-Token": edge_token}))
    assert response.status_code == 200
    assert response.json()["address"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"X-Real-IP": "203.0.113.7"},
        {"X-Real-IP": "203.0.113.7", "X-Bioaf-Edge-Token": "wrong"},
        {"X-Forwarded-For": "203.0.113.7", "X-Bioaf-Edge-Token": "edge-secret-0123456789abcdef"},
        {"X-Real-IP": "not-an-ip", "X-Bioaf-Edge-Token": "edge-secret-0123456789abcdef"},
    ],
)
async def test_unvouched_or_malformed_addresses_are_never_suggested(client, admin_token, edge_token, headers):
    response = await client.get(URL, headers=_h(admin_token, **headers))
    assert response.json()["address"] is None
    assert "Enter it yourself" in response.json()["message"]


@pytest.mark.asyncio
async def test_without_a_configured_edge_token_nothing_is_suggested(client, admin_token, monkeypatch):
    monkeypatch.setattr(settings, "edge_token", "")
    response = await client.get(URL, headers=_h(admin_token, **{"X-Real-IP": "203.0.113.7", "X-Bioaf-Edge-Token": ""}))
    assert response.json()["address"] is None


@pytest.mark.asyncio
async def test_the_helper_needs_an_ssh_settings_permission(client, comp_bio_token, edge_token):
    response = await client.get(URL, headers={"Authorization": f"Bearer {comp_bio_token}"})
    assert response.status_code == 403
