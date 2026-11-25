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


def _location_block(conf: str, header: str) -> str:
    start = conf.index(header)
    depth, i = 0, conf.index("{", start)
    for j in range(i, len(conf)):
        depth += {"{": 1, "}": -1}.get(conf[j], 0)
        if depth == 0:
            return conf[i : j + 1]
    raise AssertionError(header)


def test_the_work_node_launch_location_keeps_every_api_proxy_setting():
    """The exact launch location (the update gate) must proxy like /api/: same headers and the same
    long backend timeouts, so a slow launch is not cut off at nginx's 60 second default."""
    import re
    from pathlib import Path

    conf = (Path(__file__).resolve().parents[2] / "docker" / "nginx.conf").read_text()
    launch = _location_block(conf, "location = /api/v1/work-nodes/sessions")
    api = _location_block(conf, "location /api/ {")

    def directives(block):
        return {
            line.strip()
            for line in block.splitlines()
            if re.match(r"\s*(proxy_\w+|send_timeout|client_max_body_size)\s", line)
        }

    assert directives(api) <= directives(launch), directives(api) - directives(launch)
