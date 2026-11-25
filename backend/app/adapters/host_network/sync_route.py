"""The GCP firewall rule for the backend's private Work Node sync route (plan 03B).

Provider side of app.services.backend_sync_route: tcp:22 only from the application VM's primary
internal IPv4 /32 to the backend-sync tag.
"""

from __future__ import annotations

import ipaddress


def gcp_firewalls_client(gcp_config: dict):
    from google.cloud import compute_v1

    from app.adapters.credentials.credential_injector import load_gcp_credentials

    return compute_v1.FirewallsClient(credentials=load_gcp_credentials(gcp_config))


def gcp_sync_rule(identity, *, name: str, tag: str, priority: int):
    from google.cloud import compute_v1

    address = ipaddress.ip_address(identity.internal_ip)
    if address.version != 4:
        raise ValueError("the application VM has no internal IPv4 address")
    return compute_v1.Firewall(
        name=name,
        network=f"projects/{identity.account}/global/networks/{identity.network or 'default'}",
        direction="INGRESS",
        priority=priority,
        source_ranges=[f"{address}/32"],
        target_tags=[tag],
        allowed=[compute_v1.Allowed(I_p_protocol="tcp", ports=["22"])],
        description="Managed by bioAF (plan 03B): backend output sync to Work Nodes over the private network.",
    )


def reconcile_gcp_sync_rule(client, project: str, wanted) -> None:
    """Create the rule, or patch it back when it drifted. Blocking (up to two minutes per
    operation): call it from a worker thread."""
    from google.api_core.exceptions import NotFound

    try:
        current = client.get(project=project, firewall=wanted.name)
    except NotFound:
        current = None
    if current is None:
        client.insert(project=project, firewall_resource=wanted).result(timeout=120)
    elif (
        list(current.source_ranges) != list(wanted.source_ranges)
        or current.priority != wanted.priority
        or list(current.target_tags) != list(wanted.target_tags)
    ):
        client.patch(project=project, firewall=wanted.name, firewall_resource=wanted).result(timeout=120)
