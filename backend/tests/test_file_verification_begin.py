"""01B.4: the expectation adapters registration paths call, and the fetchngs CSV resolver.

``FileVerificationService.begin`` runs in the registering transaction: a source with no checksum makes
the file ready on existence and size; an explicit MD5 is compared at once only against a trustworthy
native digest of the final object, otherwise it is queued for the worker; an unresolved source waits.
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import ValidationError
from app.models.file_verification import FileVerification
from app.models.pipeline_run import PipelineRun
from app.services.event_types import FILE_VERIFICATION_FAILED, INTEGRATION_FILE_READY
from app.services.fetchngs_ingest_service import resolve_fetchngs_expectation
from app.services.file_service import FileService
from app.services.file_verification_service import (
    Expectation,
    FileVerificationService,
    agreed_upload_md5,
)
from tests.support.event_capture import EventCapture

pytestmark = pytest.mark.asyncio

DATA = b"ACGT" * 1000
GOOD = hashlib.md5(DATA).hexdigest()


@pytest_asyncio.fixture
async def events():
    capture = EventCapture([INTEGRATION_FILE_READY, FILE_VERIFICATION_FAILED])
    yield capture
    capture.close()


class _Native:
    def __init__(self, native):
        self.native = native

    async def object_identity(self, uri):
        from app.adapters.models import ObjectIdentity

        return ObjectIdentity(uri=uri, size_bytes=len(DATA), version="g1", native_md5=self.native)


async def _file(session, org, uri="gs://raw/a.fastq.gz"):
    return await FileService.create_file_record(
        session,
        org_id=org,
        user_id=None,
        filename="a.fastq.gz",
        storage_uri=uri,
        size_bytes=None,
        md5_checksum=None,
        file_type="fastq",
    )


async def _record(session, file_id):
    return (await session.execute(select(FileVerification).where(FileVerification.file_id == file_id))).scalar_one()


@pytest.mark.parametrize(
    ("expected", "actual", "agreed"),
    [
        (None, None, None),
        (GOOD, None, GOOD),
        (None, GOOD.upper(), GOOD),
        (GOOD, GOOD.upper(), GOOD),
        (None, "", None),  # an empty completion value is not an assertion
    ],
)
async def test_upload_assertions_are_normalized_and_agreed(expected, actual, agreed):
    assert agreed_upload_md5(expected, actual) == agreed


async def test_disagreeing_upload_assertions_are_rejected():
    with pytest.raises(ValidationError):
        agreed_upload_md5(GOOD, "0" * 32)


async def test_no_expectation_makes_a_present_file_ready(session, admin_user, monkeypatch, events):
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: _Native(GOOD))
    f = await _file(session, admin_user.organization_id)
    outcome = await FileVerificationService.begin(session, f, Expectation.none("pipeline_output"))
    await session.commit()
    await events.settle()
    assert outcome == "ready"
    record = await _record(session, f.id)
    # A native digest with no expectation is not compared with itself and proves nothing.
    assert record.status == "not_required" and record.observed_checksum is None
    assert len(events.of(INTEGRATION_FILE_READY)) == 1


async def test_a_matching_native_digest_completes_in_the_request(session, admin_user, monkeypatch, events):
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: _Native(GOOD))
    f = await _file(session, admin_user.organization_id)
    outcome = await FileVerificationService.begin(session, f, Expectation.md5("upload_assertion", GOOD))
    await session.commit()
    await events.settle()
    assert outcome == "ready"
    record = await _record(session, f.id)
    assert record.status == "verified" and record.observed_method == "provider"
    assert record.observed_object_version == "g1"
    assert len(events.of(INTEGRATION_FILE_READY)) == 1


async def test_a_mismatching_native_digest_is_recorded_not_retried(session, admin_user, monkeypatch, events):
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: _Native("0" * 32))
    f = await _file(session, admin_user.organization_id)
    outcome = await FileVerificationService.begin(session, f, Expectation.md5("upload_assertion", GOOD))
    await session.commit()
    await events.settle()
    assert outcome == "checksum_mismatch"
    record = await _record(session, f.id)
    assert record.status == "checksum_mismatch" and record.next_attempt_at is None
    assert events.of(INTEGRATION_FILE_READY) == []
    assert len(events.of(FILE_VERIFICATION_FAILED)) == 1


async def test_without_a_native_digest_the_worker_is_queued(session, admin_user, monkeypatch, events):
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: _Native(None))
    f = await _file(session, admin_user.organization_id)
    outcome = await FileVerificationService.begin(session, f, Expectation.md5("upload_assertion", GOOD))
    await session.commit()
    await events.settle()
    assert outcome == "pending"
    assert (await _record(session, f.id)).status == "queued"
    assert events.of(INTEGRATION_FILE_READY) == []


async def test_a_pending_source_waits(session, admin_user, events):
    f = await _file(session, admin_user.organization_id)
    outcome = await FileVerificationService.begin(
        session, f, Expectation.pending("fetchngs_csv", {"samplesheet_uri": "s3://x"})
    )
    await session.commit()
    assert outcome == "pending"
    assert (await _record(session, f.id)).status == "waiting_expectation"


# --- the fetchngs CSV resolver ----------------------------------------------------------------


@pytest.fixture
def s3_local(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import s3 as module

    monkeypatch.setattr(module, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = S3StorageProvider(org_slug="t")
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)
    return adapter


OUT = "s3://bioaf-results-t/runs/5"
SHEET = (
    "sample,run_accession,fastq_1,fastq_2,md5_1,md5_2\n"
    f"S1,SRR1,/work/SRR1_1.fastq.gz,/work/SRR1_2.fastq.gz,{GOOD.upper()},\n"
    "S2,SRR2,/work/SRR2_1.fastq.gz,,,\n"
)


async def _fetchngs_file(session, org, name):
    run = PipelineRun(organization_id=org, pipeline_name="nf-core/fetchngs", status="completed")
    session.add(run)
    await session.flush()
    return await FileService.create_file_record(
        session,
        org_id=org,
        user_id=None,
        filename=name,
        storage_uri=f"{OUT}/fastq/{name}",
        size_bytes=None,
        md5_checksum=None,
        file_type="fastq",
        source_type="pipeline_output",
        source_pipeline_run_id=run.id,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("SRR1_1.fastq.gz", GOOD),  # md5_1 present (normalized)
        ("SRR1_2.fastq.gz", None),  # mate present, no checksum column value
        ("SRR2_1.fastq.gz", None),
    ],
)
async def test_fetchngs_csv_values_are_expectations(session, admin_user, s3_local, name, expected):
    await s3_local.write_text(f"{OUT}/samplesheet/samplesheet.csv", SHEET)
    f = await _fetchngs_file(session, admin_user.organization_id, name)
    got = await resolve_fetchngs_expectation(session, f, {"samplesheet_uri": f"{OUT}/samplesheet/samplesheet.csv"})
    assert got == expected


async def test_an_unreadable_samplesheet_is_a_discovery_failure_not_no_checksum(session, admin_user, s3_local):
    f = await _fetchngs_file(session, admin_user.organization_id, "SRR1_1.fastq.gz")
    with pytest.raises(Exception):
        await resolve_fetchngs_expectation(session, f, {"samplesheet_uri": f"{OUT}/samplesheet/samplesheet.csv"})


async def test_a_file_missing_from_the_samplesheet_is_a_discovery_failure(session, admin_user, s3_local):
    await s3_local.write_text(f"{OUT}/samplesheet/samplesheet.csv", SHEET)
    f = await _fetchngs_file(session, admin_user.organization_id, "SRR9_1.fastq.gz")
    with pytest.raises(LookupError):
        await resolve_fetchngs_expectation(session, f, {"samplesheet_uri": f"{OUT}/samplesheet/samplesheet.csv"})


async def test_a_failure_notifies_the_responsible_non_admin_user(session, admin_user, viewer_user, monkeypatch, events):
    """The uploader is the recipient, not only the admins the default rules reach."""
    import app.database as _database
    from sqlalchemy import select

    from app.models.notification import Notification
    from app.services.notification_router import NotificationRouter

    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: _Native("0" * 32))
    f = await _file(session, admin_user.organization_id)
    f.uploader_user_id = viewer_user.id
    await session.flush()
    await FileVerificationService.begin(session, f, Expectation.md5("upload_assertion", GOOD))
    await session.commit()
    await events.settle()
    [payload] = events.of(FILE_VERIFICATION_FAILED)
    assert payload["target_user_id"] == viewer_user.id

    await NotificationRouter(_database.async_session_factory)._handle_event(payload)
    recipients = {
        n.user_id
        for n in (
            await session.execute(select(Notification).where(Notification.event_type == FILE_VERIFICATION_FAILED))
        ).scalars()
    }
    assert viewer_user.id in recipients


async def test_a_target_user_from_another_organization_is_never_notified(session, admin_user):
    import app.database as _database
    from sqlalchemy import select

    from app.models.notification import Notification
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.notification_router import NotificationRouter

    other_org = Organization(name="Other")
    session.add(other_org)
    await session.flush()
    outsider = User(
        organization_id=other_org.id,
        email="outsider@example.com",
        password_hash="x",
        role_id=admin_user._test_role_map["admin"],
        status="active",
    )
    session.add(outsider)
    await session.commit()

    await NotificationRouter(_database.async_session_factory)._handle_event(
        {
            "event_type": FILE_VERIFICATION_FAILED,
            "org_id": admin_user.organization_id,
            "target_user_id": outsider.id,
            "title": "File verification failed",
            "message": "x.fastq.gz: The file's checksum does not match the expected value.",
        }
    )
    rows = (await session.execute(select(Notification).where(Notification.user_id == outsider.id))).scalars().all()
    assert rows == []
