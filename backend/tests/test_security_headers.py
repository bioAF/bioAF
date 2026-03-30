"""Tests for security headers middleware."""

import pytest

from app.config import settings


@pytest.mark.asyncio
async def test_security_headers_present(client):
    """Every response must include X-Content-Type-Options, X-Frame-Options,
    and Referrer-Policy headers."""
    resp = await client.get("/api/health/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_hsts_header_when_ssl_enabled(client, monkeypatch):
    """HSTS header must be set when ssl_enabled is True."""
    monkeypatch.setattr(settings, "ssl_enabled", True)
    resp = await client.get("/api/health/")
    assert "max-age=31536000" in resp.headers["Strict-Transport-Security"]

    monkeypatch.setattr(settings, "ssl_enabled", False)
    resp = await client.get("/api/health/")
    assert "Strict-Transport-Security" not in resp.headers
