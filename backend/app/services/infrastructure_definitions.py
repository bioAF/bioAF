"""Which infrastructure definitions this release ships, which were reconciled, and the M17 notice.

A definition fingerprint is a deterministic hash of one module's shipped Terraform files (sorted
paths and contents) plus a manifest version that is bumped when bioAF changes how it generates a
module's inputs. Temporary directories, state, plans and generated tfvars are excluded. Hashing
reads local files only: nothing here runs Terraform or calls a cloud.

Only modules that apply to this installation are compared: its cloud, and the modules it has
deployed (the same set an infrastructure update checks). A definition difference means a check
may be needed; it does not prove Terraform would change anything.

A module's baseline advances only when an operation fully reconciled it (a deploy of the module,
or a full infrastructure update), with the fingerprint captured before that operation ran. A
targeted apply never advances it. When only delete/replace changes remain after an update, the
module counts as reconciled and is marked ``destructive_pending``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.infrastructure_definition import (
    InfrastructureDefinitionBaseline,
    InfrastructureDefinitionNotice,
    InfrastructureNoticeAcknowledgment,
)

logger = logging.getLogger("bioaf.infrastructure_definitions")

# Bump when the way bioAF generates a module's inputs changes meaning (tfvars contract).
MANIFEST_VERSION = "1"
_EXCLUDED_DIRS = {".terraform"}
_EXCLUDED_SUFFIXES = (".tfstate", ".tfstate.backup", ".tfvars", ".tfvars.json", ".tfplan")
_EXCLUDED_NAMES = {"tfplan", "terraform.tfvars.json"}
# The modules an infrastructure update reconciles (infra_update_service._CANDIDATE_MODULES).
CANDIDATE_MODULES = ("storage", "image_build", "compute")

_REPO_TERRAFORM = Path(__file__).resolve().parents[2] / "terraform"


def modules_root(cloud: str) -> Path:
    """Where this release's module definitions are: the deployed layout, else the repository's."""
    from app.services.terraform_cloud import get_terraform_cloud

    deployed = get_terraform_cloud(cloud).modules_dir()
    if deployed.is_dir():
        return deployed
    return _REPO_TERRAFORM / "aws" / "modules" if cloud == "aws" else _REPO_TERRAFORM / "modules"


def fingerprint_module(path: Path) -> str | None:
    """A deterministic fingerprint of one module directory, or None when it does not exist."""
    if not path.is_dir():
        return None
    digest = hashlib.sha256(f"bioaf-infrastructure-definitions:{MANIFEST_VERSION}\n".encode())
    files = []
    for file in path.rglob("*"):
        rel = file.relative_to(path)
        if not file.is_file() or any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        if file.name in _EXCLUDED_NAMES or file.name.endswith(_EXCLUDED_SUFFIXES):
            continue
        files.append((rel.as_posix(), file))
    for rel_path, file in sorted(files):
        digest.update(rel_path.encode() + b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def manifest(cloud: str, modules: tuple[str, ...] | list[str] = CANDIDATE_MODULES) -> dict[str, str]:
    root = modules_root(cloud)
    out = {}
    for module in modules:
        fp = fingerprint_module(root / module)
        if fp is not None:
            out[module] = fp
    return out


async def _cloud(session: AsyncSession) -> str:
    from app.platform.cloud_provider import get_cloud_provider

    return "aws" if await get_cloud_provider(session) == "aws" else "gcp"


async def applicable_modules(session: AsyncSession) -> list[str]:
    """Deployed modules of this installation's cloud (the modules an update would check)."""
    from app.platform.platform_config_service import PlatformConfigService

    flags = {"storage": "storage_deployed", "image_build": "aws_codebuild_project", "compute": "compute_deployed"}
    values = await PlatformConfigService.get_many(session, list(flags.values()))
    return [m for m, flag in flags.items() if values.get(flag) not in (None, "", "null", "false")]


async def capture(session: AsyncSession) -> dict:
    """The definitions an operation is about to apply, captured before it runs."""
    cloud = await _cloud(session)
    return {"cloud": cloud, "modules": manifest(cloud)}


async def record_reconciled(
    session: AsyncSession,
    captured: dict,
    module: str,
    *,
    destructive_pending: bool = False,
    operation_id: int | None = None,
) -> None:
    """Advance ``module``'s baseline to the fingerprint captured before the operation ran."""
    fp = (captured.get("modules") or {}).get(module)
    if not fp:
        return
    values = {
        "cloud": captured["cloud"],
        "module": module,
        "fingerprint": fp,
        "destructive_pending": destructive_pending,
        "operation_id": operation_id,
        "recorded_at": datetime.now(timezone.utc),
    }
    stmt = insert(InfrastructureDefinitionBaseline).values(**values)
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_infrastructure_definition_baselines_module",
            set_={k: stmt.excluded[k] for k in ("fingerprint", "destructive_pending", "operation_id", "recorded_at")},
        )
    )
    await session.flush()


async def forget(session: AsyncSession, module: str) -> None:
    """A torn-down module has nothing reconciled; its next deploy records a baseline."""
    cloud = await _cloud(session)
    await session.execute(
        delete(InfrastructureDefinitionBaseline).where(
            InfrastructureDefinitionBaseline.cloud == cloud, InfrastructureDefinitionBaseline.module == module
        )
    )
    await session.flush()


async def _pending(session: AsyncSession) -> tuple[list[str], bool, list[str], dict[str, str]]:
    """(pending modules, unknown baseline, delete/replace-only modules, current fingerprints)."""
    cloud = await _cloud(session)
    modules = await applicable_modules(session)
    current = manifest(cloud, modules)
    baselines = {
        b.module: b
        for b in (
            await session.execute(
                select(InfrastructureDefinitionBaseline).where(InfrastructureDefinitionBaseline.cloud == cloud)
            )
        ).scalars()
    }
    pending, unknown, destructive_only = [], False, []
    for module, fp in current.items():
        baseline = baselines.get(module)
        if baseline is None:
            pending.append(module)
            unknown = True
        elif baseline.fingerprint != fp:
            pending.append(module)
        elif baseline.destructive_pending:
            destructive_only.append(module)
    return pending, unknown, destructive_only, current


def _revision(pending: list[str], unknown: bool, current: dict[str, str]) -> str:
    canonical = json.dumps(
        {"unknown": unknown, "modules": {m: current[m] for m in sorted(pending)}}, sort_keys=True
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


async def active_notice(session: AsyncSession) -> InfrastructureDefinitionNotice | None:
    return (
        await session.execute(
            select(InfrastructureDefinitionNotice)
            .where(InfrastructureDefinitionNotice.resolved_at.is_(None))
            .order_by(InfrastructureDefinitionNotice.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def reconcile_notice(session: AsyncSession, release_version: str) -> InfrastructureDefinitionNotice | None:
    """Keep the notice in step with what is pending. Idempotent; the caller commits.

    The same pending set keeps its notice (a repeated finalization or an application-only release
    creates nothing new); a different pending set replaces it; nothing pending resolves it.
    """
    pending, unknown, _destructive_only, current = await _pending(session)
    notice = await active_notice(session)
    now = datetime.now(timezone.utc)
    if not pending:
        if notice is not None:
            notice.resolved_at = now
            await session.flush()
        return None
    revision = _revision(pending, unknown, current)
    if notice is not None and notice.revision == revision:
        return notice
    if notice is not None:
        notice.resolved_at = now
    notice = InfrastructureDefinitionNotice(
        revision=revision,
        release_version=release_version,
        pending_modules=sorted(pending),
        unknown_baseline=unknown,
    )
    session.add(notice)
    await session.flush()
    logger.info("Infrastructure definition notice recorded for %s (%d module(s))", release_version, len(pending))
    return notice


async def notice_for(session: AsyncSession, user_id: int) -> dict:
    """What one admin sees: the pending notice, whether they acknowledged it, delete/replace-only modules."""
    notice = await active_notice(session)
    _pending_modules, _unknown, destructive_only, _current = await _pending(session)
    if notice is None:
        return {"pending": False, "notice": None, "seen": False, "destructive_only_modules": destructive_only}
    seen = (
        await session.execute(
            select(InfrastructureNoticeAcknowledgment.id).where(
                InfrastructureNoticeAcknowledgment.notice_id == notice.id,
                InfrastructureNoticeAcknowledgment.user_id == user_id,
            )
        )
    ).first() is not None
    return {
        "pending": True,
        "notice": {
            "id": notice.id,
            "version": notice.release_version,
            "unknown_baseline": notice.unknown_baseline,
            "modules": list(notice.pending_modules or []),
        },
        "seen": seen,
        "destructive_only_modules": destructive_only,
    }


async def acknowledge(session: AsyncSession, notice_id: int, user_id: int) -> None:
    """Record that this admin chose Later for this notice. Marks nothing applied. The caller commits."""
    stmt = insert(InfrastructureNoticeAcknowledgment).values(notice_id=notice_id, user_id=user_id)
    await session.execute(stmt.on_conflict_do_nothing(constraint="uq_infrastructure_notice_ack_user"))
    await session.flush()


async def refresh_notice(session: AsyncSession) -> None:
    """Bring the notice in step after an operation changed what is reconciled. Never raises."""
    from app.config import settings

    try:
        async with session.begin_nested():
            await reconcile_notice(session, settings.app_version)
    except Exception as exc:
        # Notice bookkeeping never changes an infrastructure outcome; startup reconciles again.
        logger.warning("Infrastructure notice was not refreshed (error_type=%s)", type(exc).__name__)
