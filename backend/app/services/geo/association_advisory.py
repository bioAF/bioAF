"""Post-startup advisory: which GEO downloads legacy unlinked files will block.

An owned background task of the started backend (app/main.py), never part of the upgrade lifecycle, so it
cannot delay the entrypoint or the API. It waits a short delay after startup, then runs once per schema
revision and policy revision; a failed or interrupted run is retried on the next start, a completed one
is not repeated. It never fails anything else: a timeout, query error or report-write failure only marks
this advisory, and runtime GEO readiness and download recompute findings regardless.

Same detector as readiness and download (app/services/geo/file_associations.py), over every Organization's
export candidates (the Organization's files under ``/experiments/{id}/``, not ``files.experiment_id``),
under both QC filters. Prefix matches are diagnostic only; nothing is written to the database.

Bounds:
- time: one deadline (``geo_association_advisory_timeout_seconds``) for the whole scan and report, and the
  same value as a per-statement ``statement_timeout`` in a read-only transaction;
- memory: one Experiment's unlinked candidates and Samples at a time; findings stream to the report.

Outputs:
- bounded counts in the upgrade record (``geo_association_advisory``), written only beside the completed
  upgrade to this code's head (app.schema_upgrade.update_advisory): no filenames, URIs or metadata;
- the detail as JSON Lines in an operator-private report beside the recovery backups (0600 in the 0700
  backups directory, owned like it), which is never served.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import IO, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.services.geo.file_associations import (
    POLICY_REVISION,
    RAW_FILE_TYPES,
    detect_findings,
    pair_fingerprint,
)
from app.services.geo.qc_filter import GeoQcFilter

logger = logging.getLogger("bioaf.geo.association_advisory")

RECORD_KEY = "geo_association_advisory"

# Unlinked candidates joined to their Experiment exactly as the export selects them (Organization and URI).
_CANDIDATES = (
    "FROM experiments e JOIN files f ON f.organization_id = e.organization_id "
    "AND strpos(f.storage_uri, '/experiments/' || e.id || '/') > 0 "
    "WHERE NOT EXISTS (SELECT 1 FROM sample_files sf WHERE sf.file_id = f.id)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_current(advisory: dict | None, revision: str) -> bool:
    """A completed scan for this schema revision and policy needs no retry."""
    return bool(
        advisory
        and advisory.get("status") == "completed"
        and advisory.get("policy_revision") == POLICY_REVISION
        and advisory.get("revision") == revision
    )


def _empty_counts() -> dict[str, dict[str, int]]:
    return {
        f.value: {"experiments": 0, "files": 0, "sample_pairs": 0, "raw_files": 0, "processed_files": 0}
        for f in GeoQcFilter
    }


async def _scan_experiment(conn: AsyncConnection, org_id: int, exp_id: int, counts: dict, report: IO[str]) -> int:
    """One Experiment's unresolved findings, streamed to ``report``; returns its unlinked candidate count."""
    unlinked = [
        SimpleNamespace(**row._mapping)
        for row in await conn.execute(
            text(f"SELECT f.id, f.filename, f.storage_uri, f.file_type {_CANDIDATES} AND e.id = :e ORDER BY f.id"),
            {"e": exp_id},
        )
    ]
    samples = [
        SimpleNamespace(**row._mapping)
        for row in await conn.execute(
            text("SELECT id, experiment_id, external_id, qc_status FROM samples WHERE experiment_id = :e ORDER BY id"),
            {"e": exp_id},
        )
    ]
    acks = {
        (row.file_id, row.sample_id): row.fingerprint
        for row in await conn.execute(
            text(
                "SELECT file_id, sample_id, fingerprint FROM geo_file_association_acknowledgments "
                "WHERE experiment_id = :e AND policy_revision = :rev"
            ),
            {"e": exp_id, "rev": POLICY_REVISION},
        )
    }
    for qc_filter in GeoQcFilter:
        selected = [s for s in samples if qc_filter is GeoQcFilter.INCLUDE_ALL or s.qc_status != "fail"]
        tally = counts[qc_filter.value]
        unresolved = 0
        for f, matched in detect_findings(unlinked, selected):
            pending = [s for s in matched if acks.get((f.id, s.id)) != pair_fingerprint(f, set(), s)]
            if not pending:
                continue
            kind = "raw" if f.file_type in RAW_FILE_TYPES else "processed"
            line = {
                "organization_id": org_id,
                "experiment_id": exp_id,
                "qc_status_filter": qc_filter.value,
                "file_id": f.id,
                "filename": f.filename,
                "file_kind": kind,
                "samples": [
                    {"sample_id": s.id, "external_id": s.external_id, "qc_status": s.qc_status} for s in pending
                ],
            }
            report.write(json.dumps(line) + "\n")
            tally["files"] += 1
            tally["sample_pairs"] += len(pending)
            tally[f"{kind}_files"] += 1
            unresolved += 1
        if unresolved:
            tally["experiments"] += 1
    return len(unlinked)


def _open_report(backups: Path, revision: str) -> tuple[IO[str], Path]:
    from app.schema_upgrade import _give_to, secure_backups_dir

    owner = secure_backups_dir(backups)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = backups / f"bioaf_geo_association_advisory_{revision}_{stamp}.jsonl"
    attempt = 2
    while path.exists():
        path = backups / f"bioaf_geo_association_advisory_{revision}_{stamp}_{attempt}.jsonl"
        attempt += 1
    handle = os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")
    _give_to(path, owner)
    return handle, path


async def _scan_and_report(
    engine: AsyncEngine, revision: str, backups: Path, timeout: float, step: dict[str, str]
) -> dict[str, Any]:
    step["name"] = "report"
    report, path = _open_report(backups, revision)
    try:
        header = {
            "policy_revision": POLICY_REVISION,
            "revision": revision,
            "started_at": _now(),
            "note": (
                "One line per unlinked file whose name matches selected Samples by the pre-link prefix rule. "
                "Diagnostic only: a match is not ownership. Resolve each in the Experiment's GEO readiness "
                "check (link the file to its Sample, or keep it at experiment level)."
            ),
        }
        report.write(json.dumps(header) + "\n")
        step["name"] = "scan"
        counts = _empty_counts()
        unlinked_total = 0
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(
                text("SELECT set_config('statement_timeout', :ms, true)"), {"ms": str(max(1, int(timeout * 1000)))}
            )
            organizations = (await conn.execute(text("SELECT count(*) FROM organizations"))).scalar_one()
            experiments = (
                await conn.execute(
                    text(f"SELECT DISTINCT e.organization_id, e.id {_CANDIDATES} ORDER BY e.organization_id, e.id")
                )
            ).all()
            for org_id, exp_id in experiments:
                unlinked_total += await _scan_experiment(conn, org_id, exp_id, counts, report)
            await conn.rollback()
        step["name"] = "report"
        report.flush()
        os.fsync(report.fileno())
    finally:
        report.close()
    return {
        "organizations_scanned": organizations,
        "experiments_with_unlinked_candidates": len(experiments),
        "unlinked_candidate_files": unlinked_total,
        "unresolved": counts,
        "report": path.name,
    }


async def run_once(engine: AsyncEngine) -> None:
    """Scan, report, record, if this revision's upgrade completed and its advisory has not. Never raises."""
    from app.config import settings
    from app.schema_upgrade import code_head, read_status, update_advisory

    revision = code_head()
    record = read_status()
    if record.get("status") != "completed" or record.get("target_revision") != revision:
        return
    if is_current(record.get(RECORD_KEY), revision):
        return

    advisory: dict[str, Any] = {
        "status": "running",
        "policy_revision": POLICY_REVISION,
        "revision": revision,
        "started_at": _now(),
    }
    step = {"name": "record"}
    timeout = float(settings.geo_association_advisory_timeout_seconds)
    try:
        if not update_advisory(RECORD_KEY, advisory, revision):
            return
        summary = await asyncio.wait_for(
            _scan_and_report(engine, revision, Path(settings.upgrade_backups_dir), timeout, step), timeout
        )
        advisory = {**advisory, **summary, "status": "completed", "completed_at": _now()}
        update_advisory(RECORD_KEY, advisory, revision)
    except Exception as exc:
        error = "TimeoutError" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else type(exc).__name__
        logger.warning("GEO file association advisory did not complete at %s: %s", step["name"], error)
        try:
            update_advisory(
                RECORD_KEY,
                {**advisory, "status": "failed", "step": step["name"], "error": error, "failed_at": _now()},
                revision,
            )
        except Exception:
            logger.warning("Could not record the GEO advisory failure", exc_info=True)
        return

    pending = advisory["unresolved"]["include_all"]
    if pending["files"]:
        logger.warning(
            "%d file(s) in %d experiment(s) need review before their GEO export: open each experiment's GEO "
            "readiness check to link them to their samples or keep them at experiment level (details: %s in "
            "the backups directory)",
            pending["files"],
            pending["experiments"],
            advisory["report"],
        )


async def run_after_startup(engine: AsyncEngine, *, stopping: asyncio.Event | None, delay: float | None = None) -> None:
    """The owned startup task: wait ``delay`` (or stop), then run once."""
    from app.config import settings
    from app.tasks.periodic import wait_or_stop

    wait = settings.geo_association_advisory_delay_seconds if delay is None else delay
    if await wait_or_stop(wait, stopping):
        return
    await run_once(engine)
