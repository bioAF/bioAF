"""The GEO file association advisory runs after the backend has started, never inside the upgrade.

The upgrade lifecycle (app/schema_upgrade.py) only migrates and records. Once the application is
serving, an owned background task scans every Organization's GEO export candidates with the shared
detector, bounded in time (one deadline for the whole scan and report, plus a per-statement timeout)
and in memory (one Experiment at a time). It records bounded counts beside the upgrade's verdict, only
for the completed upgrade it belongs to, and writes the detail to an operator-private report. Its own
failure marks only the advisory; it is retried on the next start, and a completed scan is not repeated.
"""

import asyncio
import json
import threading

import pytest
import pytest_asyncio
from sqlalchemy import text

from app import schema_upgrade
from app.services.geo import association_advisory

pytestmark = pytest.mark.asyncio

KEY = association_advisory.RECORD_KEY


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    from app.config import settings

    status, backups = tmp_path / "update-status", tmp_path / "backups"
    monkeypatch.setattr(settings, "update_status_dir", str(status))
    monkeypatch.setattr(settings, "upgrade_backups_dir", str(backups))
    monkeypatch.setattr(settings, "geo_association_advisory_timeout_seconds", 30)
    schema_upgrade.write_status(
        {"status": "completed", "step": "validated", "target_revision": schema_upgrade.code_head()}
    )
    return status, backups


@pytest_asyncio.fixture
async def legacy(session, admin_user):
    """Two Organizations: an unlinked file named after a passing Sample, and one after a failed Sample."""
    from app.models.experiment import Experiment
    from app.models.file import File
    from app.models.organization import Organization
    from app.models.sample import Sample, sample_files

    async def file(org_id, exp_id, name, kind):
        uri = f"gs://raw/experiments/{exp_id}/{name}"
        f = File(organization_id=org_id, gcs_uri=uri, storage_uri=uri, filename=name, file_type=kind)
        session.add(f)
        await session.flush()
        return f

    org = admin_user.organization_id
    exp = Experiment(name="Legacy", organization_id=org)
    session.add(exp)
    await session.flush()
    s0 = Sample(experiment_id=exp.id, external_id="S-0", organism="Homo sapiens")
    s1 = Sample(experiment_id=exp.id, external_id="S-1", organism="Homo sapiens")
    session.add_all([s0, s1])
    await session.flush()
    await file(org, exp.id, "S-0_R1.fastq.gz", "fastq.gz")
    lane = await file(org, exp.id, "lane.fastq.gz", "fastq.gz")
    await session.execute(sample_files.insert().values(file_id=lane.id, sample_id=s1.id))
    await file(org, exp.id, "matrix.h5", "h5")

    other = Organization(name="Other lab", setup_complete=True)
    session.add(other)
    await session.flush()
    other_exp = Experiment(name="Other", organization_id=other.id)
    session.add(other_exp)
    await session.flush()
    session.add(Sample(experiment_id=other_exp.id, external_id="T-1", organism="Homo sapiens", qc_status="fail"))
    await file(other.id, other_exp.id, "T-1_counts.h5", "h5")
    await session.commit()


async def _db_state(session) -> tuple:
    links = (await session.execute(text("SELECT file_id, sample_id FROM sample_files ORDER BY 1, 2"))).all()
    acks = (await session.execute(text("SELECT count(*) FROM geo_file_association_acknowledgments"))).scalar_one()
    return links, acks


async def test_the_advisory_reports_counts_and_private_detail_without_writing_data(db_engine, session, dirs, legacy):
    status, backups = dirs
    before = await _db_state(session)

    await association_advisory.run_once(db_engine)

    record = schema_upgrade.read_status()
    assert record["status"] == "completed" and record["step"] == "validated"  # the verdict is untouched
    advisory = record[KEY]
    assert advisory["status"] == "completed" and advisory["revision"] == schema_upgrade.code_head()
    assert advisory["policy_revision"] == 1
    assert advisory["organizations_scanned"] == 2
    assert advisory["unlinked_candidate_files"] == 3
    assert advisory["unresolved"]["exclude_failed"] == {
        "experiments": 1,
        "files": 1,
        "sample_pairs": 1,
        "raw_files": 1,
        "processed_files": 0,
    }
    assert advisory["unresolved"]["include_all"]["files"] == 2
    for secret in ("S-0_R1", "T-1_counts", "gs://", "matrix"):
        assert secret not in json.dumps(record)

    report = backups / advisory["report"]
    assert oct(report.stat().st_mode & 0o777) == "0o600"
    lines = [json.loads(line) for line in report.read_text().splitlines()]
    assert lines[0]["policy_revision"] == 1
    assert {f["filename"] for f in lines[1:]} == {"S-0_R1.fastq.gz", "T-1_counts.h5"}
    assert await _db_state(session) == before


async def test_a_completed_advisory_is_not_repeated(db_engine, dirs, legacy):
    _, backups = dirs
    await association_advisory.run_once(db_engine)
    first = schema_upgrade.read_status()
    await association_advisory.run_once(db_engine)
    assert schema_upgrade.read_status() == first
    assert len(list(backups.glob("bioaf_geo_association_advisory_*"))) == 1


@pytest.mark.parametrize(
    "record",
    [
        {"status": "in_progress", "step": "migrate", "target_revision": "999"},
        {"status": "completed", "step": "validated", "target_revision": "999"},
        {"status": "failed", "step": "migrate", "target_revision": "HEAD"},
    ],
    ids=["upgrade-in-progress", "other-revision", "failed-upgrade"],
)
async def test_the_advisory_never_writes_over_another_upgrades_record(db_engine, dirs, legacy, record):
    _, backups = dirs
    if record["target_revision"] == "HEAD":
        record = {**record, "target_revision": schema_upgrade.code_head()}
    schema_upgrade.write_status(record)

    await association_advisory.run_once(db_engine)

    assert schema_upgrade.read_status() == record
    assert not list(backups.glob("bioaf_geo_association_advisory_*"))


async def test_a_statement_timeout_marks_only_the_advisory_and_is_retried(db_engine, dirs, legacy, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "geo_association_advisory_timeout_seconds", 1)
    async with db_engine.connect() as holder:
        await holder.execute(text("LOCK TABLE sample_files IN ACCESS EXCLUSIVE MODE"))
        await association_advisory.run_once(db_engine)
        await holder.rollback()

    record = schema_upgrade.read_status()
    assert record["status"] == "completed"
    advisory = record[KEY]
    assert advisory["status"] == "failed" and advisory["step"] == "scan" and advisory["error"]
    assert "unresolved" not in advisory  # never reads as zero findings

    monkeypatch.setattr(settings, "geo_association_advisory_timeout_seconds", 30)
    await association_advisory.run_once(db_engine)
    advisory = schema_upgrade.read_status()[KEY]
    assert advisory["status"] == "completed" and advisory["unresolved"]["include_all"]["files"] == 2


async def test_the_whole_scan_has_one_deadline(db_engine, dirs, legacy, monkeypatch):
    """statement_timeout bounds each query; the Python work between them has its own deadline."""
    from app.config import settings

    monkeypatch.setattr(settings, "geo_association_advisory_timeout_seconds", 0.2)

    async def stall(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(association_advisory, "_scan_experiment", stall)
    await association_advisory.run_once(db_engine)
    advisory = schema_upgrade.read_status()[KEY]
    assert advisory["status"] == "failed" and advisory["error"] == "TimeoutError"


async def test_an_unwritable_report_fails_only_the_advisory(db_engine, dirs, legacy, tmp_path, monkeypatch):
    from app.config import settings

    not_a_directory = tmp_path / "backups-file"
    not_a_directory.write_text("")
    monkeypatch.setattr(settings, "upgrade_backups_dir", str(not_a_directory))

    await association_advisory.run_once(db_engine)
    record = schema_upgrade.read_status()
    assert record["status"] == "completed"
    assert record[KEY]["status"] == "failed" and record[KEY]["step"] == "report"
    assert "unresolved" not in record[KEY]


async def test_an_interrupted_advisory_is_retried(db_engine, dirs, legacy):
    record = schema_upgrade.read_status()
    schema_upgrade.write_status({**record, KEY: {"status": "running", "policy_revision": 1}})
    await association_advisory.run_once(db_engine)
    assert schema_upgrade.read_status()[KEY]["status"] == "completed"


async def test_the_startup_task_waits_then_runs_and_stops_with_the_owner(db_engine, dirs, legacy):
    stopping = asyncio.Event()
    task = asyncio.create_task(association_advisory.run_after_startup(db_engine, stopping=stopping, delay=0.05))
    await asyncio.wait_for(task, 10)
    assert schema_upgrade.read_status()[KEY]["status"] == "completed"

    # Stopping during the delay ends it without scanning.
    schema_upgrade.write_status({k: v for k, v in schema_upgrade.read_status().items() if k != KEY})
    stopping = asyncio.Event()
    task = asyncio.create_task(association_advisory.run_after_startup(db_engine, stopping=stopping, delay=60))
    stopping.set()
    await asyncio.wait_for(task, 5)
    assert KEY not in schema_upgrade.read_status()


async def test_concurrent_record_writers_never_collide_or_lose_the_verdict(dirs):
    head = schema_upgrade.code_head()
    errors: list[BaseException] = []

    def lifecycle():
        try:
            for _ in range(100):
                schema_upgrade.write_status({"status": "completed", "step": "validated", "target_revision": head})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def advisory():
        try:
            for i in range(100):
                schema_upgrade.update_advisory(KEY, {"status": "running", "n": i}, head)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=f) for f in (lifecycle, advisory, lifecycle, advisory)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    record = schema_upgrade.read_status()
    assert record["status"] == "completed" and record["target_revision"] == head
    assert not list(schema_upgrade.status_path().parent.glob("*.tmp"))
