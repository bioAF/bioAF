"""Tests for the networking settings API (hostname/domain, reachability, TLS)."""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_get_networking_returns_defaults(client, admin_token, session):
    """GET /api/v1/settings/networking returns empty defaults for a fresh install."""
    response = await client.get(
        "/api/v1/settings/networking",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["hostname"] == ""
    assert data["domain"] == ""
    assert data["fqdn"] == ""
    assert data["reachability_status"] == ""
    assert data["reachability_checked_at"] is None
    assert data["cert_status"] == ""
    assert data["https_enforced"] is False


@pytest.mark.asyncio
async def test_get_networking_requires_auth(client):
    """GET /api/v1/settings/networking returns 401 without a token."""
    response = await client.get("/api/v1/settings/networking")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_put_networking_saves_hostname_and_domain(client, admin_token, session):
    """PUT saves hostname and domain and returns the composed FQDN."""
    response = await client.put(
        "/api/v1/settings/networking",
        json={"hostname": "app", "domain": "acme.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["hostname"] == "app"
    assert data["domain"] == "acme.com"
    assert data["fqdn"] == "app.acme.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_hostname",
    [
        "-leading",
        "trailing-",
        "UPPER",
        "has space",
        "under_score",
        "a" * 64,
        "",
    ],
)
async def test_put_networking_rejects_invalid_hostname(client, admin_token, bad_hostname):
    """PUT returns 422 for hostnames that are not valid DNS labels (RFC 1123)."""
    response = await client.put(
        "/api/v1/settings/networking",
        json={"hostname": bad_hostname, "domain": "acme.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_domain",
    [
        "-acme.com",
        "acme.com-",
        "acme..com",
        "acme",
        "acme.c",
        "acme.com/",
        " ".join(["a"] * 2),
    ],
)
async def test_put_networking_rejects_invalid_domain(client, admin_token, bad_domain):
    """PUT returns 422 for domains that are not valid DNS names."""
    response = await client.put(
        "/api/v1/settings/networking",
        json={"hostname": "app", "domain": bad_domain},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_networking_resets_verification_state(client, admin_token, session):
    """PUT clears reachability and cert status, since the new FQDN is unverified."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_reachability_status', 'reachable'), "
            "('networking_cert_status', 'active') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    response = await client.put(
        "/api/v1/settings/networking",
        json={"hostname": "newapp", "domain": "acme.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["reachability_status"] == ""
    assert data["cert_status"] == ""


@pytest.mark.asyncio
async def test_put_networking_writes_audit_log(client, admin_token, session):
    """PUT records an update_networking_config audit entry."""
    await client.put(
        "/api/v1/settings/networking",
        json={"hostname": "app", "domain": "acme.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE entity_type='platform_config' AND action='update_networking_config'"
            )
        )
    ).scalar()
    assert count >= 1


@pytest.mark.asyncio
async def test_put_networking_requires_admin(client, viewer_token):
    """PUT returns 403 for users without infrastructure:edit."""
    response = await client.put(
        "/api/v1/settings/networking",
        json={"hostname": "app", "domain": "acme.com"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# /self-check: unauthenticated loopback endpoint for the reachability test.
# Returns the active nonce so the verifier can confirm the FQDN routes here.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_check_returns_active_token(client, session):
    """GET /self-check returns the stored token when not expired."""
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_self_check_token', 'abc-123'), "
            "('networking_self_check_expires_at', :exp) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(exp=future)
    )
    await session.commit()

    response = await client.get("/api/v1/settings/networking/self-check")
    assert response.status_code == 200
    assert response.json() == {"token": "abc-123"}


@pytest.mark.asyncio
async def test_self_check_returns_null_when_expired(client, session):
    """GET /self-check returns null when the token has expired."""
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_self_check_token', 'expired'), "
            "('networking_self_check_expires_at', :exp) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(exp=past)
    )
    await session.commit()

    response = await client.get("/api/v1/settings/networking/self-check")
    assert response.status_code == 200
    assert response.json() == {"token": None}


@pytest.mark.asyncio
async def test_self_check_returns_null_when_unset(client, session):
    """GET /self-check returns null when no token exists."""
    response = await client.get("/api/v1/settings/networking/self-check")
    assert response.status_code == 200
    assert response.json() == {"token": None}


@pytest.mark.asyncio
async def test_self_check_unauthenticated_allowed(client):
    """The self-check endpoint must be reachable without auth: it is the
    target of an outbound HTTP loopback from this instance's own backend."""
    response = await client.get("/api/v1/settings/networking/self-check")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /reachability-test: write a nonce, GET it back via the configured FQDN.
# ---------------------------------------------------------------------------


async def _set_fqdn(session, hostname: str = "app", domain: str = "acme.com") -> None:
    """Seed a hostname + domain so the reachability test has somewhere to call."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_hostname', :h), ('networking_domain', :d) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(h=hostname, d=domain)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_reachability_test_requires_fqdn_set(client, admin_token, session):
    """POST /reachability-test returns 400 when hostname/domain unset."""
    response = await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reachability_test_passes_on_matching_token(
    client, admin_token, session, monkeypatch
):
    """Loopback returns our nonce -> status = reachable."""
    from uuid import UUID

    await _set_fqdn(session)
    fixed = UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr("app.api.networking.uuid4", lambda: fixed)

    async def fake_get(self, url, **kw):
        from httpx import Response
        return Response(200, json={"token": str(fixed)})

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    response = await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "reachable"
    assert data["fqdn"] == "app.acme.com"


@pytest.mark.asyncio
async def test_reachability_test_http_unreachable(client, admin_token, session, monkeypatch):
    """Outbound HTTP raises -> status = http_unreachable."""
    import httpx

    await _set_fqdn(session)

    async def fake_get(self, url, **kw):
        raise httpx.ConnectError("Name or service not known")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    response = await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "http_unreachable"
    assert "Name or service not known" in data["detail"]


@pytest.mark.asyncio
async def test_reachability_test_wrong_instance_on_token_mismatch(
    client, admin_token, session, monkeypatch
):
    """Loopback returns a different token -> status = wrong_instance."""
    await _set_fqdn(session)

    async def fake_get(self, url, **kw):
        from httpx import Response
        return Response(200, json={"token": "different-token"})

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    response = await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "wrong_instance"


@pytest.mark.asyncio
async def test_reachability_test_persists_status(client, admin_token, session, monkeypatch):
    """After the test runs, networking_reachability_status reflects the outcome."""
    from uuid import UUID

    await _set_fqdn(session)
    fixed = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    monkeypatch.setattr("app.api.networking.uuid4", lambda: fixed)

    async def fake_get(self, url, **kw):
        from httpx import Response
        return Response(200, json={"token": str(fixed)})

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    row = (
        await session.execute(
            text("SELECT value FROM platform_config WHERE key='networking_reachability_status'")
        )
    ).scalar()
    assert row == "reachable"
    checked = (
        await session.execute(
            text("SELECT value FROM platform_config WHERE key='networking_reachability_checked_at'")
        )
    ).scalar()
    assert checked  # non-empty ISO timestamp


@pytest.mark.asyncio
async def test_reachability_test_writes_audit_log(client, admin_token, session, monkeypatch):
    """POST /reachability-test writes a run_reachability_test audit row."""
    await _set_fqdn(session)

    async def fake_get(self, url, **kw):
        import httpx
        raise httpx.ConnectError("nope")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE entity_type='platform_config' AND action='run_reachability_test'"
            )
        )
    ).scalar()
    assert count >= 1


@pytest.mark.asyncio
async def test_reachability_test_requires_admin(client, viewer_token, session):
    """Non-admin gets 403 even with FQDN configured."""
    await _set_fqdn(session)
    response = await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
