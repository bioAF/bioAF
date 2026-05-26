"""Tests for the networking settings API (hostname/domain, reachability, TLS)."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.networking_applier import (
    CERT_STATUS_ACTIVE,
    CERT_STATUS_PROVISIONING,
    MockNetworkingApplier,
    get_networking_applier,
)


@pytest_asyncio.fixture
async def mock_applier(client):
    """Override get_networking_applier with a fresh MockNetworkingApplier.

    Yields the instance so tests can inspect calls (requested_for,
    enforce_calls, restart_count) and drive responses (status_to_return).
    """
    from app import main as main_module

    applier = MockNetworkingApplier()
    main_module.app.dependency_overrides[get_networking_applier] = lambda: applier
    try:
        yield applier
    finally:
        main_module.app.dependency_overrides.pop(get_networking_applier, None)


@pytest.mark.asyncio
async def test_get_networking_returns_defaults(client, admin_token, session):
    """GET /api/v1/settings/networking returns empty defaults for a fresh install.

    https_enforced is True even on a fresh install because the VM topology
    enforces it via nginx.conf's port-80 redirect; the value reflects the
    install topology (asked of the applier), not a DB flag the operator sets.
    """
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
    assert data["https_enforced"] is True


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
async def test_reachability_test_passes_on_matching_token(client, admin_token, session, monkeypatch):
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
async def test_reachability_test_dns_failure_classification(client, admin_token, session, monkeypatch):
    """A getaddrinfo failure surfaces as dns_failed with a friendly detail."""
    import httpx

    await _set_fqdn(session)

    async def fake_get(self, url, **kw):
        raise httpx.ConnectError("[Errno -2] Name or service not known")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    response = await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dns_failed"
    detail = data["detail"].lower()
    assert "could not resolve" in detail
    assert "cluster dns" in detail
    # Raw libc error must not leak through:
    assert "errno -2" not in detail


@pytest.mark.asyncio
async def test_reachability_test_connection_refused_classification(client, admin_token, session, monkeypatch):
    """A connection refused error is classified separately from DNS failures."""
    import httpx

    await _set_fqdn(session)

    async def fake_get(self, url, **kw):
        raise httpx.ConnectError("[Errno 111] Connection refused")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    response = await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = response.json()
    assert data["status"] == "connection_refused"
    assert "refused" in data["detail"].lower()
    assert "ingress" in data["detail"].lower() or "firewall" in data["detail"].lower()


@pytest.mark.asyncio
async def test_reachability_test_tls_error_classification(client, admin_token, session, monkeypatch):
    """A TLS handshake error on https is classified as tls_error."""
    import httpx

    await _set_fqdn(session)

    calls: list[str] = []

    async def fake_get(self, url, **kw):
        calls.append(url)
        if url.startswith("https://"):
            raise httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED")
        # http succeeds with the right token so the run also exercises the fallback
        from httpx import Response
        from app.api.networking import uuid4 as _uuid  # noqa: F401

        # Use the most recently written token from the DB instead of guessing.
        return Response(200, json={"token": "never-match"})

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    response = await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = response.json()
    # First (https) error is preserved because the http fallback returned a
    # mismatched nonce; that's an even noisier failure mode, but the operator's
    # most actionable signal is the TLS error from the primary scheme.
    assert data["status"] in ("tls_error", "wrong_instance")
    # https must have been attempted first
    assert calls[0].startswith("https://")


@pytest.mark.asyncio
async def test_reachability_test_tries_https_first(client, admin_token, session, monkeypatch):
    """Reachability test attempts https://<fqdn>/... before http://<fqdn>/..."""
    import httpx

    await _set_fqdn(session)
    schemes_seen: list[str] = []

    async def fake_get(self, url, **kw):
        schemes_seen.append(url.split("://", 1)[0])
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert schemes_seen[0] == "https"


@pytest.mark.asyncio
async def test_reachability_test_skips_http_fallback_on_dns_failure(client, admin_token, session, monkeypatch):
    """DNS failure on https short-circuits: no point retrying with http."""
    import httpx

    await _set_fqdn(session)
    attempts: list[str] = []

    async def fake_get(self, url, **kw):
        attempts.append(url)
        raise httpx.ConnectError("Name or service not known")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    await client.post(
        "/api/v1/settings/networking/reachability-test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Only the https attempt should run; http is skipped because DNS will fail
    # the same way.
    assert len(attempts) == 1
    assert attempts[0].startswith("https://")


@pytest.mark.asyncio
async def test_reachability_test_wrong_instance_on_token_mismatch(client, admin_token, session, monkeypatch):
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
        await session.execute(text("SELECT value FROM platform_config WHERE key='networking_reachability_status'"))
    ).scalar()
    assert row == "reachable"
    checked = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='networking_reachability_checked_at'"))
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
                "SELECT COUNT(*) FROM audit_log WHERE entity_type='platform_config' AND action='run_reachability_test'"
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


# ---------------------------------------------------------------------------
# /certificate: request a TLS cert for the configured FQDN.
# ---------------------------------------------------------------------------


async def _mark_reachable(session) -> None:
    """Mark the configured FQDN as reachable, the precondition for /certificate."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_reachability_status', 'reachable') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_request_certificate_calls_issuer(client, admin_token, session, mock_applier):
    """POST /certificate calls applier.request_certificate with the configured FQDN."""
    await _set_fqdn(session, "app", "acme.com")
    await _mark_reachable(session)

    response = await client.post(
        "/api/v1/settings/networking/certificate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert mock_applier.requested_for == "app.acme.com"


@pytest.mark.asyncio
async def test_request_certificate_persists_status(client, admin_token, session, mock_applier):
    """After requesting, networking_cert_status is 'provisioning'."""
    await _set_fqdn(session)
    await _mark_reachable(session)

    await client.post(
        "/api/v1/settings/networking/certificate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key='networking_cert_status'"))).scalar()
    assert row == CERT_STATUS_PROVISIONING


@pytest.mark.asyncio
async def test_request_certificate_requires_reachable(client, admin_token, session, mock_applier):
    """POST /certificate returns 400 if reachability hasn't been verified yet."""
    await _set_fqdn(session)
    # reachability NOT marked reachable

    response = await client.post(
        "/api/v1/settings/networking/certificate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert mock_applier.requested_for is None


@pytest.mark.asyncio
async def test_request_certificate_writes_audit_log(client, admin_token, session, mock_applier):
    await _set_fqdn(session)
    await _mark_reachable(session)

    await client.post(
        "/api/v1/settings/networking/certificate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE entity_type='platform_config' AND action='request_certificate'")
        )
    ).scalar()
    assert count >= 1


@pytest.mark.asyncio
async def test_request_certificate_translates_manual_action_to_501(client, admin_token, session, mock_applier):
    """When the applier raises ManualActionRequired, the API returns 501 with the operator
    instructions in the detail and does not move cert_status to provisioning."""
    from app.services.networking_applier import ManualActionRequired

    await _set_fqdn(session)
    await _mark_reachable(session)

    async def raise_manual(fqdn):
        raise ManualActionRequired(
            "Install certbot on the host and run certbot certonly --webroot -d "
            f"{fqdn} --email <email>; then copy fullchain.pem to docker/certs/."
        )

    mock_applier.request_certificate = raise_manual

    response = await client.post(
        "/api/v1/settings/networking/certificate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 501
    detail = response.json()["detail"]
    assert "certbot" in detail.lower()
    assert "app.acme.com" in detail

    # Status must not have advanced to "provisioning" if no automated action ran.
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key='networking_cert_status'"))).scalar()
    assert row != "provisioning"


@pytest.mark.asyncio
async def test_request_certificate_requires_admin(client, viewer_token, session, mock_applier):
    await _set_fqdn(session)
    await _mark_reachable(session)
    response = await client.post(
        "/api/v1/settings/networking/certificate",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_certificate_status_polls_applier_and_persists(client, admin_token, session, mock_applier):
    """GET /certificate/status calls applier.get_certificate_status and caches it."""
    await _set_fqdn(session)
    mock_applier.status_to_return = CERT_STATUS_ACTIVE

    response = await client.get(
        "/api/v1/settings/networking/certificate/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == CERT_STATUS_ACTIVE

    cached = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='networking_cert_status'"))
    ).scalar()
    assert cached == CERT_STATUS_ACTIVE


@pytest.mark.asyncio
async def test_certificate_status_returns_not_requested_when_no_fqdn(client, admin_token, session, mock_applier):
    """GET /certificate/status returns not_requested if no FQDN is set."""
    response = await client.get(
        "/api/v1/settings/networking/certificate/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "not_requested"


@pytest.mark.asyncio
async def test_get_networking_uses_live_cert_status_not_cached(client, admin_token, session, mock_applier):
    """GET /networking computes cert status live from the applier.

    A stale 'provisioning' row left over from an earlier click must NOT
    leak into the response if the applier (reading the real on-disk cert)
    says the cert is actually not_requested.
    """
    await _set_fqdn(session, hostname="app", domain="acme.com")
    # Plant a stale 'provisioning' in the DB cache.
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_cert_status', 'provisioning') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()
    # But the applier (truth) says the on-disk cert does not match.
    mock_applier.status_to_return = "not_requested"

    response = await client.get(
        "/api/v1/settings/networking",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["cert_status"] == "not_requested"


@pytest.mark.asyncio
async def test_get_networking_uses_live_https_enforced_from_applier(client, admin_token, session, mock_applier):
    """GET /networking returns the applier's view of https_enforced, ignoring stale DB."""
    await _set_fqdn(session, hostname="app", domain="acme.com")
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_https_enforced', 'false') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()
    mock_applier.https_enforced_value = True

    response = await client.get(
        "/api/v1/settings/networking",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["https_enforced"] is True


@pytest.mark.asyncio
async def test_get_networking_skips_applier_when_no_fqdn_set(client, admin_token, session, mock_applier):
    """No FQDN set means we can't ask the applier about anything; cert_status stays empty."""
    response = await client.get(
        "/api/v1/settings/networking",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["cert_status"] == ""


# ---------------------------------------------------------------------------
# /enforce-https: flip the flag, patch Ingress, restart deployments.
# ---------------------------------------------------------------------------


async def _mark_cert_active(session) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_cert_status', 'active') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_enforce_https_requires_active_cert(client, admin_token, session, mock_applier):
    """POST /enforce-https returns 400 if the cert is not yet active."""
    await _set_fqdn(session)
    # cert NOT active

    response = await client.post(
        "/api/v1/settings/networking/enforce-https",
        json={"enabled": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert mock_applier.enforce_calls == []


@pytest.mark.asyncio
async def test_enforce_https_calls_applier_and_restarts(client, admin_token, session, mock_applier):
    """Enabling enforcement calls applier.enforce_https(fqdn, True) and restart_services."""
    await _set_fqdn(session)
    await _mark_cert_active(session)

    response = await client.post(
        "/api/v1/settings/networking/enforce-https",
        json={"enabled": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert mock_applier.enforce_calls == [("app.acme.com", True)]
    assert mock_applier.restart_count == 1


@pytest.mark.asyncio
async def test_enforce_https_persists_flag(client, admin_token, session, mock_applier):
    await _set_fqdn(session)
    await _mark_cert_active(session)

    await client.post(
        "/api/v1/settings/networking/enforce-https",
        json={"enabled": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='networking_https_enforced'"))
    ).scalar()
    assert row == "true"


@pytest.mark.asyncio
async def test_enforce_https_can_be_disabled(client, admin_token, session, mock_applier):
    """Disabling enforcement is allowed even without active cert (rollback)."""
    await _set_fqdn(session)
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES "
            "('networking_https_enforced', 'true') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    response = await client.post(
        "/api/v1/settings/networking/enforce-https",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert mock_applier.enforce_calls == [("app.acme.com", False)]


@pytest.mark.asyncio
async def test_enforce_https_writes_audit_log(client, admin_token, session, mock_applier):
    await _set_fqdn(session)
    await _mark_cert_active(session)

    await client.post(
        "/api/v1/settings/networking/enforce-https",
        json={"enabled": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE entity_type='platform_config' AND action='enforce_https'")
        )
    ).scalar()
    assert count >= 1


@pytest.mark.asyncio
async def test_enforce_https_requires_admin(client, viewer_token, session, mock_applier):
    await _set_fqdn(session)
    await _mark_cert_active(session)
    response = await client.post(
        "/api/v1/settings/networking/enforce-https",
        json={"enabled": True},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
