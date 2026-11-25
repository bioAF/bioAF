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

import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from app.exceptions import ConflictError, ValidationError
from app.services.ssh_access_policy import _validated_entries, normalize_range

FILTER_KEY = "app_host_ssh_filter"
PROBE_KEY = "ssh_recovery_probe"
DIAGNOSTICS_FILE = "ssh-recovery-diagnostics.json"
DIAGNOSTICS_MAX_AGE = timedelta(hours=1)
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

    raw = await PlatformConfigService.get(session, FILTER_KEY)
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


async def _write(session, record: dict) -> None:
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, FILTER_KEY, json.dumps(record))


async def network_for(session, identity):
    """The provider network driver for this host, with the backend's configured authority."""
    from app.services.host_ssh_filter import AwsHostNetwork, GcpHostNetwork

    if identity.provider == "gcp":
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
        client = compute_v1.FirewallsClient(credentials=load_gcp_credentials(cfg))
        return GcpHostNetwork(identity, client=client, target_tag="bioaf")
    import boto3

    return AwsHostNetwork(identity, client=boto3.client("ec2", region_name=identity.region))


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
    try:
        driver = await network_for(session, identity)
        sources = driver.ensure_recovery() if reconcile else driver.recovery_sources()
        problems += driver.check_recovery()
    except Exception as exc:
        problems.append(f"bioAF could not read or change this server's firewall ({type(exc).__name__}).")
    problems += guest_problems(identity)
    status = "needs_attention" if problems else "configured_automatic"
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
    }
    if status == "login_tested":
        result["login_tested_at"] = probe.get("probed_at")
    return result


async def check_access(session) -> dict:
    request_guest_diagnostics()
    recovery = await preflight(session, reconcile=False)
    record = await _read(session)
    record["recovery"] = recovery
    await _write(session, record)
    await session.commit()
    return {"recovery": recovery}


# -- The filter lifecycle ----------------------------------------------------------------------------


async def save_draft(session, *, enabled: bool, ranges: list[dict], base_version: int, actor_id: int) -> dict:
    from app.services.audit_service import log_action

    record = await _read(session)
    draft = record["draft"]
    if base_version != draft["version"]:
        raise ConflictError("These settings were changed by someone else. Reload them, then make your change again.")
    validated = _validated_entries(ranges, draft.get("ranges", []) + record["applied"].get("ranges", []))
    if enabled and not validated:
        raise ValidationError("Add at least one address or range, or turn the filter off.")
    record["draft"] = {"version": draft["version"] + 1, "enabled": bool(enabled), "ranges": validated}
    await _write(session, record)
    await log_action(
        session,
        user_id=actor_id,
        entity_type="ssh_access_policy",
        entity_id=0,
        action="save_host_ssh_filter_draft",
        details={"enabled": bool(enabled), "ranges": [r["cidr"] for r in validated]},
    )
    return record["draft"]


async def _apply(session, record: dict, *, enabled: bool, ranges: list[dict], actor_id: int, action: str) -> dict:
    from app.services.audit_service import log_action
    from app.services.host_ssh_filter import FilterApplyError
    from app.services.ssh_recovery import current_host_identity

    recovery = await preflight(session, reconcile=True)
    record["recovery"] = recovery
    if recovery["status"] == "needs_attention":
        await _write(session, record)
        await session.commit()
        raise ConflictError(NEEDS_ATTENTION_REFUSAL)
    identity = await current_host_identity(session)
    driver = await network_for(session, identity)
    applied = record["applied"]
    cidrs = [normalize_range(r["cidr"]) for r in ranges]
    try:
        if enabled:
            resources = driver.apply_filter(cidrs)
        else:
            driver.remove_filter(applied.get("resources"))
            resources = None
    except FilterApplyError as exc:
        await _write(session, record)
        await session.commit()
        raise ConflictError(str(exc)) from None
    record["previous"] = applied
    record["applied"] = {
        "revision": applied["revision"] + 1,
        "enabled": enabled,
        "ranges": ranges,
        "resources": resources,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "applied_by": actor_id,
        "instance_id": identity.instance_id,
    }
    await _write(session, record)
    await log_action(
        session,
        user_id=actor_id,
        entity_type="ssh_access_policy",
        entity_id=0,
        action=action,
        details={
            "revision": record["applied"]["revision"],
            "enabled": enabled,
            "ranges": cidrs,
            "instance_id": identity.instance_id,
        },
        previous_value={"revision": applied["revision"], "enabled": applied.get("enabled")},
    )
    await session.commit()
    return record["applied"]


async def apply_draft(session, *, draft_version: int, actor_id: int) -> dict:
    record = await _read(session)
    draft = record["draft"]
    if draft_version != draft["version"]:
        raise ConflictError("These settings were changed by someone else. Reload them, then apply again.")
    if draft.get("enabled") and not draft.get("ranges"):
        raise ValidationError("Add at least one address or range, or turn the filter off.")
    return await _apply(
        session,
        record,
        enabled=bool(draft.get("enabled")),
        ranges=draft.get("ranges", []),
        actor_id=actor_id,
        action="apply_host_ssh_filter",
    )


async def rollback(session, *, actor_id: int) -> dict:
    record = await _read(session)
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
    )


async def view(session) -> dict:
    record = await _read(session)
    applied = record["applied"]
    return {
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
        "recovery": record.get("recovery") or _empty()["recovery"],
    }
