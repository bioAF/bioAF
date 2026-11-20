"""Read-only assessment of environment-cascade versions that lost their QC definition.

Before the fix, a pipeline version created by an environment rebuild
(``version_trigger='environment_cascade'``) was stored without ``qc_template``,
``qc_config_json`` or each variable's ``reference_category``. This command finds
those versions and, for each one, follows the ``cascade_create`` audit's
``source_version_id`` back to the nearest user-created version. It then reports
which of the three fields differ, which runs used the version, and which QC
dashboards those runs have. It never writes: it runs in a READ ONLY transaction.

Provenance is taken only from audits. A link is followed only when it stays in
the same pipeline and organization; anything else is reported as unresolved with
a reason code, never guessed from version numbers or timestamps.

The report names ids, field names, counts and reason codes only. It never prints
QC settings, template names or other scientific content.

Usage:
    python -m app.cli.assess_cascade_qc [--org-id 1] [--after-id 0] [--limit 200]
        [--max-depth 20] [--max-runs 50]

Re-run with ``--after-id`` set to the printed continuation id until none is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.models.audit_log import AuditLog
from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.qc_dashboard import QCDashboard
from app.services.qc.resolver import build_config_for_template

CASCADE_TRIGGER = "environment_cascade"
MAX_LIMIT = 1000
MAX_DEPTH = 100
MAX_RUNS = 1000


@dataclass
class DashboardFinding:
    dashboard_id: int
    status: str
    # A nonempty snapshot is what dashboard reads prefer; an empty one is re-resolved
    # from the version on every read.
    has_snapshot: bool


@dataclass
class RunFinding:
    run_id: int
    status: str
    live_output_files: int
    deleted_output_files: int
    dashboards: list[DashboardFinding]


@dataclass
class VersionFinding:
    version_id: int
    custom_pipeline_id: int
    organization_id: int
    version_number: int
    # affected: fields were lost; unaffected: nothing differs; conflicting: values
    # differ in a way the old cascade could not cause; unresolved: provenance broken.
    outcome: str
    chain: list[int]
    root_version_id: int | None = None
    lost_fields: list[str] = field(default_factory=list)
    qc_render_changed: bool = False
    reasons: list[str] = field(default_factory=list)
    runs: list[RunFinding] = field(default_factory=list)
    runs_truncated: bool = False


@dataclass
class Assessment:
    org_id: int | None
    after_id: int
    limit: int
    max_depth: int
    max_runs: int
    findings: list[VersionFinding]
    # Pass as --after-id to continue; None when this page reached the end.
    next_after_id: int | None


class _Unresolved(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _check_bound(name: str, value: int, maximum: int) -> None:
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")


async def assess_cascade_versions(
    session: AsyncSession,
    *,
    org_id: int | None = None,
    after_id: int = 0,
    limit: int = 200,
    max_depth: int = 20,
    max_runs: int = 50,
) -> Assessment:
    """Assess up to ``limit`` cascade versions with id greater than ``after_id``."""
    _check_bound("limit", limit, MAX_LIMIT)
    _check_bound("max_depth", max_depth, MAX_DEPTH)
    _check_bound("max_runs", max_runs, MAX_RUNS)

    query = (
        select(CustomPipelineVersion, CustomPipeline.organization_id)
        .join(CustomPipeline, CustomPipelineVersion.custom_pipeline_id == CustomPipeline.id)
        .where(CustomPipelineVersion.version_trigger == CASCADE_TRIGGER, CustomPipelineVersion.id > after_id)
        .order_by(CustomPipelineVersion.id)
        .limit(limit)
    )
    if org_id is not None:
        query = query.where(CustomPipeline.organization_id == org_id)
    candidates = list((await session.execute(query)).all())

    findings = []
    for version, version_org_id in candidates:
        findings.append(await _assess_one(session, version, version_org_id, max_depth, max_runs))

    next_after_id = candidates[-1][0].id if len(candidates) == limit else None
    return Assessment(org_id, after_id, limit, max_depth, max_runs, findings, next_after_id)


async def _assess_one(
    session: AsyncSession, candidate: CustomPipelineVersion, org_id: int, max_depth: int, max_runs: int
) -> VersionFinding:
    finding = VersionFinding(
        version_id=candidate.id,
        custom_pipeline_id=candidate.custom_pipeline_id,
        organization_id=org_id,
        version_number=candidate.version_number,
        outcome="unresolved",
        chain=[candidate.id],
    )
    try:
        chain = await _walk_to_root(session, candidate, org_id, max_depth)
    except _Unresolved as unresolved:
        finding.reasons = [unresolved.reason]
    else:
        finding.chain = [v.id for v in chain]
        finding.root_version_id = chain[-1].id
        _compare(finding, chain)

    if finding.outcome != "unaffected":
        finding.runs, finding.runs_truncated = await _runs_for(session, candidate.id, org_id, max_runs)
    return finding


async def _load_version(session: AsyncSession, version_id: int) -> CustomPipelineVersion | None:
    result = await session.execute(
        select(CustomPipelineVersion)
        .where(CustomPipelineVersion.id == version_id)
        .options(selectinload(CustomPipelineVersion.variables))
    )
    return result.scalar_one_or_none()


async def _environment_org(session: AsyncSession, environment_version_id: int) -> int | None:
    result = await session.execute(
        select(Environment.organization_id)
        .join(EnvironmentVersion, EnvironmentVersion.environment_id == Environment.id)
        .where(EnvironmentVersion.id == environment_version_id)
    )
    return result.scalar_one_or_none()


async def _audited_source(session: AsyncSession, version: CustomPipelineVersion) -> int:
    result = await session.execute(
        select(AuditLog.details_json).where(
            AuditLog.entity_type == "custom_pipeline_version",
            AuditLog.entity_id == version.id,
            AuditLog.action == "cascade_create",
        )
    )
    details = [d for (d,) in result.all()]
    if not details:
        raise _Unresolved("missing_audit")
    sources = {d.get("source_version_id") if isinstance(d, dict) else None for d in details}
    if len(sources) > 1:
        raise _Unresolved("conflicting_audit")
    (source_id,) = sources
    if not isinstance(source_id, int) or isinstance(source_id, bool):
        raise _Unresolved("malformed_audit")
    env_ids = {d.get("environment_version_id") for d in details}
    if env_ids != {version.environment_version_id}:
        raise _Unresolved("audit_environment_mismatch")
    return source_id


async def _walk_to_root(
    session: AsyncSession, candidate: CustomPipelineVersion, org_id: int, max_depth: int
) -> list[CustomPipelineVersion]:
    """Return [candidate, ..., nearest non-cascade ancestor], or raise _Unresolved."""
    if await _environment_org(session, candidate.environment_version_id) != org_id:
        raise _Unresolved("organization_mismatch")
    loaded = await _load_version(session, candidate.id)
    assert loaded is not None
    chain = [loaded]
    seen = {loaded.id}
    current = loaded
    for _ in range(max_depth):
        source_id = await _audited_source(session, current)
        if source_id in seen:
            raise _Unresolved("cycle")
        source = await _load_version(session, source_id)
        if source is None:
            raise _Unresolved("missing_ancestor")
        if source.custom_pipeline_id != current.custom_pipeline_id:
            raise _Unresolved("pipeline_mismatch")
        if await _environment_org(session, source.environment_version_id) != org_id:
            raise _Unresolved("organization_mismatch")
        if source.version_number >= current.version_number:
            raise _Unresolved("version_order")
        chain.append(source)
        seen.add(source.id)
        if source.version_trigger != CASCADE_TRIGGER:
            return chain
        current = source
    raise _Unresolved("depth_exceeded")


def _categories(version: CustomPipelineVersion) -> dict[str, str | None] | None:
    """Variable name -> reference_category, or None when a name is ambiguous."""
    names = [v.variable_name for v in version.variables]
    if len(names) != len(set(names)):
        return None
    return {v.variable_name: v.reference_category for v in version.variables}


def _effective_qc(version: CustomPipelineVersion) -> tuple[str, dict[str, Any]]:
    template = version.qc_template or "custom"
    return template, build_config_for_template(template, version.qc_config_json)


def _compare(finding: VersionFinding, chain: list[CustomPipelineVersion]) -> None:
    candidate, root, intermediates = chain[0], chain[-1], chain[1:-1]
    reasons: list[str] = []
    lost: list[str] = []

    for name in ("qc_template", "qc_config_json"):
        root_value = getattr(root, name)
        value = getattr(candidate, name)
        if value == root_value:
            continue
        if value is None:
            lost.append(name)
        else:
            reasons.append(f"value_differs:{name}")
        for step in intermediates:
            step_value = getattr(step, name)
            if step_value is not None and step_value != root_value:
                reasons.append(f"intermediate_differs:{name}")
                break

    root_categories = _categories(root)
    candidate_categories = _categories(candidate)
    if root_categories is None or candidate_categories is None:
        finding.outcome = "unresolved"
        finding.reasons = ["ambiguous_variable_name"]
        return
    if set(root_categories) != set(candidate_categories):
        reasons.append("variable_set_mismatch")
    for name in sorted(set(root_categories) & set(candidate_categories)):
        root_value = root_categories[name]
        value = candidate_categories[name]
        if value == root_value:
            continue
        if value is None:
            lost.append(f"reference_category:{name}")
        else:
            reasons.append(f"value_differs:reference_category:{name}")
        for step in intermediates:
            step_value = (_categories(step) or {}).get(name)
            if step_value is not None and step_value != root_value:
                reasons.append(f"intermediate_differs:reference_category:{name}")
                break

    finding.lost_fields = lost
    finding.qc_render_changed = _effective_qc(candidate) != _effective_qc(root)
    finding.reasons = reasons
    if reasons:
        finding.outcome = "conflicting"
    elif lost:
        finding.outcome = "affected"
    else:
        finding.outcome = "unaffected"


async def _runs_for(
    session: AsyncSession, version_id: int, org_id: int, max_runs: int
) -> tuple[list[RunFinding], bool]:
    result = await session.execute(
        select(PipelineRun.id, PipelineRun.status)
        .where(PipelineRun.custom_pipeline_version_id == version_id, PipelineRun.organization_id == org_id)
        .order_by(PipelineRun.id)
        .limit(max_runs + 1)
    )
    rows = list(result.all())
    truncated = len(rows) > max_runs
    rows = rows[:max_runs]
    if not rows:
        return [], truncated
    run_ids = [run_id for run_id, _ in rows]

    file_counts: dict[tuple[int, bool], int] = {}
    files = await session.execute(
        select(File.source_pipeline_run_id, File.deleted_at.is_not(None), func.count())
        .where(File.source_pipeline_run_id.in_(run_ids), File.organization_id == org_id)
        .group_by(File.source_pipeline_run_id, File.deleted_at.is_not(None))
    )
    for file_run_id, deleted, count in files.all():
        if file_run_id is not None:
            file_counts[(file_run_id, bool(deleted))] = count

    dashboards: dict[int, list[DashboardFinding]] = {run_id: [] for run_id in run_ids}
    dash_rows = await session.execute(
        select(QCDashboard.id, QCDashboard.pipeline_run_id, QCDashboard.status, QCDashboard.qc_config_json)
        .where(QCDashboard.pipeline_run_id.in_(run_ids), QCDashboard.organization_id == org_id)
        .order_by(QCDashboard.id)
    )
    for row in dash_rows.all():
        dashboards[row.pipeline_run_id].append(DashboardFinding(row.id, row.status, bool(row.qc_config_json)))

    runs = [
        RunFinding(
            run_id=run_id,
            status=status,
            live_output_files=file_counts.get((run_id, False), 0),
            deleted_output_files=file_counts.get((run_id, True), 0),
            dashboards=dashboards[run_id],
        )
        for run_id, status in rows
    ]
    return runs, truncated


def format_report(assessment: Assessment) -> str:
    scope = f"organization {assessment.org_id}" if assessment.org_id is not None else "all organizations"
    counts: dict[str, int] = {}
    for f in assessment.findings:
        counts[f.outcome] = counts.get(f.outcome, 0) + 1
    lines = [
        f"Cascade QC assessment ({scope}): {len(assessment.findings)} cascade version(s) with id > "
        f"{assessment.after_id} (limit {assessment.limit}, max depth {assessment.max_depth}, "
        f"max runs per version {assessment.max_runs}).",
        "Outcomes: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"),
    ]
    for f in assessment.findings:
        head = (
            f"- version {f.version_id} (pipeline {f.custom_pipeline_id}, org {f.organization_id}, "
            f"v{f.version_number}): {f.outcome}"
        )
        if f.root_version_id is not None:
            head += f"; root version {f.root_version_id}; chain {' -> '.join(str(i) for i in f.chain)}"
        lines.append(head)
        if f.lost_fields:
            lines.append(
                f"    lost: {', '.join(f.lost_fields)}; QC render changed: {'yes' if f.qc_render_changed else 'no'}"
            )
        if f.reasons:
            lines.append(f"    reasons: {', '.join(f.reasons)}")
        for r in f.runs:
            dash = (
                ", ".join(
                    f"dashboard {d.dashboard_id} ({d.status}, {'snapshot' if d.has_snapshot else 'no snapshot'})"
                    for d in r.dashboards
                )
                or "no dashboard"
            )
            lines.append(
                f"    run {r.run_id} ({r.status}): output files live={r.live_output_files} "
                f"deleted={r.deleted_output_files}; {dash}"
            )
        if f.runs_truncated:
            lines.append(f"    more runs exist beyond {assessment.max_runs}; rerun with a higher --max-runs")
    if assessment.next_after_id is not None:
        lines.append(f"More cascade versions remain: rerun with --after-id {assessment.next_after_id}")
    else:
        lines.append("End of cascade versions in scope.")
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> None:
    database_url = os.environ.get("BIOAF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: Set BIOAF_DATABASE_URL or DATABASE_URL environment variable.")
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            try:
                assessment = await assess_cascade_versions(
                    session,
                    org_id=args.org_id,
                    after_id=args.after_id,
                    limit=args.limit,
                    max_depth=args.max_depth,
                    max_runs=args.max_runs,
                )
            finally:
                await session.rollback()
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()
    print(format_report(assessment))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only report of environment-cascade pipeline versions that lost their QC definition"
    )
    parser.add_argument("--org-id", type=int, default=None, help="Only this organization (default: all)")
    parser.add_argument("--after-id", type=int, default=0, help="Continue after this version id")
    parser.add_argument("--limit", type=int, default=200, help=f"Versions per page (1-{MAX_LIMIT})")
    parser.add_argument("--max-depth", type=int, default=20, help=f"Provenance hops per version (1-{MAX_DEPTH})")
    parser.add_argument("--max-runs", type=int, default=50, help=f"Runs listed per version (1-{MAX_RUNS})")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
