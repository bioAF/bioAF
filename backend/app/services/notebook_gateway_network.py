"""Keep the notebook gateway's port open wherever the application's HTTPS is (plan 03.5A).

New installations open it in the installer. Existing ones must not need a manual firewall step when
they update, so the backend reconciles it, soon after every start and every six hours, with the same
provider authority it uses for the host's other network rules (``host_ssh_access.network_for``). It
never opens the port wider than HTTPS, and a failure is recorded and logged, never raised: the rest
of bioAF keeps working, and only notebook Open is affected until it succeeds.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("bioaf.notebook_gateway_network")

STATUS_KEY = "notebook_gateway_port"


async def _record(session, **result) -> None:
    from app.platform.platform_config_service import PlatformConfigService

    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    await PlatformConfigService.set(session, STATUS_KEY, json.dumps(result))


async def ensure_gateway_port(session) -> None:
    from app.services.host_ssh_access import _provider, network_for
    from app.services.ssh_recovery import RecoveryError, current_host_identity

    try:
        identity = await current_host_identity(session)
    except RecoveryError:
        await _record(session, status="not_applicable")
        return
    try:
        driver = await network_for(session, identity)
        # Provider calls can take a while; they run off the serving event loop.
        result = await _provider(driver.ensure_gateway_port)
    except Exception as exc:
        logger.warning(
            "Could not open the notebook gateway port (%s); notebook sessions cannot be opened until it is",
            type(exc).__name__,
        )
        await _record(session, status="failed", detail=type(exc).__name__)
        return
    if result.get("opened"):
        logger.info("Notebook gateway port open to %s", ", ".join(result.get("sources") or []) or "the HTTPS sources")
    await _record(session, status="ok", opened=bool(result.get("opened")), sources=list(result.get("sources") or []))
