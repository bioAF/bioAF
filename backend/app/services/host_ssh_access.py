"""Application-host SSH: automatic preflight, guest diagnostics and the filter lifecycle (plan 03B).

- **Check access** is read-only: it inspects the protected recovery rules through the provider,
  reads the host's latest fixed guest diagnostics and asks the host agent for a fresh report.
- **Apply changes** (infrastructure:deploy) reconciles the protected recovery sources, re-runs the
  preflight, and only then applies or removes the editable filter. While recovery Needs attention
  the filter is not applied and current access is kept.
- Evidence is labelled honestly: ``configured_automatic`` from these machine checks;
  ``login_tested`` only for a recorded successful installer probe bound to this instance and the
  current filter revision. Neither proves a particular person can sign in to the cloud console.
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
import secrets
from datetime import datetime, timedelta, timezone

from app.exceptions import ConflictError, ValidationError
from app.services.ssh_access_policy import _validated_entries, normalize_range

logger = logging.getLogger("bioaf.host_ssh_access")

FILTER_KEY = "app_host_ssh_filter"
PROBE_KEY = "ssh_recovery_probe"
DIAGNOSTICS_FILE = "ssh-recovery-diagnostics.json"
DIAGNOSTICS_MAX_AGE = timedelta(hours=1)
UNFINISHED_CHANGE = (
    "An earlier change to the SSH filter did not finish. Apply changes puts the previous rules back first."
)
UNFINISHED_RESTORE_FAILED = (
    "An earlier change to the SSH filter did not finish, and bioAF could not put the previous rules back. "
    "Cloud recovery access is unchanged. Try again shortly."
)
NEEDS_ATTENTION_REFUSAL = (
    "Cloud recovery access needs attention, so the SSH filter was not changed. Your current access is unchanged."
)


def _empty() -> dict:
    return {
        "version": 1,
        "draft": {"version": 0, "enabled": False, "ranges": []},
        "applied": {"revision": 0, "enabled": False, "ranges": [], "resources": None},
        "previous": None,
        "recovery": {"status": "not_checked", "problems": [], "sources": [], "checked_at": None},
    }


async def _read(session) -> dict:
    from app.platform.platform_config_service import PlatformConfigService

    return _parse(await PlatformConfigService.get(session, FILTER_KEY))


async def _write(session, record: dict) -> None:
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, FILTER_KEY, json.dumps(record))


def _parse(raw: str | None) -> dict:
    if not raw:
        return _empty()
    try:
        record = json.loads(raw)
        if record.get("version") != 1:
            raise ValueError
        return record
    except (ValueError, TypeError, AttributeError):
        raise ValidationError(
            "The application server SSH setting could not be read. Ask an administrator to review it."
        ) from None


async def _update(session, change) -> dict:
    """The one way to write the record: lock its row, read it fresh, apply ``change`` and write it back.

    Every writer (draft saves, Check access, filter changes, recovery) changes only its own fields of
    the current stored record, so none can erase another's (for example a committed recovery journal).
    The lock is held until the caller's transaction ends.
    """
    from sqlalchemy import text

    from app.platform.platform_config_service import PlatformConfigService

    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO NOTHING"),
        {"k": FILTER_KEY, "v": PlatformConfigService._maybe_encrypt(FILTER_KEY, json.dumps(_empty()))},
    )
    record = _parse(await PlatformConfigService.get_for_update(session, FILTER_KEY))
    change(record)
    await _write(session, record)
    return record


async def _provider(fn, *args):
    """Run a blocking provider call in a worker thread.

    A thread keeps running after its awaiting task is cancelled, so on cancellation this waits for
    the call to return before re-raising: the infrastructure operation keeps its exclusive slot until
    the cloud work has settled, and a retry cannot race it.
    """
    future = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            if future.done():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


async def network_for(session, identity):
    """The provider network driver for this host, with the backend's configured authority."""
    from app.adapters.host_network.firewall import host_network

    gcp_config = None
    if identity.provider == "gcp":
        from app.platform.platform_config_service import PlatformConfigService

        gcp_config = await PlatformConfigService.get_many(
            session,
            [
                "gcp_project_id",
                "gcp_credential_source",
                "gcp_service_account_key",
                "gcp_service_account_email",
                "gcp_bootstrap_sa_email",
            ],
        )
    return host_network(identity, gcp_config)


# -- Guest diagnostics (fixed read-only report published by the host, plan 03B) ------------------


def request_guest_diagnostics() -> None:
    """Ask the host agent for a fresh fixed-schema report; no command, path or username is passed."""
    from app.config import settings

    name = f"ssh-recovery-diagnostics-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}.json"
    path = os.path.join(settings.update_requests_dir, name)
    try:
        with open(path, "x", encoding="utf-8") as f:
            json.dump({"operation": "ssh-recovery-diagnostics"}, f)
    except OSError:
        pass


def guest_problems(identity) -> list[str]:
    from app.config import settings

    path = os.path.join(settings.update_status_dir, DIAGNOSTICS_FILE)
    stale = "The server's SSH diagnostics are missing or out of date. Check access again in a minute."
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        generated = datetime.fromisoformat(report["generated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return [stale]
    if report.get("schema") != 1 or report.get("instance_id") != identity.instance_id:
        return [stale]
    if datetime.now(timezone.utc) - generated > DIAGNOSTICS_MAX_AGE:
        return [stale]
    problems = []
    if not report.get("sshd_ok"):
        problems.append("The server's SSH configuration did not pass its check.")
    if not report.get("pubkey_auth"):
        problems.append("The server does not accept public key login, which cloud recovery needs.")
    if identity.provider == "gcp" and not report.get("guest_agent"):
        problems.append("The Google guest environment is not running on the server, so browser SSH cannot add its key.")
    if identity.provider == "aws" and not (
        report.get("eic_package") and "eic" in str(report.get("authorized_keys_command", ""))
    ):
        problems.append("EC2 Instance Connect is not installed or not used by SSH on the server.")
    return problems


# -- Preflight ---------------------------------------------------------------------------------------


async def _probe(session) -> dict | None:
    from app.platform.platform_config_service import PlatformConfigService

    raw = await PlatformConfigService.get(session, PROBE_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


RECOVERY_REMOVED = "The cloud recovery rule was removed outside bioAF."


def _recovery_established(record: dict) -> bool:
    """Evidence recovery access existed here: it was seen or created, or a filter was ever applied."""
    return bool(record.get("recovery_seen")) or record["applied"].get("revision", 0) > 0 or bool(record.get("previous"))


def _note_established(record: dict, recovery: dict) -> dict:
    """Record that recovery was seen or created; return the result without the internal flag."""
    if recovery.get("_present"):
        record["recovery_seen"] = True
    return {k: v for k, v in recovery.items() if k != "_present"}


async def preflight(session, *, reconcile: bool) -> dict:
    """Recovery readiness. With ``reconcile`` (Apply), the protected sources are created or repaired."""
    from app.services.ssh_recovery import RecoveryError, current_host_identity

    record = await _read(session)
    now = datetime.now(timezone.utc).isoformat()
    try:
        identity = await current_host_identity(session)
    except RecoveryError:
        return {
            "status": "needs_attention",
            "problems": ["bioAF could not identify the server it runs on."],
            "sources": [],
            "checked_at": now,
        }
    problems: list[str] = []
    sources: list[dict] = []
    missing = False
    present = False
    try:
        driver = await network_for(session, identity)
        # Provider calls can wait minutes; they run off the serving event loop.
        sources = await _provider(driver.ensure_recovery if reconcile else driver.recovery_sources)
        missing = (not reconcile) and bool(await _provider(driver.recovery_missing))
        present = not missing
        problems += await _provider(driver.check_recovery)
    except Exception as exc:
        problems.append(f"bioAF could not read or change this server's firewall ({type(exc).__name__}).")
    if missing and _recovery_established(record):
        # It existed here before: its absence is lost protection, not an installation from before it.
        problems.append(RECOVERY_REMOVED)
        missing = False
    problems += guest_problems(identity)
    status = "needs_attention" if problems else ("not_configured" if missing else "configured_automatic")
    probe = await _probe(session)
    if (
        status == "configured_automatic"
        and probe
        and probe.get("result") == "succeeded"
        and probe.get("instance_id") == identity.instance_id
        and probe.get("filter_revision") == record["applied"]["revision"]
    ):
        status = "login_tested"
    result = {
        "status": status,
        "problems": problems,
        "sources": sources,
        "checked_at": now,
        "instance_id": identity.instance_id,
        # Internal: the recovery resource was seen or created; writers record it (_note_established).
        "_present": present,
    }
    if status == "login_tested":
        result["login_tested_at"] = probe.get("probed_at")
    return result


async def check_access(session) -> dict:
    request_guest_diagnostics()
    recovery = await preflight(session, reconcile=False)

    def change(record: dict) -> None:
        nonlocal recovery
        recovery = _note_established(record, recovery)
        if record.get("pending"):
            recovery = {
                **recovery,
                "status": "needs_attention",
                "problems": [*recovery["problems"], UNFINISHED_CHANGE],
            }
        record["recovery"] = recovery

    await _update(session, change)
    await session.commit()
    return {"recovery": recovery}


# -- The filter lifecycle ----------------------------------------------------------------------------


async def save_draft(session, *, enabled: bool, ranges: list[dict], base_version: int, actor_id: int) -> dict:
    from app.services.audit_service import log_action

    validated: list[dict] = []

    def change(record: dict) -> None:
        nonlocal validated
        draft = record["draft"]
        if base_version != draft["version"]:
            raise ConflictError(
                "These settings were changed by someone else. Reload them, then make your change again."
            )
        validated = _validated_entries(ranges, draft.get("ranges", []) + record["applied"].get("ranges", []))
        if enabled and not validated:
            raise ValidationError("Add at least one address or range, or turn the filter off.")
        record["draft"] = {"version": draft["version"] + 1, "enabled": bool(enabled), "ranges": validated}

    record = await _update(session, change)
    await log_action(
        session,
        user_id=actor_id,
        entity_type="ssh_access_policy",
        entity_id=0,
        action="save_host_ssh_filter_draft",
        details={"enabled": bool(enabled), "ranges": [r["cidr"] for r in validated]},
    )
    return record["draft"]


def _carry_forward(previous: dict | None, resources: dict | None) -> dict | None:
    """Keep what earlier applies revoked (AWS), so turning the filter off still restores it after edits."""
    if not previous or not resources or "revoked" not in resources:
        return resources
    merged = list(previous.get("revoked") or [])
    merged += [rule for rule in resources["revoked"] if rule not in merged]
    return {**resources, "revoked": merged}


async def _restore_unfinished(session, driver, *, actor_id: int) -> None:
    """Put back the access an interrupted change saved before it touched the cloud."""
    from app.services.audit_service import log_action

    pending = (await _read(session)).get("pending")
    if not pending:
        return
    try:
        await _provider(driver.restore, pending["snapshot"])
    except Exception as exc:
        logger.error("Restoring an unfinished SSH filter change failed (%s)", type(exc).__name__)
        raise ConflictError(UNFINISHED_RESTORE_FAILED) from None
    record = await _update(session, lambda r: r.pop("pending", None))
    await log_action(
        session,
        user_id=actor_id,
        entity_type="ssh_access_policy",
        entity_id=0,
        action="restore_unfinished_host_ssh_filter",
        details={"operation_id": pending.get("operation_id"), "revision": record["applied"]["revision"]},
    )
    await session.commit()


async def _apply(
    session, record: dict, *, enabled: bool, ranges: list[dict], actor_id: int, action: str, operation_id: int
) -> dict:
    """Change the cloud rules as the owner of one infrastructure operation.

    The pre-change state is committed (``pending``) before any rule changes, so a crash or a database
    failure mid-change leaves what is needed to put access back; the next change restores it first.
    """
    from app.services.audit_service import log_action
    from app.services.host_ssh_filter import RESTORE_FAILED, FilterApplyError
    from app.services.ssh_recovery import current_host_identity

    identity = await current_host_identity(session)
    driver = await network_for(session, identity)
    await _restore_unfinished(session, driver, actor_id=actor_id)

    recovery = await preflight(session, reconcile=True)

    def note(r: dict) -> None:
        nonlocal recovery
        recovery = _note_established(r, recovery)
        if recovery["status"] == "needs_attention":
            r["recovery"] = recovery

    await _update(session, note)
    if recovery["status"] == "needs_attention":
        await session.commit()
        raise ConflictError(NEEDS_ATTENTION_REFUSAL)
    # Only the operation that holds the exclusive slot changes "applied", so this read stays current.
    applied = (await _read(session))["applied"]
    cidrs = [normalize_range(r["cidr"]) for r in ranges]

    pending = {
        "operation_id": operation_id,
        "snapshot": await _provider(driver.snapshot),
        "enabled": enabled,
        "ranges": cidrs,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    def journal(r: dict) -> None:
        r["recovery"] = recovery
        r["pending"] = pending

    await _update(session, journal)
    await session.commit()
    try:
        if enabled:
            resources = _carry_forward(applied.get("resources"), await _provider(driver.apply_filter, cidrs))
        else:
            await _provider(driver.remove_filter, applied.get("resources"))
            resources = None
    except FilterApplyError as exc:
        # The driver put the previous rules back, unless it says it could not (then keep the journal).
        if str(exc) != RESTORE_FAILED:
            await _update(session, lambda r: r.pop("pending", None))
            await session.commit()
        raise ConflictError(str(exc)) from None
    new_applied = {
        "revision": applied["revision"] + 1,
        "enabled": enabled,
        "ranges": ranges,
        "resources": resources,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "applied_by": actor_id,
        "instance_id": identity.instance_id,
    }

    def settle(r: dict) -> None:
        r.pop("pending", None)
        r["previous"] = applied
        r["applied"] = new_applied

    await _update(session, settle)
    await log_action(
        session,
        user_id=actor_id,
        entity_type="ssh_access_policy",
        entity_id=0,
        action=action,
        details={
            "revision": new_applied["revision"],
            "enabled": enabled,
            "ranges": cidrs,
            "instance_id": identity.instance_id,
            "operation_id": operation_id,
        },
        previous_value={"revision": applied["revision"], "enabled": applied.get("enabled")},
    )
    await session.commit()
    return new_applied


async def _as_operation(
    session,
    *,
    actor_id: int,
    change,
    action: str = "host_ssh_filter",
    done: str = "SSH filter changed.",
    outcome=None,
) -> dict:
    """Run one host SSH change as the single running infrastructure operation (409 while another runs).

    ``outcome(result)`` decides the recorded result; without it a change that returns has succeeded.
    """
    from app.services import infrastructure_operations as ops

    op_id, _ = await ops.accept(action=action, principal=ops.Principal(user_id=actor_id))
    async with ops.owned(op_id):
        # Read the setting again inside the slot: a change that settled meanwhile is seen.
        session.expire_all()
        applied = await change(await _read(session), op_id)
    await ops.finish(op_id, outcome(applied) if outcome else ops.succeeded(done))
    return applied


async def set_up_recovery(session, *, actor_id: int) -> dict:
    """Create or repair protected cloud recovery access without touching the SSH filter."""
    from app.services.audit_service import log_action

    async def change(record: dict, op_id: int) -> dict:
        recovery = await preflight(session, reconcile=True)

        def store(r: dict) -> None:
            nonlocal recovery
            recovery = _note_established(r, recovery)
            r["recovery"] = recovery

        await _update(session, store)
        await log_action(
            session,
            user_id=actor_id,
            entity_type="ssh_access_policy",
            entity_id=0,
            action="set_up_ssh_recovery",
            details={"status": recovery["status"], "operation_id": op_id},
        )
        await session.commit()
        return recovery

    def outcome(recovery: dict):
        from app.services import infrastructure_operations as ops

        if recovery["status"] in ("configured_automatic", "login_tested"):
            return ops.succeeded("Cloud recovery access set up.")
        return ops.failed("Cloud recovery access could not be set up: " + " ".join(recovery["problems"]))

    return await _as_operation(session, actor_id=actor_id, change=change, action="host_recovery_setup", outcome=outcome)


def _check_draft(record: dict, draft_version: int) -> dict:
    draft = record["draft"]
    if draft_version != draft["version"]:
        raise ConflictError("These settings were changed by someone else. Reload them, then apply again.")
    if draft.get("enabled") and not draft.get("ranges"):
        raise ValidationError("Add at least one address or range, or turn the filter off.")
    return draft


async def apply_draft(session, *, draft_version: int, actor_id: int) -> dict:
    _check_draft(await _read(session), draft_version)

    async def change(record: dict, op_id: int) -> dict:
        draft = _check_draft(record, draft_version)
        return await _apply(
            session,
            record,
            enabled=bool(draft.get("enabled")),
            ranges=draft.get("ranges", []),
            actor_id=actor_id,
            action="apply_host_ssh_filter",
            operation_id=op_id,
        )

    return await _as_operation(session, actor_id=actor_id, change=change)


async def rollback(session, *, actor_id: int) -> dict:
    if not (await _read(session)).get("previous"):
        raise ValidationError("There is no earlier applied setting to return to.")

    async def change(record: dict, op_id: int) -> dict:
        previous = record.get("previous")
        if not previous:
            raise ValidationError("There is no earlier applied setting to return to.")
        return await _apply(
            session,
            record,
            enabled=bool(previous.get("enabled")),
            ranges=previous.get("ranges", []),
            actor_id=actor_id,
            action="rollback_host_ssh_filter",
            operation_id=op_id,
        )

    return await _as_operation(session, actor_id=actor_id, change=change)


GIT_TRUST_MESSAGE = (
    "A connected code repository needs a security setting before this update can finish. "
    "Existing work has been left running."
)
GIT_TRUST_PROCEDURE = (
    "Get the server's SSH host keys from its administrator (not from ssh-keyscan), save them in a file, then on "
    'the bioAF server run: ./bioaf import-git-known-hosts <file> "<where the keys came from>"'
)


async def _admin_key(session, identity_id: str | None) -> dict | None:
    from app.platform.platform_config_service import PlatformConfigService

    raw = await PlatformConfigService.get(session, "admin_ssh_key")
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("fingerprint"):
        return None
    return {
        "fingerprint": str(data["fingerprint"]),
        "account": str(data.get("account", "")),
        "current": identity_id is None or data.get("instance_id") in (None, identity_id),
    }


async def _git_trust(session, org_id: int | None) -> dict:
    """Repositories on SSH hosts with no approved key (plan 03C), scoped to the caller's organization."""
    from sqlalchemy import select

    from app.models.github_repo import GitHubRepo
    from app.platform.git_trust import trust_for_repositories

    query = select(GitHubRepo.git_ssh_url)
    if org_id is not None:
        query = query.where(GitHubRepo.organization_id == org_id)
    urls = list((await session.execute(query)).scalars())
    trust = await trust_for_repositories(session, urls)
    hosts = sorted(h if p == 22 else f"{h}:{p}" for h, p in trust.untrusted_hosts)
    return {
        "hosts_needing_keys": hosts,
        "message": GIT_TRUST_MESSAGE if hosts else None,
        "procedure": GIT_TRUST_PROCEDURE if hosts else None,
    }


async def view(session, org_id: int | None = None) -> dict:
    record = await _read(session)
    applied = record["applied"]
    recovery = record.get("recovery") or _empty()["recovery"]
    return {
        "admin_key": await _admin_key(session, recovery.get("instance_id")),
        "git_trust": await _git_trust(session, org_id),
        "filter": {
            "draft": record["draft"],
            "applied": {
                "revision": applied["revision"],
                "enabled": applied["enabled"],
                "ranges": applied["ranges"],
                "applied_at": applied.get("applied_at"),
            },
            "can_roll_back": record.get("previous") is not None,
            "scope": "SSH only. Web and API access are not affected.",
        },
        "recovery": recovery,
    }
