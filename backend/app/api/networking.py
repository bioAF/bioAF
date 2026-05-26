"""Networking settings API: hostname, domain, reachability test, TLS, HTTPS enforcement."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
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
    rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=_KEYS)
        )
    ).fetchall()
    config = dict(_DEFAULTS)
    config.update({r[0]: r[1] for r in rows})
    return config


async def _upsert(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_at = now()"
        ).bindparams(k=key, v=value)
    )


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
) -> NetworkingConfigResponse:
    """Return current networking configuration (hostname, domain, reachability, cert, HTTPS)."""
    config = await _read_config(session)
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

    url = f"http://{fqdn}/api/v1/settings/networking/self-check"
    status_str = "reachable"
    detail = ""
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as http:
            resp = await http.get(url)
        if resp.status_code != 200:
            status_str = "http_unreachable"
            detail = f"HTTP {resp.status_code}"
        else:
            payload = resp.json()
            returned = payload.get("token")
            if returned == nonce:
                status_str = "reachable"
            else:
                status_str = "wrong_instance"
                detail = "self-check token did not match"
    except httpx.HTTPError as exc:
        status_str = "http_unreachable"
        detail = str(exc) or exc.__class__.__name__

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

    await applier.request_certificate(fqdn)
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
    rows = (
        await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('networking_self_check_token', 'networking_self_check_expires_at')"
            )
        )
    ).fetchall()
    values = {r[0]: r[1] for r in rows}
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
