"""The private route the backend uses to save Work Node outputs (plan 03B).

GCP: Work Nodes launch on the application VM's own network. A backend-managed firewall rule
allows tcp:22 only from the application VM's primary internal IPv4 address (a single /32,
discovered from authenticated provider metadata, never typed) to the installation's backend-sync
tag at priority 800. No source tag or service account identifies the backend. A replacement
application VM moves the rule to its new address on the next launch.

AWS: the compute module peers the dedicated compute VPC with the application VPC and provides a
backend-sync security group referencing the application's group. Until that deployment exists,
new nodes keep the existing public route; that is the supported earlier generation, not a fallback.

A failed private route stops the launch with a plain message; it never switches to public sync.
"""

from __future__ import annotations

import logging

from app.exceptions import ValidationError

logger = logging.getLogger("bioaf.backend_sync_route")

SYNC_TAG = "bioaf-backend-sync"
SYNC_RULE = "bioaf-work-node-sync-from-app"
SYNC_PRIORITY = 800
PRIVATE_ROUTE = "private-v1"
PUBLIC_ROUTE = "public"
ROUTE_FAILED = (
    "bioAF could not prepare the private connection it uses to save this work node's files, so the work "
    "node was not launched. Ask an administrator to check bioAF's cloud permissions."
)


def _simulated() -> bool:
    from app.config import settings
    from app.settings_contract import is_simulated

    return is_simulated(settings.compute_mode)


async def _gcp_firewalls(session):
    from google.cloud import compute_v1

    from app.adapters.credentials.credential_injector import load_gcp_credentials
    from app.platform.platform_config_service import PlatformConfigService

    cfg = await PlatformConfigService.get_many(
        session,
        [
            "gcp_project_id",
            "gcp_credential_source",
            "gcp_service_account_key",
            "gcp_service_account_email",
            "gcp_bootstrap_sa_email",
        ],
    )
    return compute_v1.FirewallsClient(credentials=load_gcp_credentials(cfg))


def _gcp_rule(identity):
    import ipaddress

    from google.cloud import compute_v1

    address = ipaddress.ip_address(identity.internal_ip)
    if address.version != 4:
        raise ValueError("the application VM has no internal IPv4 address")
    return compute_v1.Firewall(
        name=SYNC_RULE,
        network=f"projects/{identity.account}/global/networks/{identity.network or 'default'}",
        direction="INGRESS",
        priority=SYNC_PRIORITY,
        source_ranges=[f"{address}/32"],
        target_tags=[SYNC_TAG],
        allowed=[compute_v1.Allowed(I_p_protocol="tcp", ports=["22"])],
        description="Managed by bioAF (plan 03B): backend output sync to Work Nodes over the private network.",
    )


async def ensure_backend_sync_route(session) -> dict:
    if _simulated():
        return {"route": PUBLIC_ROUTE}
    from app.services.ssh_recovery import RecoveryError, current_host_identity

    try:
        identity = await current_host_identity(session)
    except RecoveryError:
        raise ValidationError(ROUTE_FAILED) from None
    if identity.provider == "aws":
        from app.platform.platform_config_service import PlatformConfigService

        group = (await PlatformConfigService.get(session, "aws_backend_sync_security_group_id") or "").strip()
        if group and group != "null":
            return {"route": PRIVATE_ROUTE, "security_group": group}
        return {"route": PUBLIC_ROUTE}
    try:
        from google.api_core.exceptions import NotFound

        client = await _gcp_firewalls(session)
        wanted = _gcp_rule(identity)
        try:
            current = client.get(project=identity.account, firewall=SYNC_RULE)
        except NotFound:
            current = None
        if current is None:
            client.insert(project=identity.account, firewall_resource=wanted).result(timeout=120)
        elif (
            list(current.source_ranges) != list(wanted.source_ranges)
            or current.priority != SYNC_PRIORITY
            or list(current.target_tags) != [SYNC_TAG]
        ):
            client.patch(project=identity.account, firewall=SYNC_RULE, firewall_resource=wanted).result(timeout=120)
    except Exception as exc:
        logger.warning("Private backend sync route could not be prepared: %s", type(exc).__name__)
        raise ValidationError(ROUTE_FAILED) from None
    return {"route": PRIVATE_ROUTE, "network": identity.network or "default", "tag": SYNC_TAG}
