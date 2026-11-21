"""Durable infrastructure operations: one accepted action, one owner, one truthful outcome.

Every infrastructure workflow (deploy, teardown, storage destroy, update, bootstrap, image build,
...) is admitted as one ``InfrastructureOperation`` before it does anything. Admission is atomic: the
operation takes the single ``exclusive_slot``, so a second action while one is accepted, running,
stopping, or awaiting recovery is refused with 409 and a reference to the running operation. The
slot is held across preflight, every Terraform child, and cleanup, and released only when the
outcome is settled. Opening a confirmation popup reserves nothing; acceptance does.

Nested work inherits ownership through ``current_operation_id``; the Terraform executor refuses to
start a run outside an operation while another operation holds the slot.

Retries: an ``Idempotency-Key`` scoped to the authenticated principal replays the same operation for
the same action and inputs, and is refused (422) for different inputs (ADR-050). A key is replayed
for 24 hours, and for as long as its operation still holds the slot.

Startup recovery (approved D1): a confirmed restart of the recorded backend container proves its
local Terraform processes stopped, so the operation becomes ``interrupted`` and releases the slot.
Anything less (another container, an unreadable container start, a server reload) leaves it
``recovery_required`` with the slot held until an administrator records the recovery.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, DomainError, StateError, UnprocessableError, ValidationError
from app.models.infrastructure_operation import InfrastructureOperation
from app.services.runner_identity import container_restarted, current_runner, pid_alive, same_container

logger = logging.getLogger("bioaf.infrastructure_operations")

ACTIVE_STATUSES = ("accepted", "running", "stopping", "recovery_required")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
IDEMPOTENCY_WINDOW = timedelta(hours=24)

# The popup title for each action (mockup M2).
ACTION_LABELS: dict[str, str] = {
    "compute_deploy": "Compute deployment",
    "compute_teardown": "Compute teardown",
    "storage_deploy": "Storage deployment",
    "storage_update": "Storage update",
    "storage_destroy": "Storage destroy",
    "infrastructure_update": "Infrastructure update",
    "cluster_config": "Cluster settings change",
    "image_build": "Image build",
    "notebook_image_build": "Notebook image build",
    "cellxgene_image_build": "Cellxgene image build",
    "foundation_bootstrap": "Infrastructure initialization",
    "billing_export_enable": "Billing export setup",
    "billing_export_teardown": "Billing export removal",
    "terraform_plan": "Infrastructure plan",
    "terraform_apply": "Infrastructure apply",
    "config_sync": "Configuration sync",
    "component_change": "Component change",
}

_current: ContextVar[int | None] = ContextVar("bioaf_infrastructure_operation", default=None)

# The longest a user-requested cancellation may take to confirm stopping (approved M16). The stop is
# requested at once; this is the deadline for confirming it, after which the operation needs recovery.
CANCEL_BUDGET = timedelta(seconds=120)
CANCEL_UNCONFIRMED = "bioAF asked Terraform to stop, but could not confirm that it did."

# The task running each operation in this process, so a cancellation can reach it.
_op_tasks: dict[int, asyncio.Task] = {}


def _register_task(operation_id: int) -> None:
    task = asyncio.current_task()
    if task is not None:
        _op_tasks[operation_id] = task


def _unregister_task(operation_id: int) -> None:
    task = _op_tasks.get(operation_id)
    if task is asyncio.current_task():
        _op_tasks.pop(operation_id, None)


class InfrastructureBusyError(ConflictError):
    """Another infrastructure operation holds installation-wide exclusion."""

    code = "infrastructure_busy"


class IdempotencyKeyReusedError(UnprocessableError):
    """The Idempotency-Key was already used for a different request."""

    code = "idempotency_key_reused"


class ProcessNotSettled(Exception):
    """A Terraform child process could not be confirmed to have exited."""


def current_operation_id() -> int | None:
    return _current.get()


@dataclass(frozen=True)
class Principal:
    user_id: int | None
    api_key_id: int | None = None

    @property
    def key(self) -> str:
        return f"api_key:{self.api_key_id}" if self.api_key_id is not None else f"user:{self.user_id}"

    @classmethod
    def from_user(cls, user: dict) -> Principal:
        api_key_id = user.get("api_key_id")
        return cls(int(user["sub"]), int(api_key_id) if api_key_id is not None else None)


@dataclass(frozen=True)
class OperationResult:
    status: str
    message: str | None = None
    category: str | None = None
    partial_effects: dict | None = None


def succeeded(message: str | None = None, **effects: Any) -> OperationResult:
    return OperationResult("succeeded", message, None, effects or None)


def failed(message: str, category: str = "failed", **effects: Any) -> OperationResult:
    return OperationResult("failed", message, category, effects or None)


def fingerprint(action: str, inputs: dict | None) -> str:
    canonical = json.dumps({"action": action, "inputs": inputs or {}}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_factory():
    from app import database

    return database.async_session_factory


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


async def active_operation(session: AsyncSession) -> InfrastructureOperation | None:
    return (
        await session.execute(select(InfrastructureOperation).where(InfrastructureOperation.exclusive_slot == 1))
    ).scalar_one_or_none()


async def get_operation(session: AsyncSession, operation_id: int) -> InfrastructureOperation | None:
    return await session.get(InfrastructureOperation, operation_id)


async def _user_names(session: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    from app.models.user import User

    if not user_ids:
        return {}
    rows = (await session.execute(select(User.id, User.name, User.email).where(User.id.in_(user_ids)))).all()
    return {row.id: (row.name or row.email) for row in rows}


async def _current_resources(session: AsyncSession, op: InfrastructureOperation) -> dict | None:
    """The running Terraform child's planned and completed resources, for the per-resource list."""
    from app.models.component import TerraformRun

    if op.exclusive_slot != 1 or not op.child_run_ids:
        return None
    run = await session.get(TerraformRun, max(op.child_run_ids))
    if run is None or run.status not in ("planning", "applying", "awaiting_confirmation"):
        return None
    planned = [
        r["address"]
        for r in ((run.plan_json or {}).get("resources") or [])
        if isinstance(r, dict) and not str(r.get("address", "")).startswith("data.")
    ]
    return {"planned": planned, "completed": list(run.completed_resources or [])}


async def describe(session: AsyncSession, op: InfrastructureOperation) -> dict:
    """The operation as the status popup and banner show it."""
    names = await _user_names(
        session, {u for u in (op.started_by_user_id, op.cancel_requested_by_user_id) if u is not None}
    )
    resources = await _current_resources(session, op)
    return {
        "resources": resources,
        "id": op.id,
        "action": op.action,
        "label": ACTION_LABELS.get(op.action, op.action.replace("_", " ").capitalize()),
        "status": op.status,
        "phase": op.phase,
        "started_by": names.get(op.started_by_user_id) if op.started_by_user_id else None,
        "started_by_user_id": op.started_by_user_id,
        "accepted_at": op.accepted_at,
        "started_at": op.started_at,
        "finished_at": op.finished_at,
        "steps": list(op.steps or []),
        "message": op.message,
        "error_category": op.error_category,
        "child_run_ids": list(op.child_run_ids or []),
        "partial_effects": op.partial_effects,
        "cancel_requested_at": op.cancel_requested_at,
        "cancel_requested_by": names.get(op.cancel_requested_by_user_id) if op.cancel_requested_by_user_id else None,
        "cancel_deadline": op.cancel_deadline,
        "recovered_at": op.recovered_at,
        "outcome_seen_at": op.outcome_seen_at,
    }


# ---------------------------------------------------------------------------
# admission
# ---------------------------------------------------------------------------


async def _busy_error(session: AsyncSession, requested: str) -> InfrastructureBusyError:
    active = await active_operation(session)
    if active is None:
        return InfrastructureBusyError("Another infrastructure action started first. Try again shortly.")
    summary = await describe(session, active)
    label = summary["label"]
    return InfrastructureBusyError(
        f"{label} (Operation #{active.id}) is in progress. Your {ACTION_LABELS.get(requested, requested)} "
        "was not started.",
        details={"operation": _jsonable(summary)},
    )


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)))


async def accept(
    *,
    action: str,
    principal: Principal,
    idempotency_key: str | None = None,
    inputs: dict | None = None,
    generation: dict | None = None,
) -> tuple[int, bool]:
    """Admit ``action`` as the one running infrastructure operation; return (operation_id, replayed).

    Commits in its own transaction before returning, so the caller can spawn work that sees it.
    Raises InfrastructureBusyError (409) when another operation holds exclusion and
    IdempotencyKeyReusedError (422) when the key was used for different inputs.
    """
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 255:
            raise ValidationError("Idempotency-Key must be 1-255 characters.")
    request_fingerprint = fingerprint(action, inputs)
    async with _session_factory()() as session:
        if idempotency_key:
            prior = (
                await session.execute(
                    select(InfrastructureOperation)
                    .where(
                        InfrastructureOperation.principal == principal.key,
                        InfrastructureOperation.idempotency_key == idempotency_key,
                    )
                    .order_by(InfrastructureOperation.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if prior is not None and (prior.exclusive_slot == 1 or prior.accepted_at >= _now() - IDEMPOTENCY_WINDOW):
                if prior.request_fingerprint != request_fingerprint:
                    raise IdempotencyKeyReusedError("This Idempotency-Key was already used for a different request.")
                return prior.id, True

        op = InfrastructureOperation(
            action=action,
            status="accepted",
            exclusive_slot=1,
            started_by_user_id=principal.user_id,
            started_by_api_key_id=principal.api_key_id,
            principal=principal.key,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            inputs=inputs,
            deployment_generation=generation,
            steps=[{"key": "accepted", "label": "Accepted", "state": "done"}],
        )
        session.add(op)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise await _busy_error(session, action) from None
        logger.info("Accepted infrastructure operation %s (%s)", op.id, action)
        return op.id, False


async def ensure_admitted(session: AsyncSession, requested: str = "terraform_apply") -> None:
    """Refuse Terraform work outside an operation while another operation holds exclusion."""
    if current_operation_id() is not None:
        return
    if await active_operation(session) is not None:
        raise await _busy_error(session, requested)


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


class OperationHandle:
    """Writes one operation's progress in short transactions of its own."""

    def __init__(self, operation_id: int):
        self.id = operation_id

    async def _update(self, change: Callable[[InfrastructureOperation], None]) -> None:
        async with _session_factory()() as session:
            op = (
                await session.execute(
                    select(InfrastructureOperation)
                    .where(InfrastructureOperation.id == self.id)
                    .with_for_update(key_share=True)
                )
            ).scalar_one()
            change(op)
            await session.commit()

    async def step(
        self,
        key: str,
        label: str | None = None,
        state: str = "running",
        *,
        detail: str | None = None,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        def change(op: InfrastructureOperation) -> None:
            steps = [dict(s) for s in (op.steps or [])]
            found = next((s for s in steps if s.get("key") == key), None)
            if found is None:
                found = {"key": key, "label": label or key}
                steps.append(found)
            if label:
                found["label"] = label
            found["state"] = state
            if detail is not None:
                found["detail"] = detail
            if completed is not None:
                found["completed"] = completed
            if total is not None:
                found["total"] = total
            op.steps = steps
            if state == "running":
                op.phase = found["label"]

        await self._update(change)

    async def attach_run(self, run_id: int) -> None:
        def change(op: InfrastructureOperation) -> None:
            if run_id not in (op.child_run_ids or []):
                op.child_run_ids = [*(op.child_run_ids or []), run_id]

        await self._update(change)

    async def record_external_build(self, kind: str, build_id: str) -> None:
        def change(op: InfrastructureOperation) -> None:
            op.external_builds = [*(op.external_builds or []), {"kind": kind, "build_id": build_id}]

        await self._update(change)

    async def cancel_requested(self) -> bool:
        async with _session_factory()() as session:
            op = await session.get(InfrastructureOperation, self.id)
            return bool(op and op.cancel_requested_at is not None)


def _settle_steps(steps: list[dict], status: str) -> list[dict]:
    done_state = {"succeeded": "done", "failed": "failed", "cancelled": "stopped", "interrupted": "interrupted"}.get(
        status, "interrupted"
    )
    out = []
    for step in steps:
        step = dict(step)
        if step.get("state") == "running":
            step["state"] = done_state
        elif step.get("state") == "waiting":
            step["state"] = "done" if status == "succeeded" else "not_started"
        out.append(step)
    return out


# Provider build states that settle a cloud build, and what each means for its operation.
BUILD_TERMINAL = {
    "SUCCESS": "succeeded",
    "FAILURE": "failed",
    "TIMEOUT": "failed",
    "INTERNAL_ERROR": "failed",
    "EXPIRED": "failed",
    "CANCELLED": "cancelled",
}
_BUILD_LABELS = {"notebook": "Notebook image build", "cellxgene": "Cellxgene image build"}


def _unsettled_builds(op: InfrastructureOperation) -> list[dict]:
    return [b for b in (op.external_builds or []) if b.get("status") not in BUILD_TERMINAL]


def _combined_result(op: InfrastructureOperation, pending: dict) -> OperationResult:
    builds = op.external_builds or []
    status = pending.get("status", "succeeded")
    message = pending.get("message")
    category = pending.get("category")
    if status == "succeeded":
        failed_builds = [b for b in builds if BUILD_TERMINAL.get(b.get("status", "")) == "failed"]
        cancelled = [b for b in builds if b.get("status") == "CANCELLED"]
        if failed_builds:
            label = _BUILD_LABELS.get(failed_builds[0].get("kind", ""), "Image build")
            return failed(f"{label} failed.", "build_failed")
        if cancelled:
            return OperationResult("cancelled", message or "The image build was cancelled.", "cancelled")
    return OperationResult(status, message, category)


async def finish(operation_id: int, result: OperationResult) -> None:
    """Record the settled outcome. Exclusion is released unless recovery is required.

    A cloud build the operation submitted may still be running after its own work ends: submission
    is not completion. Then the outcome is held and the operation keeps exclusion until the build
    monitor reports every build settled (``settle_external_build``).
    """
    waiting = False
    live = _live_children(operation_id)

    def change(op: InfrastructureOperation) -> None:
        nonlocal waiting, result
        if op.exclusive_slot is None and op.status in TERMINAL_STATUSES:
            # Already settled (a completion raced a cancellation, or a duplicate finish): keep it.
            return
        if op.cancel_requested_at is not None and result.status == "interrupted":
            result = OperationResult(
                "cancelled",
                "Cancelled. Work already done was not undone.",
                "cancelled",
                result.partial_effects,
            )
        if result.status != "recovery_required" and live(op):
            # A Terraform child is still alive: never release exclusion under it.
            result = OperationResult("recovery_required", CANCEL_UNCONFIRMED, "process_not_settled")
        if result.status != "recovery_required" and _unsettled_builds(op):
            waiting = True
            op.partial_effects = {
                **(op.partial_effects or {}),
                **(result.partial_effects or {}),
                "pending_result": {"status": result.status, "message": result.message, "category": result.category},
            }
            op.steps = [
                dict(s, state="done")
                if s.get("state") == "running" and not s.get("key", "").startswith("build:")
                else s
                for s in (op.steps or [])
            ]
            op.phase = "Waiting for image builds to finish"
            return
        op.status = result.status
        op.message = result.message
        op.error_category = result.category
        if result.partial_effects is not None:
            op.partial_effects = result.partial_effects
        op.steps = _settle_steps(list(op.steps or []), result.status)
        op.finished_at = _now()
        if result.status != "recovery_required":
            op.exclusive_slot = None
        op.phase = None

    await OperationHandle(operation_id)._update(change)
    if waiting:
        logger.info("Infrastructure operation %s waits for its image builds", operation_id)
    else:
        logger.info("Infrastructure operation %s finished: %s", operation_id, result.status)


def _live_children(operation_id: int):
    def live(op: InfrastructureOperation) -> bool:
        from app.services.terraform_executor import live_child_pids

        return any(pid_alive(pid) for pid in live_child_pids(operation_id, list(op.child_run_ids or [])))

    return live


async def request_cancel(session: AsyncSession, operation_id: int, user_id: int) -> InfrastructureOperation:
    """Record a cancellation request and ask the operation's owned work to stop (M16).

    Stops remaining work at the next safe point and never undoes finished work. The request is
    durable and idempotent: a repeat neither restarts the deadline nor signals again. A finished
    operation keeps its outcome; a recovery-required one needs recovery, not cancellation.
    """
    from app.exceptions import NotFoundError
    from app.services.audit_service import log_action

    op = (
        await session.execute(
            select(InfrastructureOperation)
            .where(InfrastructureOperation.id == operation_id)
            .with_for_update(key_share=True)
        )
    ).scalar_one_or_none()
    if op is None:
        raise NotFoundError("Operation not found")
    if op.status in TERMINAL_STATUSES:
        raise StateError("This operation already finished, so there is nothing to cancel.")
    if op.status == "recovery_required":
        raise StateError("This operation needs recovery, not cancellation.")
    first = op.cancel_requested_at is None
    if first:
        now = _now()
        op.cancel_requested_at = now
        op.cancel_requested_by_user_id = user_id
        op.cancel_deadline = now + CANCEL_BUDGET
        op.status = "stopping"
        op.phase = "Stopping"
        await log_action(
            session,
            user_id=user_id,
            entity_type="infrastructure_operation",
            entity_id=op.id,
            action="infrastructure_operation_cancel_requested",
            details={"operation_id": op.id, "action": op.action},
        )
    builds = list(op.external_builds or [])
    await session.commit()
    if first:
        await _signal_cancel(operation_id, builds)
    return op


async def _signal_cancel(operation_id: int, builds: list[dict]) -> None:
    """Ask owned work to stop: the running task (Terraform stops gracefully) and unsettled cloud builds."""
    from app import runtime

    task = _op_tasks.get(operation_id)
    if task is not None and not task.done():
        task.cancel()
    for build in builds:
        if build.get("status") in BUILD_TERMINAL:
            continue
        try:
            await _cancel_cloud_build(build.get("kind", ""), build.get("build_id", ""))
        except Exception as exc:
            # The monitor still settles it; an unconfirmed stop becomes recovery at the deadline.
            logger.warning("Could not request build cancellation (error_type=%s)", type(exc).__name__)
    runtime.spawn(_cancel_watchdog(operation_id), role="infrastructure-cancel-watchdog")


async def _cancel_cloud_build(kind: str, build_id: str) -> None:
    if kind == "notebook":
        from app.services.notebook_image_service import request_provider_cancel
    else:
        from app.services.cellxgene_image_service import request_provider_cancel  # type: ignore[no-redef]
    async with _session_factory()() as session:
        await request_provider_cancel(session, build_id)
        await session.commit()


async def _cancel_watchdog(operation_id: int) -> None:
    """At the deadline, an operation that has not confirmed stopping needs recovery (lock kept)."""
    async with _session_factory()() as session:
        op = await session.get(InfrastructureOperation, operation_id)
        deadline = op.cancel_deadline if op else None
    if deadline is None:
        return
    await asyncio.sleep(max(0.0, (deadline - _now()).total_seconds()))
    await expire_cancellation(operation_id)


async def expire_cancellation(operation_id: int) -> bool:
    """Move a stopping operation past its deadline to recovery required. Returns whether it did."""
    expired = False

    def change(op: InfrastructureOperation) -> None:
        nonlocal expired
        if op.status == "stopping" and op.cancel_deadline is not None and op.cancel_deadline <= _now():
            op.status = "recovery_required"
            op.message = CANCEL_UNCONFIRMED
            op.error_category = "cancel_unconfirmed"
            op.phase = None
            expired = True

    await OperationHandle(operation_id)._update(change)
    return expired


async def operation_for_build(session: AsyncSession, build_id: str) -> int | None:
    """The operation holding exclusion that submitted ``build_id``, if any."""
    active = await active_operation(session)
    if active is None:
        return None
    return active.id if any(b.get("build_id") == build_id for b in (active.external_builds or [])) else None


async def begin_image_build(kind: str, user_id: int | None) -> tuple[int, bool]:
    """Admit a cloud image build before it is submitted; return (operation_id, created).

    A build started inside an operation (a deploy draining queued components) belongs to it.
    Otherwise the build is its own operation, which raises InfrastructureBusyError while
    another holds exclusion.
    """
    operation_id = current_operation_id()
    if operation_id is not None:
        return operation_id, False
    operation_id, _ = await accept(action=f"{kind}_image_build", principal=Principal(user_id))
    handle = OperationHandle(operation_id)
    await _start(handle)

    def pending(op: InfrastructureOperation) -> None:
        op.partial_effects = {"pending_result": {"status": "succeeded", "message": "Image build finished."}}

    await handle._update(pending)
    return operation_id, True


async def record_image_build(operation_id: int, kind: str, build_id: str) -> None:
    handle = OperationHandle(operation_id)

    def change(op: InfrastructureOperation) -> None:
        op.external_builds = [*(op.external_builds or []), {"kind": kind, "build_id": build_id, "status": "WORKING"}]
        if op.partial_effects and "pending_result" in op.partial_effects:
            op.phase = "Waiting for image builds to finish"

    await handle._update(change)
    await handle.step(f"build:{build_id}", f"Building {kind} image", "running")


async def settle_external_build(build_id: str, provider_status: str) -> None:
    """Record a build's provider-confirmed status; settle its operation once nothing is pending."""
    if provider_status not in BUILD_TERMINAL:
        return
    async with _session_factory()() as session:
        ops = (
            await session.execute(
                select(InfrastructureOperation)
                .where(InfrastructureOperation.exclusive_slot == 1)
                .with_for_update(key_share=True)
            )
        ).scalars()
        for op in ops:
            builds = [dict(b) for b in (op.external_builds or [])]
            match = next((b for b in builds if b.get("build_id") == build_id), None)
            if match is None or match.get("status") in BUILD_TERMINAL:
                continue
            match["status"] = provider_status
            op.external_builds = builds
            state = {"succeeded": "done", "failed": "failed", "cancelled": "stopped"}[BUILD_TERMINAL[provider_status]]
            op.steps = [dict(s, state=state) if s.get("key") == f"build:{build_id}" else s for s in (op.steps or [])]
            pending = (op.partial_effects or {}).get("pending_result")
            if pending is not None and not _unsettled_builds(op):
                result = _combined_result(op, pending)
                op.status = result.status
                op.message = result.message
                op.error_category = result.category
                op.finished_at = _now()
                op.exclusive_slot = None
                op.phase = None
                op.steps = _settle_steps(list(op.steps or []), result.status)
                logger.info("Infrastructure operation %s finished after its image builds: %s", op.id, result.status)
        await session.commit()


async def run_operation(operation_id: int, body: Callable[[OperationHandle], Awaitable[OperationResult]]) -> None:
    """Run ``body`` as the operation's owner and record its settled outcome.

    Unknown failures are recorded with a generic message; a DomainError's message is already safe
    for users. A cancellation of this task records an interrupted outcome (the executor settles its
    children first); a child that could not be confirmed stopped records recovery required.
    """
    handle = OperationHandle(operation_id)
    token = _current.set(operation_id)
    _register_task(operation_id)
    try:
        await _start(handle)
        try:
            result = await body(handle)
        except ProcessNotSettled:
            logger.error(
                "Infrastructure operation %s: a Terraform process could not be confirmed stopped", operation_id
            )
            result = OperationResult(
                "recovery_required",
                "bioAF could not confirm that Terraform stopped.",
                "process_not_settled",
            )
        except asyncio.CancelledError:
            with _shielded():
                await finish(
                    operation_id,
                    OperationResult("interrupted", "The bioAF server stopped while this was running.", "interrupted"),
                )
            raise
        except DomainError as exc:
            result = failed(str(exc) or "The operation failed.", exc.code)
        except Exception as exc:
            logger.exception("Infrastructure operation %s failed (error_type=%s)", operation_id, type(exc).__name__)
            result = failed(
                "The operation failed unexpectedly. The technical detail is in the server log.", "unexpected"
            )
        await finish(operation_id, result)
    finally:
        _unregister_task(operation_id)
        _reset(token)


class _shielded:
    def __enter__(self):
        import anyio

        self._scope = anyio.CancelScope(shield=True)
        return self._scope.__enter__()

    def __exit__(self, *exc):
        return self._scope.__exit__(*exc)


async def _start(handle: OperationHandle) -> None:
    def start(op: InfrastructureOperation) -> None:
        op.status = "running"
        op.started_at = op.started_at or _now()
        op.runner = op.runner or current_runner()

    await handle._update(start)


def _reset(token) -> None:
    try:
        _current.reset(token)
    except ValueError:
        # An async generator closed from another context: the variable dies with that context.
        pass


@asynccontextmanager
async def owned(operation_id: int) -> AsyncIterator[OperationHandle]:
    """Run request-scoped work as the operation's owner.

    A failure records a failed outcome (a DomainError keeps its safe message) and propagates;
    a normal exit leaves the operation running for the caller to finish or hand on.
    """
    handle = OperationHandle(operation_id)
    token = _current.set(operation_id)
    _register_task(operation_id)
    try:
        await _start(handle)
        yield handle
    except asyncio.CancelledError:
        with _shielded():
            await finish(operation_id, OperationResult("interrupted", "The request was interrupted.", "interrupted"))
        raise
    except ProcessNotSettled:
        with _shielded():
            await finish(
                operation_id,
                OperationResult(
                    "recovery_required", "bioAF could not confirm that Terraform stopped.", "process_not_settled"
                ),
            )
        raise
    except DomainError as exc:
        await finish(operation_id, failed(str(exc) or "The operation failed.", exc.code))
        raise
    except Exception:
        await finish(
            operation_id,
            failed("The operation failed unexpectedly. The technical detail is in the server log.", "unexpected"),
        )
        raise
    finally:
        _unregister_task(operation_id)
        _reset(token)


async def operation_stream(operation_id: int, events: AsyncIterator, *, terminal: str = "stack") -> AsyncIterator:
    """Pass a stack generator's events through to an SSE response while the operation owns them.

    The stream's outcome becomes the operation's: a terminal event settles it, a client
    disconnect records it interrupted (the executor settles Terraform first), and progress
    without a terminal event is never recorded as success.
    """
    handle = OperationHandle(operation_id)
    tracker = StackTracker(handle, terminal=terminal)
    token = _current.set(operation_id)
    _register_task(operation_id)
    try:
        await _start(handle)
        await handle.step("prerequisites", "Checking prerequisites", "running")
        first = True
        async for event in events:
            if first:
                await handle.step("prerequisites", state="done")
                first = False
            await tracker.observe(event)
            yield event
        result = tracker.result()
        if result.status == "succeeded":
            await handle.step("cleanup", "Final cleanup", "done")
        await finish(operation_id, result)
    except (asyncio.CancelledError, GeneratorExit):
        with _shielded():
            await finish(
                operation_id,
                tracker.outcome
                or OperationResult("interrupted", "The connection closed while this was running.", "interrupted"),
            )
        raise
    except ProcessNotSettled:
        with _shielded():
            await finish(
                operation_id,
                OperationResult(
                    "recovery_required", "bioAF could not confirm that Terraform stopped.", "process_not_settled"
                ),
            )
        raise
    except DomainError as exc:
        await finish(operation_id, failed(str(exc) or "The operation failed.", exc.code))
        raise
    except Exception:
        await finish(
            operation_id,
            failed("The operation failed unexpectedly. The technical detail is in the server log.", "unexpected"),
        )
        raise
    finally:
        _unregister_task(operation_id)
        _reset(token)


def spawn_operation(
    operation_id: int, body: Callable[[OperationHandle], Awaitable[OperationResult]], *, role: str
) -> None:
    """Start the accepted operation in the background under the runtime's task owner."""
    from app import runtime

    runtime.spawn(run_operation(operation_id, body), role=role)


# ---------------------------------------------------------------------------
# progress from stack events
# ---------------------------------------------------------------------------

# A progress message that starts a step: (message prefix, step key, step label).
_STEP_HINTS: tuple[tuple[str, str, str], ...] = (
    ("Deploying storage infrastructure", "storage", "Creating storage resources"),
    ("Deploying image build infrastructure", "image_build", "Creating image build resources"),
    ("Checking GCE zone capacity", "capacity", "Checking zone capacity"),
    ("Deploying compute infrastructure", "compute", "Creating compute resources"),
    ("Tearing down compute", "compute_teardown", "Removing compute resources"),
    ("Destroying compute", "compute_teardown", "Removing compute resources"),
    ("Destroying image build infrastructure", "image_build_destroy", "Removing image build resources"),
    ("Destroying storage infrastructure", "storage_destroy", "Removing storage resources"),
    ("Pausing uploads", "pause_uploads", "Pausing uploads"),
    ("Waiting for uploads", "drain_uploads", "Waiting for uploads to finish"),
)
_COUNTED_EVENTS = {"resource_complete", "planned_resources", "phase_complete", "apply_complete", "progress"}


@dataclass
class StackTracker:
    """Turns a stack generator's events into operation steps and an outcome."""

    handle: OperationHandle
    # "stack": stack_complete / stack_error settle the outcome. "terraform": a single Terraform
    # command's apply_complete / apply_error do.
    terminal: str = "stack"
    current: str | None = None
    counts: tuple[int, int] = (0, 0)
    outcome: OperationResult | None = None
    _last_write: float = 0.0
    seen_steps: set[str] = field(default_factory=set)

    async def start(self, key: str, label: str) -> None:
        if self.current and self.current != key:
            await self.handle.step(self.current, state="done")
        self.current = key
        self.counts = (0, 0)
        self.seen_steps.add(key)
        await self.handle.step(key, label, "running")

    async def observe(self, event) -> None:
        from app.services.terraform_stream import INTERRUPTED_EVENT

        etype = event.event_type
        message = event.message or ""
        extra = event.extra or {}
        if etype == "step":
            # A structured step from the workflow: its own key, label, state and detail.
            key, state = extra.get("key"), extra.get("state", "running")
            if key:
                if state == "running" and self.current and self.current != key:
                    await self.handle.step(self.current, state="done")
                await self.handle.step(key, extra.get("label"), state, detail=extra.get("detail"))
                self.current = key if state == "running" else (None if self.current == key else self.current)
            return
        if etype == "progress":
            for prefix, key, label in _STEP_HINTS:
                if message.startswith(prefix):
                    await self.start(key, label)
                    return
            if message.startswith("Emptying bucket "):
                bucket = message.removeprefix("Emptying bucket ").rstrip(".")
                await self.start(f"empty:{bucket}", f"Emptying {bucket}")
                return
        if self.terminal == "terraform" and etype in ("apply_complete", "apply_error"):
            etype = "stack_complete" if etype == "apply_complete" else "stack_error"
            message = "Apply complete" if etype == "stack_complete" else "Terraform reported an error."
        if etype == "stack_complete":
            self.outcome = succeeded(message or None, **(extra.get("effects") or {}))
        elif etype == "stack_error":
            self.outcome = failed(
                message or "The operation failed.",
                extra.get("category", "stack_error"),
                **(extra.get("effects") or {}),
            )
            if self.current:
                await self.handle.step(self.current, state="failed", detail=extra.get("step_detail") or message or None)
        elif etype == INTERRUPTED_EVENT:
            self.outcome = OperationResult("interrupted", "The operation was interrupted.", "interrupted")
        elif etype in _COUNTED_EVENTS and self.current and event.resources_total:
            counts = (event.resources_completed, event.resources_total)
            loop = asyncio.get_running_loop().time()
            if counts != self.counts and (loop - self._last_write > 1.0 or etype != "progress"):
                self.counts = counts
                self._last_write = loop
                await self.handle.step(self.current, completed=counts[0], total=counts[1])

    def result(self) -> OperationResult:
        # Progress without a terminal event never counts as success.
        return self.outcome or failed("The operation ended without reporting an outcome.", "no_outcome")


async def drive_stack(
    handle: OperationHandle,
    make_events: Callable[[AsyncSession], AsyncIterator],
    *,
    prerequisites: bool = True,
) -> OperationResult:
    """Run a stack generator in its own session under the operation, recording steps."""
    from app.services.terraform_stream import owned_event_stream

    tracker = StackTracker(handle)
    if prerequisites:
        await handle.step("prerequisites", "Checking prerequisites", "running")
    async with _session_factory()() as session:
        async with owned_event_stream(session, make_events(session)) as events:
            first = True
            async for event in events:
                if first and prerequisites:
                    await handle.step("prerequisites", state="done")
                    first = False
                await session.commit()
                await tracker.observe(event)
    if prerequisites and tracker.current is None and tracker.outcome is None:
        await handle.step("prerequisites", state="done")
    result = tracker.result()
    if result.status == "succeeded":
        await handle.step("cleanup", "Final cleanup", "done")
    return result


# ---------------------------------------------------------------------------
# startup recovery and administrator recovery (D1)
# ---------------------------------------------------------------------------


async def reconcile_on_startup(session: AsyncSession) -> dict[str, int]:
    """Settle operations a restart left behind, using recorded runner evidence only."""
    now = current_runner()
    counts = {"interrupted": 0, "recovery_required": 0, "external": 0}
    ops = (
        await session.execute(
            select(InfrastructureOperation).where(
                InfrastructureOperation.status.in_(("accepted", "running", "stopping"))
            )
        )
    ).scalars()
    for op in ops:
        if op.external_builds and op.status == "running":
            # An external cloud build may still run without this container; its monitor settles it.
            counts["external"] += 1
            continue
        if op.runner is None:
            # Accepted but never started: nothing ran, so nothing needs recovery.
            _mark(
                op, "interrupted", "The bioAF server restarted before this started. Nothing was changed.", "not_started"
            )
            counts["interrupted"] += 1
        elif container_restarted(op.runner, now):
            if op.cancel_requested_at is not None:
                _mark(op, "cancelled", "Cancelled. Work already done was not undone.", "cancelled")
            else:
                _mark(
                    op,
                    "interrupted",
                    "The bioAF server restarted while this was running, and Terraform stopped with it.",
                    "interrupted",
                )
            await _fail_child_runs(session, op)
            counts["interrupted"] += 1
        else:
            op.status = "recovery_required"
            op.error_category = "runner_unconfirmed"
            op.message = "bioAF could not confirm that Terraform stopped."
            op.phase = None
            counts["recovery_required"] += 1
    await session.flush()
    return counts


def _mark(op: InfrastructureOperation, status: str, message: str, category: str) -> None:
    op.status = status
    op.message = message
    op.error_category = category
    op.steps = _settle_steps(list(op.steps or []), status)
    op.finished_at = _now()
    op.exclusive_slot = None
    op.phase = None


async def _fail_child_runs(session: AsyncSession, op: InfrastructureOperation) -> None:
    from sqlalchemy import update

    from app.models.component import TerraformRun
    from app.services.terraform_executor import RUN_INTERRUPTED_MESSAGE

    if not op.child_run_ids:
        return
    await session.execute(
        update(TerraformRun)
        .where(
            TerraformRun.id.in_(op.child_run_ids),
            TerraformRun.status.in_(("planning", "applying", "awaiting_confirmation")),
        )
        .values(status="failed", error_message=RUN_INTERRUPTED_MESSAGE, completed_at=_now())
    )


RECOVERY_REFUSED = (
    "bioAF still sees this Terraform process running, so it cannot be marked as recovered. "
    "Wait for it to finish or stop it on the server."
)


def _process_still_seen(op: InfrastructureOperation) -> bool:
    from app.services.terraform_executor import live_child_pids

    if any(pid_alive(pid) for pid in live_child_pids(op.id, op.child_run_ids or [])):
        return True
    runner = op.runner or {}
    pid = runner.get("pid")
    return bool(
        same_container(runner, current_runner()) and isinstance(pid, int) and pid_alive(pid) and pid != _own_pid()
    )


def _own_pid() -> int:
    import os

    return os.getpid()


async def recover(session: AsyncSession, operation_id: int, user_id: int, *, reason: str) -> InfrastructureOperation:
    """An administrator records that a recovery-required operation's Terraform process is gone."""
    from app.services.audit_service import log_action

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to mark the operation as recovered.")
    op = (
        await session.execute(
            select(InfrastructureOperation).where(InfrastructureOperation.id == operation_id).with_for_update()
        )
    ).scalar_one_or_none()
    if op is None:
        from app.exceptions import NotFoundError

        raise NotFoundError("Operation not found")
    if op.status != "recovery_required":
        raise StateError("Only an operation that needs recovery can be marked as recovered.")
    if _process_still_seen(op):
        raise StateError(RECOVERY_REFUSED)
    _mark(
        op,
        "interrupted",
        "Marked as recovered by an administrator. Some changes may already exist; run the action again to finish.",
        "recovered",
    )
    op.recovered_at = _now()
    op.recovered_by_user_id = user_id
    op.recovery_reason = reason
    await _fail_child_runs(session, op)
    await _clear_stale_locks(session, op)
    await log_action(
        session,
        user_id=user_id,
        entity_type="infrastructure_operation",
        entity_id=op.id,
        action="infrastructure_operation_recovered",
        details={"operation_id": op.id, "action": op.action, "reason": reason},
    )
    await session.flush()
    return op


async def _clear_stale_locks(session: AsyncSession, op: InfrastructureOperation) -> None:
    from app.models.component import TerraformRun
    from app.services.terraform_executor import TerraformExecutor

    if not op.child_run_ids:
        return
    modules = (
        await session.execute(select(TerraformRun.module_name).where(TerraformRun.id.in_(op.child_run_ids)))
    ).scalars()
    for module in {m for m in modules if m}:
        try:
            await TerraformExecutor._auto_cleanup_lock(session, module)
        except Exception as exc:
            logger.warning("Could not clear the Terraform lock for %s (error_type=%s)", module, type(exc).__name__)


async def mark_seen(session: AsyncSession, operation_id: int) -> None:
    op = await session.get(InfrastructureOperation, operation_id)
    if op is not None and op.outcome_seen_at is None and op.status in TERMINAL_STATUSES:
        op.outcome_seen_at = _now()
        await session.flush()
