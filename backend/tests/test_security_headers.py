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


def _directive(csp: str, name: str) -> str | None:
    """Return the named CSP directive, or None if it is absent."""
    for part in csp.split(";"):
        part = part.strip()
        if part == name or part.startswith(name + " "):
            return part
    return None


@pytest.mark.asyncio
async def test_csp_confines_every_content_type_to_our_own_origin(client):
    """The restrictive half of the policy, pinned directive by directive.

    The two tests above only assert that `default-src` and `frame-ancestors`
    are *present*. That leaves the policy free to be widened to something that
    still contains both words and stops defending anything, which is the shape
    a CSP usually rots into: one directive gets a host added to unblock a
    feature and nobody notices the rest went with it.
    """
    resp = await client.get("/api/health/")
    csp = resp.headers["Content-Security-Policy"]

    expected = {
        "default-src": "default-src 'self'",
        # No third-party script host. 'unsafe-inline'/'unsafe-eval' are asserted
        # separately below, with the reason they are there.
        "style-src": "style-src 'self' 'unsafe-inline'",
        # data: is needed for inline plot images; no remote image hosts.
        "img-src": "img-src 'self' data:",
        "font-src": "font-src 'self'",
        # The one that stops a compromised page exfiltrating to someone else.
        "connect-src": "connect-src 'self'",
        # Clickjacking, and a <base> tag rewriting every relative URL.
        "frame-ancestors": "frame-ancestors 'none'",
        "base-uri": "base-uri 'self'",
        "form-action": "form-action 'self'",
    }
    for name, value in expected.items():
        assert _directive(csp, name) == value, f"{name} is no longer {value!r}: {csp!r}"


@pytest.mark.asyncio
async def test_csp_script_src_admits_no_remote_origin(client):
    """script-src may loosen inline/eval, but never to an off-origin host.

    'unsafe-inline' and 'unsafe-eval' weaken *how* our own scripts may run.
    Adding a host or a wildcard changes *whose* scripts may run, which is the
    difference between a policy that is inconvenient and one that is decorative.
    """
    resp = await client.get("/api/health/")
    script_src = _directive(resp.headers["Content-Security-Policy"], "script-src")
    assert script_src is not None

    sources = script_src.split()[1:]
    allowed = {"'self'", "'unsafe-inline'", "'unsafe-eval'"}
    unexpected = [s for s in sources if s not in allowed]
    assert unexpected == [], (
        f"script-src gained {unexpected}; a remote origin here means an attacker "
        f"who can host a file there executes in our origin: {script_src!r}"
    )


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
