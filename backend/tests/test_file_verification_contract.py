"""01B.1 verification contracts: expectations, normalization, and the persisted record's states."""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.exceptions import ValidationError
from app.models.file_verification import FileVerification
from app.services.file_service import FileService
from app.services.file_verification_service import FileVerificationService, normalize_md5

pytestmark = pytest.mark.asyncio

HEX = "0123456789abcdef0123456789abcdef"


async def _file(session, org_id, **kw):
    return await FileService.create_file_record(
        session,
        org_id=org_id,
        user_id=None,
        filename="a.fastq.gz",
        storage_uri="s3://raw/a.fastq.gz",
        size_bytes=10,
        md5_checksum=None,
        file_type="fastq",
        **kw,
    )


async def _record(session, file_id) -> FileVerification | None:
    return (
        await session.execute(select(FileVerification).where(FileVerification.file_id == file_id))
    ).scalar_one_or_none()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (HEX, HEX),
        (HEX.upper(), HEX),
        (f"  {HEX} ", HEX),
    ],
)
async def test_hex_md5_is_normalized(raw, expected):
    assert normalize_md5(raw) == expected


async def test_gcs_base64_md5_is_normalized_only_when_allowed():
    b64 = base64.b64encode(bytes.fromhex(HEX)).decode()
    assert normalize_md5(b64, allow_base64=True) == HEX
    with pytest.raises(ValidationError):
        normalize_md5(b64)


@pytest.mark.parametrize("raw", ["", "abc", "g" * 32, HEX + "0", "d41d8cd98f00b204e9800998ecf8427e-2", None, 5])
async def test_invalid_expected_md5_is_rejected(raw):
    with pytest.raises(ValidationError):
        normalize_md5(raw)


async def test_an_expectation_queues_required_work(session, admin_user):
    f = await _file(session, admin_user.organization_id)
    record = await FileVerificationService.record_expectation(session, f, source="upload_assertion", md5=HEX.upper())
    assert record.status == "queued"
    assert record.expected_algorithm == "md5" and record.expected_checksum == HEX
    assert record.observed_checksum is None
    assert record.next_attempt_at is not None
    assert record.organization_id == admin_user.organization_id


async def test_the_same_expectation_again_keeps_the_record(session, admin_user):
    f = await _file(session, admin_user.organization_id)
    first = await FileVerificationService.record_expectation(session, f, source="upload_assertion", md5=HEX)
    first.status = "verified"
    first.observed_checksum = HEX
    again = await FileVerificationService.record_expectation(session, f, source="upload_assertion", md5=HEX)
    assert again.id == first.id and again.status == "verified" and again.expectation_revision == 1


async def test_a_changed_expectation_is_a_new_revision_and_drops_old_evidence(session, admin_user):
    f = await _file(session, admin_user.organization_id)
    record = await FileVerificationService.record_expectation(session, f, source="upload_assertion", md5=HEX)
    record.status = "verified"
    record.observed_checksum = HEX
    record.lease_token = None
    changed = await FileVerificationService.record_expectation(session, f, source="fetchngs_csv", md5="f" * 32)
    assert changed.id == record.id
    assert changed.expectation_revision == 2
    assert changed.status == "queued"
    assert changed.expected_checksum == "f" * 32 and changed.expectation_source == "fetchngs_csv"
    assert changed.observed_checksum is None and changed.verified_at is None
    assert changed.attempt_count == 0


async def test_an_expectation_cannot_be_waived_by_a_later_optional_flow(session, admin_user):
    f = await _file(session, admin_user.organization_id)
    await FileVerificationService.record_expectation(session, f, source="upload_assertion", md5=HEX)
    record = await FileVerificationService.record_not_required(session, f, source="pipeline_output")
    assert record.status == "queued" and record.expected_checksum == HEX


async def test_not_required_and_waiting_states(session, admin_user):
    f = await _file(session, admin_user.organization_id)
    waiting = await FileVerificationService.await_expectation(
        session, f, source="fetchngs_csv", context={"pipeline_run_id": 9}
    )
    assert waiting.status == "waiting_expectation" and waiting.discovery_context == {"pipeline_run_id": 9}
    assert waiting.next_attempt_at is not None

    resolved = await FileVerificationService.record_not_required(session, f, source="fetchngs_csv")
    assert resolved.id == waiting.id and resolved.status == "not_required"
    assert resolved.expected_checksum is None


async def test_status_view_distinguishes_every_state(session, admin_user):
    org = admin_user.organization_id
    legacy = await _file(session, org)
    legacy.md5_checksum = HEX
    assert FileVerificationService.view(legacy, None)["status"] == "unverified_legacy"

    f = await _file(session, org, external_id="y")
    record = await FileVerificationService.record_expectation(session, f, source="upload_assertion", md5=HEX)
    record.observed_checksum = "e" * 32
    record.observed_method = "streamed"
    record.status = "checksum_mismatch"
    record.error_code = "checksum_mismatch"
    record.retryable = False
    view = FileVerificationService.view(f, record)
    assert view == {
        "status": "checksum_mismatch",
        "algorithm": "md5",
        "expected_checksum": HEX,
        "observed_checksum": "e" * 32,
        "method": "streamed",
        "verified_at": None,
        "bytes_processed": None,
        "total_bytes": None,
        "error_code": "checksum_mismatch",
        "retryable": False,
    }


async def test_no_record_and_no_checksum_has_no_verification(session, admin_user):
    f = await _file(session, admin_user.organization_id)
    assert FileVerificationService.view(f, None) is None
