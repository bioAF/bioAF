"""Networking settings API: hostname, domain, reachability test, TLS, HTTPS enforcement."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.platform.platform_config_service import PlatformConfigService
from app.schemas.networking import (
    CertificateStatusResponse,
    EnforceHttpsRequest,
    EnforceHttpsResponse,
    NetworkingConfigResponse,
    NetworkingConfigUpdate,
    ReachabilityTestResult,
)
from app.services import audit_service
from app.services.networking_applier import (
    CERT_STATUS_NOT_REQUESTED,
    CERT_STATUS_PROVISIONING,
    ManualActionRequired,
    NetworkingApplier,
    get_networking_applier,
)

router = APIRouter(prefix="/api/v1/settings/networking", tags=["networking"])

_KEYS = [
    "networking_hostname",
    "networking_domain",
    "networking_reachability_status",
    "networking_reachability_checked_at",
    "networking_cert_status",
    "networking_https_enforced",
]

_DEFAULTS: dict[str, str] = {
    "networking_hostname": "",
    "networking_domain": "",
    "networking_reachability_status": "",
    "networking_reachability_checked_at": "",
    "networking_cert_status": "",
    "networking_https_enforced": "false",
}


async def _read_config(session: AsyncSession) -> dict[str, str]:
    config = dict(_DEFAULTS)
    config.update(await PlatformConfigService.get_many(session, _KEYS))
    return config


async def _upsert(session: AsyncSession, key: str, value: str) -> None:
    await PlatformConfigService.set(session, key, value)


def _compute_fqdn(hostname: str, domain: str) -> str:
    if hostname and domain:
        return f"{hostname}.{domain}"
    if hostname:
        return hostname
    return domain


def _to_response(config: dict[str, str]) -> NetworkingConfigResponse:
    hostname = config.get("networking_hostname", "")
    domain = config.get("networking_domain", "")
    checked_raw = config.get("networking_reachability_checked_at", "")
    checked_at: datetime | None = None
    if checked_raw:
        try:
            checked_at = datetime.fromisoformat(checked_raw)
        except ValueError:
            checked_at = None
    return NetworkingConfigResponse(
        hostname=hostname,
        domain=domain,
        fqdn=_compute_fqdn(hostname, domain),
        reachability_status=config.get("networking_reachability_status", ""),
        reachability_checked_at=checked_at,
        cert_status=config.get("networking_cert_status", ""),
        https_enforced=config.get("networking_https_enforced", "false") == "true",
    )


@router.get("", response_model=NetworkingConfigResponse)
async def get_networking_config(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
    applier: NetworkingApplier = Depends(get_networking_applier),
) -> NetworkingConfigResponse:
    """Return current networking configuration with live cert status."""
    config = await _read_config(session)
    hostname = config.get("networking_hostname", "")
    domain = config.get("networking_domain", "")
    fqdn = _compute_fqdn(hostname, domain)
    if fqdn:
        # Cert status is external state (the on-disk nginx cert), not config.
        # Read it live so a stale "provisioning" cache from an earlier click
        # cannot lie to the operator about what the system actually serves.
        live = await applier.get_certificate_status(fqdn)
        config["networking_cert_status"] = live
        await _upsert(session, "networking_cert_status", live)
    # HTTPS enforcement is also a property of the install topology
    # (nginx.conf's port-80 redirect on VM installs), not a DB-stored
    # toggle. Always ask the applier.
    enforced = await applier.get_https_enforced()
    config["networking_https_enforced"] = "true" if enforced else "false"
    await _upsert(session, "networking_https_enforced", config["networking_https_enforced"])
    await session.commit()
    return _to_response(config)


@router.put("", response_model=NetworkingConfigResponse)
async def update_networking_config(
    body: NetworkingConfigUpdate,
    current_user: dict = require_permission("infrastructure", "edit"),
    session: AsyncSession = Depends(get_session),
) -> NetworkingConfigResponse:
    """Save hostname and domain, reset verification state, write an audit entry."""
    user_id = int(current_user["sub"])

    await _upsert(session, "networking_hostname", body.hostname)
    await _upsert(session, "networking_domain", body.domain)
    # Changing the FQDN invalidates any prior verification.
    await _upsert(session, "networking_reachability_status", "")
    await _upsert(session, "networking_reachability_checked_at", "")
    await _upsert(session, "networking_cert_status", "")

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="update_networking_config",
        details={"hostname": body.hostname, "domain": body.domain},
    )

    await session.commit()

    return _to_response(await _read_config(session))


_DNS_FAILURE_MARKERS = (
    "Name or service not known",
    "Temporary failure in name resolution",
    "nodename nor servname provided",
    "Name does not resolve",
    "No address associated with hostname",
    "getaddrinfo",
)


def _classify_connect_error(exc: Exception, scheme: str, fqdn: str) -> tuple[str, str]:
    """Translate a low-level connect/HTTP error into (status, human-readable detail)."""
    raw = str(exc) or exc.__class__.__name__
    lowered = raw.lower()
    port = 443 if scheme == "https" else 80
    if any(marker.lower() in lowered for marker in _DNS_FAILURE_MARKERS):
        return (
            "dns_failed",
            f"The bioAF backend pod could not resolve {fqdn} via cluster DNS. "
            f"If you just added the DNS record, wait 1 to 5 minutes for negative "
            f"DNS cache entries to expire and retry. If the failure persists, check "
            f"that CoreDNS is healthy (kubectl -n kube-system get pods -l k8s-app=kube-dns) "
            f"and that external DNS resolution works from inside the pod.",
        )
    if "ssl" in lowered or "tls" in lowered or "certificate" in lowered:
        return (
            "tls_error",
            f"TLS handshake to {scheme}://{fqdn} failed: {raw}. This usually means "
            f"the certificate is not yet provisioned for this hostname, or the "
            f"server is presenting a certificate for a different name.",
        )
    if "refused" in lowered:
        return (
            "connection_refused",
            f"The connection to {scheme}://{fqdn}:{port} was refused. Confirm the "
            f"Ingress is routing traffic for {fqdn} and that the firewall allows "
            f"port {port}.",
        )
    if "timed out" in lowered or "timeout" in lowered:
        return (
            "timeout",
            f"The request to {scheme}://{fqdn} timed out. Check that the Ingress is "
            f"responsive and the firewall allows port {port}.",
        )
    return (
        "unreachable",
        f"Could not contact {scheme}://{fqdn}: {raw}",
    )


async def _attempt_loopback(fqdn: str, nonce: str) -> tuple[str, str]:
    """Try https first (verify=False), then http; return the most informative result.

    HTTPS is attempted first because production installs typically enforce HTTPS
    at the Ingress and refuse or redirect port 80. Certificate verification is
    disabled because the cert may not yet cover this hostname; the loopback
    nonce is the proof of reach, not the TLS chain.
    """
    first_error: tuple[str, str] | None = None
    for scheme in ("https", "http"):
        url = f"{scheme}://{fqdn}/api/v1/settings/networking/self-check"
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True, verify=False) as http:
                resp = await http.get(url)
        except httpx.HTTPError as exc:
            status_str, detail = _classify_connect_error(exc, scheme, fqdn)
            if status_str == "dns_failed":
                return status_str, detail
            if first_error is None:
                first_error = (status_str, detail)
            continue

        if resp.status_code != 200:
            err = (
                "bad_response",
                f"{scheme.upper()} {scheme}://{fqdn} returned HTTP {resp.status_code}; "
                f"expected 200. The FQDN may route to a different application, or the "
                f"server is misconfigured.",
            )
            if first_error is None:
                first_error = err
            continue

        try:
            payload = resp.json()
        except ValueError:
            err = (
                "bad_response",
                f"The server at {scheme}://{fqdn} returned a non-JSON response. The "
                f"FQDN may route to a different application.",
            )
            if first_error is None:
                first_error = err
            continue

        returned = payload.get("token")
        if returned == nonce:
            return (
                "reachable",
                f"Verified via {scheme.upper()}: the FQDN {fqdn} routes to this bioAF instance.",
            )
        if returned is None:
            err = (
                "wrong_instance",
                f"The server at {scheme}://{fqdn} did not return a reachability "
                f"nonce. The FQDN likely routes to a different application, or to "
                f"a different bioAF instance.",
            )
        else:
            err = (
                "wrong_instance",
                f"The nonce returned from {scheme}://{fqdn} did not match the one "
                f"this instance just wrote. The FQDN routes to a different bioAF "
                f"instance.",
            )
        if first_error is None:
            first_error = err
        # Don't try http after a wrong_instance response on https: same server
        # would answer the same way. Return the https result.
        return first_error

    return first_error or (
        "unreachable",
        "No scheme attempt succeeded; this is unexpected. Please retry.",
    )


@router.post("/reachability-test", response_model=ReachabilityTestResult)
async def reachability_test(
    current_user: dict = require_permission("infrastructure", "edit"),
    session: AsyncSession = Depends(get_session),
) -> ReachabilityTestResult:
    """Write a nonce, fetch http://<fqdn>/.../self-check, prove the FQDN routes here."""
    user_id = int(current_user["sub"])

    config = await _read_config(session)
    hostname = config.get("networking_hostname", "")
    domain = config.get("networking_domain", "")
    fqdn = _compute_fqdn(hostname, domain)
    if not hostname or not domain:
        raise HTTPException(400, "hostname and domain must be configured before testing reachability")

    nonce = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    await _upsert(session, "networking_self_check_token", nonce)
    await _upsert(session, "networking_self_check_expires_at", expires_at.isoformat())
    await session.commit()

    status_str, detail = await _attempt_loopback(fqdn, nonce)

    checked_at = datetime.now(timezone.utc)
    await _upsert(session, "networking_reachability_status", status_str)
    await _upsert(session, "networking_reachability_checked_at", checked_at.isoformat())

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="run_reachability_test",
        details={"fqdn": fqdn, "status": status_str},
    )

    await session.commit()

    return ReachabilityTestResult(
        fqdn=fqdn,
        status=status_str,
        detail=detail,
        checked_at=checked_at,
    )


@router.post("/certificate", response_model=CertificateStatusResponse)
async def request_certificate(
    current_user: dict = require_permission("infrastructure", "edit"),
    session: AsyncSession = Depends(get_session),
    applier: NetworkingApplier = Depends(get_networking_applier),
) -> CertificateStatusResponse:
    """Request a TLS certificate for the configured FQDN via the configured applier."""
    user_id = int(current_user["sub"])
    config = await _read_config(session)
    hostname = config.get("networking_hostname", "")
    domain = config.get("networking_domain", "")
    fqdn = _compute_fqdn(hostname, domain)
    if not hostname or not domain:
        raise HTTPException(400, "hostname and domain must be configured before requesting a certificate")
    if config.get("networking_reachability_status") != "reachable":
        raise HTTPException(400, "reachability must be verified before requesting a certificate")

    try:
        await applier.request_certificate(fqdn)
    except ManualActionRequired as exc:
        # The current applier (VmNginxApplier) cannot issue certs itself; it
        # returns instructions for the operator to run certbot on the host.
        # Record the attempt in the audit log so we can see who tried and when,
        # then surface the instruction to the UI.
        await audit_service.log_action(
            session,
            user_id=user_id,
            entity_type="platform_config",
            entity_id=0,
            action="request_certificate_manual",
            details={"fqdn": fqdn},
        )
        await session.commit()
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    await _upsert(session, "networking_cert_status", CERT_STATUS_PROVISIONING)

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="request_certificate",
        details={"fqdn": fqdn},
    )
    await session.commit()
    return CertificateStatusResponse(fqdn=fqdn, status=CERT_STATUS_PROVISIONING)


@router.get("/certificate/status", response_model=CertificateStatusResponse)
async def certificate_status(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
    applier: NetworkingApplier = Depends(get_networking_applier),
) -> CertificateStatusResponse:
    """Poll the applier for the current cert status; cache the result."""
    config = await _read_config(session)
    hostname = config.get("networking_hostname", "")
    domain = config.get("networking_domain", "")
    fqdn = _compute_fqdn(hostname, domain)
    if not hostname or not domain:
        return CertificateStatusResponse(fqdn="", status=CERT_STATUS_NOT_REQUESTED)

    status_str = await applier.get_certificate_status(fqdn)
    await _upsert(session, "networking_cert_status", status_str)
    await session.commit()
    return CertificateStatusResponse(fqdn=fqdn, status=status_str)


@router.post("/enforce-https", response_model=EnforceHttpsResponse)
async def enforce_https(
    body: EnforceHttpsRequest,
    current_user: dict = require_permission("infrastructure", "edit"),
    session: AsyncSession = Depends(get_session),
    applier: NetworkingApplier = Depends(get_networking_applier),
) -> EnforceHttpsResponse:
    """Flip the HTTPS-enforcement flag, patch the Ingress, restart services."""
    user_id = int(current_user["sub"])
    config = await _read_config(session)
    hostname = config.get("networking_hostname", "")
    domain = config.get("networking_domain", "")
    fqdn = _compute_fqdn(hostname, domain)
    if not hostname or not domain:
        raise HTTPException(400, "hostname and domain must be configured before enforcing HTTPS")
    if body.enabled and config.get("networking_cert_status") != "active":
        raise HTTPException(400, "TLS certificate must be active before enforcing HTTPS")

    await applier.enforce_https(fqdn, body.enabled)
    await _upsert(session, "networking_https_enforced", "true" if body.enabled else "false")
    await applier.restart_services()

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="enforce_https",
        details={"fqdn": fqdn, "enabled": body.enabled},
    )
    await session.commit()
    return EnforceHttpsResponse(fqdn=fqdn, https_enforced=body.enabled)


@router.get("/self-check")
async def self_check(session: AsyncSession = Depends(get_session)) -> dict[str, str | None]:
    """Unauthenticated. Return the active reachability-test nonce, or null.

    The reachability test makes an outbound HTTP request to this endpoint at
    the configured public FQDN. If the response token matches the one this
    instance just wrote, we have proof that the FQDN routes here.
    """
    values = await PlatformConfigService.get_many(
        session,
        ["networking_self_check_token", "networking_self_check_expires_at"],
    )
    token = values.get("networking_self_check_token")
    expires_raw = values.get("networking_self_check_expires_at")
    if not token or not expires_raw:
        return {"token": None}
    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except ValueError:
        return {"token": None}
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return {"token": None}
    return {"token": token}
