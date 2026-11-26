"""Containment of notebook sessions launched before the protected gateway (plan 03.5F, owner decision 3).

Those sessions have a public LoadBalancer Service and no notebook authentication. The preferred
containment blocks that ingress while preserving the workload and storage: the session's own
(ownership-verified) Service is changed to ClusterIP, replaced only if unchanged since it was read.
Nothing is deleted or stopped. The old Pods also hold the namespace's Kubernetes credential, which
cannot be removed in place, so the report marks them for save (Stop) and relaunch.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.notebooks.readiness import INTERACTIVE_SESSION_TYPES
from app.models.notebook_session import NotebookSession

logger = logging.getLogger("bioaf.notebook_exposure")

# Sessions whose resources may still exist and be reachable.
POSSIBLY_LIVE = ("pending", "starting", "running", "idle", "stopping")


@dataclass
class ExposureFinding:
    session_id: int
    user_id: int
    service_name: str
    # True: public listener; False: none; None: could not be read.
    exposed: bool | None
    contained: bool
    relaunch_needed: bool
    detail: str


def _pod_mounts_api_token(core, workload, timeout) -> bool:
    try:
        pod = core.read_namespaced_pod(name=workload.pod_name, namespace=workload.namespace, _request_timeout=timeout)
    except Exception:
        return True  # unknown: assume the old default (mounted)
    return getattr(pod.spec, "automount_service_account_token", None) is not False


def _contain_one(core, workload, apply: bool, timeout: float) -> tuple[bool | None, bool, str]:
    from app.adapters.notebooks.lifecycle import _service_belongs

    try:
        svc = core.read_namespaced_service(
            name=workload.service_name, namespace=workload.namespace, _request_timeout=timeout
        )
    except Exception as exc:
        status = getattr(exc, "status", None)
        if status == 404:
            return False, False, "no Service"
        return None, False, f"Service could not be read ({status or type(exc).__name__})"
    if not _service_belongs(svc, workload):
        return None, False, "Service not verified as this session's own; left unchanged"
    if (svc.spec.type or "ClusterIP") != "LoadBalancer":
        return False, False, "no public listener"
    if not apply:
        return True, False, "public LoadBalancer without notebook authentication"
    svc.spec.type = "ClusterIP"
    for port in svc.spec.ports or []:
        port.node_port = None
    svc.spec.external_traffic_policy = None
    for attr in (
        "load_balancer_ip",
        "load_balancer_source_ranges",
        "load_balancer_class",
        "allocate_load_balancer_node_ports",
        "health_check_node_port",
    ):
        if hasattr(svc.spec, attr):
            setattr(svc.spec, attr, None)
    svc.status = None
    try:
        # resource_version from the read: refused (409) if the Service changed meanwhile.
        core.replace_namespaced_service(
            name=workload.service_name, namespace=workload.namespace, body=svc, _request_timeout=timeout
        )
    except Exception as exc:
        return True, False, f"Service could not be changed ({getattr(exc, 'status', None) or type(exc).__name__})"
    return True, True, "public listener removed; the session keeps running"


async def contain_exposed_sessions(session: AsyncSession, adapter, *, apply: bool) -> list[ExposureFinding]:
    from app.adapters.notebooks.lifecycle import REQUEST_TIMEOUT_SECONDS

    rows = (
        (
            await session.execute(
                select(NotebookSession)
                .where(
                    NotebookSession.session_type.in_(INTERACTIVE_SESSION_TYPES),
                    NotebookSession.status.in_(POSSIBLY_LIVE),
                )
                .order_by(NotebookSession.id)
            )
        )
        .scalars()
        .all()
    )
    core = adapter._get_k8s_core_client()
    findings: list[ExposureFinding] = []
    for ns in rows:
        workload, why = adapter.resolve_workload(ns)
        if workload is None:
            findings.append(ExposureFinding(ns.id, ns.user_id, "", None, False, True, f"workload not resolved ({why})"))
            continue
        exposed, contained, detail = await asyncio.to_thread(
            _contain_one, core, workload, apply, REQUEST_TIMEOUT_SECONDS
        )
        relaunch = await asyncio.to_thread(_pod_mounts_api_token, core, workload, REQUEST_TIMEOUT_SECONDS)
        if contained:
            ns.access_url = None
            ns.proxy_url = None
            ns.provider_metadata = {
                **(ns.provider_metadata or {}),
                "exposure": {"contained_at": datetime.now(timezone.utc).isoformat(), "service": "ClusterIP"},
            }
            logger.info("Notebook session %s: public listener removed (containment)", ns.id)
        findings.append(ExposureFinding(ns.id, ns.user_id, workload.service_name, exposed, contained, relaunch, detail))
    await session.flush()
    return findings
