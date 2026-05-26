"""Networking settings API: hostname, domain, reachability test, TLS, HTTPS enforcement."""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.networking import NetworkingConfigResponse

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
