"""Installation/upgrade advisory: which GEO downloads legacy unlinked files will block.

Runs after a successful schema upgrade (app/schema_upgrade.py), outside the migration transaction and
after the upgrade lock is released, so the application's gate is already open. It never fails the upgrade:
a finding, a timeout, a query error or a report-write failure only changes this advisory's own status.
Runtime GEO readiness and download recompute findings regardless of what this reported.

It uses the same detector as readiness and download (app/services/geo/file_associations.py) over every
Organization's export candidates (the Organization's files under ``/experiments/{id}/``, not
``files.experiment_id``), under both QC filters. Prefix matches are diagnostic only; nothing is linked,
acknowledged or otherwise written to the database.

Outputs:
- bounded counts in the upgrade record (``geo_association_advisory``): no filenames, URIs or metadata;
- the detailed findings in an operator-private report beside the recovery backups (0600 in the 0700
  backups directory, owned like it), which is never served.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.services.geo.file_associations import (
    POLICY_REVISION,
    RAW_FILE_TYPES,
    detect_findings,
    pair_fingerprint,
)
from app.services.geo.qc_filter import GeoQcFilter

logger = logging.getLogger("bioaf.geo.association_advisory")

RECORD_KEY = "geo_association_advisory"


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


def scan(conn: Connection, timeout_seconds: int) -> dict[str, Any]:
    """Every Organization's findings under both filters. Read-only, bounded by ``timeout_seconds``."""
    conn.execute(text("SET TRANSACTION READ ONLY"))
    conn.execute(text("SELECT set_config('statement_timeout', :ms, true)"), {"ms": str(int(timeout_seconds * 1000))})

    candidates = conn.execute(
        text(
            "SELECT f.id, f.filename, f.storage_uri, f.file_type, e.id AS experiment_id, e.organization_id "
            "FROM experiments e JOIN files f ON f.organization_id = e.organization_id "
            "AND strpos(f.storage_uri, '/experiments/' || e.id || '/') > 0 "
            "ORDER BY e.organization_id, e.id, f.id"
        )
    ).all()
    file_ids = sorted({c.id for c in candidates})
    linked = set(
        conn.execute(
            text("SELECT DISTINCT file_id FROM sample_files WHERE file_id = ANY(:ids)"), {"ids": file_ids}
        ).scalars()
    )
    experiment_ids = sorted({c.experiment_id for c in candidates})
    samples_by_experiment: dict[int, list[Any]] = defaultdict(list)
    for s in conn.execute(
        text(
            "SELECT id, experiment_id, external_id, qc_status FROM samples WHERE experiment_id = ANY(:ids) ORDER BY id"
        ),
        {"ids": experiment_ids},
    ):
        samples_by_experiment[s.experiment_id].append(SimpleNamespace(**s._mapping))
    acks = {
        (a.experiment_id, a.file_id, a.sample_id): a.fingerprint
        for a in conn.execute(
            text(
                "SELECT experiment_id, file_id, sample_id, fingerprint FROM geo_file_association_acknowledgments "
                "WHERE policy_revision = :rev"
            ),
            {"rev": POLICY_REVISION},
        )
    }
    organizations = {row[0] for row in conn.execute(text("SELECT id FROM organizations"))}

    unlinked_by_experiment: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for c in candidates:
        if c.id not in linked:
            unlinked_by_experiment[(c.organization_id, c.experiment_id)].append(SimpleNamespace(**c._mapping))

    counts = {
        f.value: {"experiments": 0, "files": 0, "sample_pairs": 0, "raw_files": 0, "processed_files": 0}
        for f in GeoQcFilter
    }
    report: dict[int, list[dict]] = defaultdict(list)
    for (org_id, exp_id), unlinked in unlinked_by_experiment.items():
        per_filter: dict[str, list[dict]] = {}
        for qc_filter in GeoQcFilter:
            selected = [
                s
                for s in samples_by_experiment.get(exp_id, [])
                if qc_filter is GeoQcFilter.INCLUDE_ALL or s.qc_status != "fail"
            ]
            unresolved: list[dict[str, Any]] = []
            for f, matched in detect_findings(unlinked, selected):
                pending = [s for s in matched if acks.get((exp_id, f.id, s.id)) != pair_fingerprint(f, set(), s)]
                if pending:
                    unresolved.append(
                        {
                            "file_id": f.id,
                            "filename": f.filename,
                            "file_kind": "raw" if f.file_type in RAW_FILE_TYPES else "processed",
                            "samples": [
                                {"sample_id": s.id, "external_id": s.external_id, "qc_status": s.qc_status}
                                for s in pending
                            ],
                        }
                    )
            if unresolved:
                tally = counts[qc_filter.value]
                tally["experiments"] += 1
                tally["files"] += len(unresolved)
                tally["sample_pairs"] += sum(len(u["samples"]) for u in unresolved)
                tally["raw_files"] += sum(1 for u in unresolved if u["file_kind"] == "raw")
                tally["processed_files"] += sum(1 for u in unresolved if u["file_kind"] == "processed")
                per_filter[qc_filter.value] = unresolved
        if per_filter:
            report[org_id].append({"experiment_id": exp_id, "unresolved": per_filter})

    return {
        "summary": {
            "organizations_scanned": len(organizations),
            "experiments_with_candidates": len(experiment_ids),
            "unlinked_candidate_files": sum(len(v) for v in unlinked_by_experiment.values()),
            "unresolved": counts,
        },
        "organizations": [{"organization_id": org, "experiments": exps} for org, exps in sorted(report.items())],
    }


def write_report(backups: Path, revision: str, scanned_at: str, organizations: list[dict]) -> str:
    """The operator-private detailed report; returns its file name (never its path) for the record."""
    from app.schema_upgrade import _give_to, secure_backups_dir

    owner = secure_backups_dir(backups)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = backups / f"bioaf_geo_association_advisory_{revision}_{stamp}.json"
    attempt = 2
    while path.exists():
        path = backups / f"bioaf_geo_association_advisory_{revision}_{stamp}_{attempt}.json"
        attempt += 1
    body = {
        "policy_revision": POLICY_REVISION,
        "revision": revision,
        "scanned_at": scanned_at,
        "note": (
            "Unlinked files whose names match selected Samples by the pre-link prefix rule. Diagnostic only: "
            "a match is not ownership. Resolve each in the Experiment's GEO readiness check (link the file to "
            "its Sample, or keep it at experiment level)."
        ),
        "organizations": organizations,
    }
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write(json.dumps(body, indent=1))
    _give_to(path, owner)
    return path.name


def run(conn: Connection, revision: str, backups: Path, timeout_seconds: int, update_record) -> None:
    """Scan, report, record. Never raises (short of interpreter exit); failure is the advisory's status."""
    advisory: dict[str, Any] = {
        "status": "running",
        "policy_revision": POLICY_REVISION,
        "revision": revision,
        "started_at": _now(),
    }
    step = "record"
    try:
        update_record(advisory)
        step = "scan"
        try:
            result = scan(conn, timeout_seconds)
        finally:
            conn.rollback()
        step = "report"
        scanned_at = _now()
        report = write_report(backups, revision, scanned_at, result["organizations"])
        advisory = {
            **advisory,
            **result["summary"],
            "status": "completed",
            "completed_at": scanned_at,
            "report": report,
        }
        update_record(advisory)
    except Exception as exc:
        logger.warning("GEO file association advisory did not complete at %s: %s", step, type(exc).__name__)
        advisory = {
            **advisory,
            "status": "failed",
            "step": step,
            "error": type(exc).__name__,
            "failed_at": _now(),
        }
        try:
            update_record(advisory)
        except Exception:
            logger.warning("Could not record the GEO advisory failure", exc_info=True)
        print(
            "Note: the GEO file association check did not complete; GEO readiness still checks each export.",
            flush=True,
        )
        return

    pending = advisory["unresolved"]
    if pending["include_all"]["files"]:
        print(
            f"Note: {pending['include_all']['files']} file(s) in {pending['include_all']['experiments']} experiment(s) "
            "need review before their GEO export: open each experiment's GEO readiness check to link them to "
            f"their samples or keep them at experiment level. Details: {report} in the backups directory.",
            flush=True,
        )
