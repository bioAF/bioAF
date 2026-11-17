"""Terminal instructions for running pipeline runs and Jupyter/RStudio sessions.

Each request is checked in this order before any provider call: effective ``notebooks:launch``
permission (and API-key scope), the ``ssh_exec`` capability, the organization-scoped record,
its workload type, and its database state. The adapter then proves the recorded workload is live
(``describe_connection``). Before the audit entry is written, the record is read again and must
still name the same workload. bioAF records that instructions were generated, never that a shell
session was opened.
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.capabilities import CapabilityNotSupported
from app.adapters.models import ConnectionDescription, ConnectionTarget
from app.adapters.registry import get_compute_adapter, get_notebook_adapter, require_capability
from app.api.dependencies import require_permission
from app.database import get_session
from app.exceptions import ConnectionTargetInvalidError, ConnectionUnavailableError
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun
from app.schemas.connection import ConnectionCommandResponse
from app.services.audit_service import log_action
from app.services.connection_access import (
    NOTEBOOK_STATES,
    NOTEBOOK_TYPES,
    PIPELINE_STATES,
    notebook_target,
    pipeline_target,
)

router = APIRouter(tags=["ssh-connect"])

WARNING_TEXT = (
    "Commands you run and changes you make in this terminal are not tracked by bioAF. "
    "Editing files or configuration can change the running analysis, so do not modify its input data or "
    "pipeline configuration. These instructions describe the workload as it was just now: if it has since "
    "ended, refresh its details in bioAF and request new instructions."
)


async def _load(session: AsyncSession, model, record_id: int, org_id: int, *, fresh: bool = False):
    query = select(model).where(model.id == record_id, model.organization_id == org_id)
    if fresh:
        query = query.execution_options(populate_existing=True)
    return (await session.execute(query)).scalar_one_or_none()


async def _connect(
    session: AsyncSession,
    current_user: dict,
    *,
    model,
    record_id: int,
    not_found: str,
    target_for: Callable[[object], ConnectionTarget],
    adapter,
    audit_details: Callable[[object], dict],
) -> ConnectionCommandResponse:
    require_capability("ssh_exec")
    org_id = current_user["org_id"]
    record = await _load(session, model, record_id, org_id)
    if record is None:
        raise HTTPException(status_code=404, detail=not_found)
    target = target_for(record)
    extra = audit_details(record)
    # End the read transaction: no database locks are held while the provider is consulted.
    await session.rollback()

    description: ConnectionDescription = await adapter.describe_connection(target)

    current = await _load(session, model, record_id, org_id, fresh=True)
    try:
        still = target_for(current) if current is not None else None
    except (ConnectionTargetInvalidError, ConnectionUnavailableError):
        still = None
    if still != target:
        raise ConnectionUnavailableError(
            "This analysis changed while instructions were being prepared. Refresh its details before "
            "requesting new instructions.",
            reason="state_changed",
        )

    await log_action(
        session=session,
        user_id=int(current_user["sub"]),
        entity_type="container_session",
        entity_id=record_id,
        action="connection_command_generated",
        details={
            "target_type": target.kind,
            "record_id": record_id,
            "target_id": description.provider_ref,
            "provider_ref": description.provider_ref,
            "pod_name": description.pod_name,
            "namespace": description.namespace,
            "container": description.container,
            "context": description.context,
            **extra,
        },
    )
    await session.commit()

    return ConnectionCommandResponse(
        command=description.command,
        setup_guide=description.setup_guide,
        warning=WARNING_TEXT,
        target_type=target.kind,
        target_id=description.provider_ref,
        namespace=description.namespace,
    )


@router.post(
    "/api/pipeline-runs/{run_id}/connect",
    response_model=ConnectionCommandResponse,
)
async def connect_pipeline_run(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: dict = require_permission("notebooks", "launch"),
):
    """Terminal instructions for a running pipeline run's head pod."""
    return await _connect(
        session,
        current_user,
        model=PipelineRun,
        record_id=run_id,
        not_found="Pipeline run not found",
        target_for=pipeline_target,
        adapter=get_compute_adapter(),
        audit_details=lambda run: {"pipeline_name": run.pipeline_name},
    )


@router.post(
    "/api/sessions/{session_id}/connect",
    response_model=ConnectionCommandResponse,
)
async def connect_notebook_session(
    session_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: dict = require_permission("notebooks", "launch"),
):
    """Terminal instructions for a running or idle Jupyter or RStudio session."""
    return await _connect(
        session,
        current_user,
        model=NotebookSession,
        record_id=session_id,
        not_found="Notebook session not found",
        target_for=notebook_target,
        adapter=get_notebook_adapter(),
        audit_details=lambda nb: {"session_type": nb.session_type},
    )


@router.get("/api/infrastructure/compute/pods")
async def list_running_pods(
    session: AsyncSession = Depends(get_session),
    current_user: dict = require_permission("infrastructure", "configure"),
):
    """This organization's running terminal targets, from the database (no provider listing).

    A snapshot: each entry's ``record_id`` is what the per-record connect endpoints take, and the
    live check happens there. Records whose identity is conflicting or malformed are left out.
    """
    org_id = current_user["org_id"]
    runs = (
        await session.execute(
            select(PipelineRun)
            .where(PipelineRun.organization_id == org_id, PipelineRun.status.in_(PIPELINE_STATES))
            .order_by(PipelineRun.id)
        )
    ).scalars()
    sessions = (
        await session.execute(
            select(NotebookSession)
            .where(
                NotebookSession.organization_id == org_id,
                NotebookSession.session_type.in_(NOTEBOOK_TYPES),
                NotebookSession.status.in_(NOTEBOOK_STATES),
            )
            .order_by(NotebookSession.id)
        )
    ).scalars()

    pods = []
    for records, target_for, adapter in (
        (runs, pipeline_target, get_compute_adapter()),
        (sessions, notebook_target, get_notebook_adapter()),
    ):
        for record in records:
            try:
                target = adapter.connection_reference(target_for(record))
            except (ConnectionTargetInvalidError, ConnectionUnavailableError, CapabilityNotSupported):
                continue
            pods.append(
                {
                    "name": target.provider_ref,
                    "type": target.kind,
                    "status": record.status,
                    "namespace": target.namespace,
                    "record_id": record.id,
                }
            )

    return {"pods": pods, "total": len(pods)}
