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


@pytest.mark.asyncio
async def test_content_security_policy_present(client):
    """Every response must include a Content-Security-Policy header."""
    resp = await client.get("/api/health/")
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src" in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.asyncio
async def test_csp_allows_unsafe_eval_for_nextflow_report(client):
    """The Nextflow HTML report embeds Plotly, which calls `new Function(...)`
    via its `cwise-compiler` to JIT vector math. The report renders inside a
    srcdoc iframe whose policy container is inherited from this page (per
    the HTML spec), so without 'unsafe-eval' in script-src the report's
    plots and task table fail to render and the user sees a blank report.
    Sandbox doesn't help here -- about:srcdoc inherits regardless."""
    resp = await client.get("/api/health/")
    csp = resp.headers["Content-Security-Policy"]
    # Find the script-src directive and verify unsafe-eval is in it.
    script_src = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("script-src")),
        None,
    )
    assert script_src is not None, f"no script-src directive in CSP: {csp!r}"
    assert "'unsafe-eval'" in script_src, (
        f"script-src must include 'unsafe-eval' so Plotly in the Nextflow "
        f"report iframe can JIT vector math, got: {script_src!r}"
    )
