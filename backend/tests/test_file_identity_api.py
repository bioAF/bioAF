"""01B.5: file responses expose identity, references, readiness, and verification; retry is authorized.

Regular and Integration API file responses add ``external_id``, the parent external IDs paired with
their existing IDs, ``samples`` with experiment context, ``ready_at``, and a ``verification`` object.
Reading details never starts hashing or touches storage.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.experiment import Experiment
from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.organization import Organization
from app.models.project import Project
from app.models.sample import Sample
from app.services.file_service import FileService

pytestmark = pytest.mark.asyncio

HEX = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def no_storage(monkeypatch):
    def refuse():
        raise AssertionError("reading file details must not touch storage")

    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", refuse)


async def _tree(session, org_id):
    project = Project(organization_id=org_id, name="P", external_id="PRJ-1")
    session.add(project)
    await session.flush()
    exp = Experiment(organization_id=org_id, project_id=project.id, name="E", external_id="EXP-1")
    session.add(exp)
    await session.flush()
    s1 = Sample(experiment_id=exp.id, external_id="S-002")
    s2 = Sample(experiment_id=exp.id, external_id=None)
    session.add_all([s1, s2])
    await session.flush()
    return project, exp, s1, s2


async def _file(session, org_id, **kw):
    return await FileService.create_file_record(
        session,
        org_id=org_id,
        user_id=None,
        filename="a.fastq.gz",
        storage_uri="s3://raw/a.fastq.gz",
        size_bytes=5,
        md5_checksum=kw.pop("md5_checksum", None),
        file_type="fastq",
        **kw,
    )


async def _linked(session, admin_user, external_id="LIMS-F-9"):
    project, exp, s1, s2 = await _tree(session, admin_user.organization_id)
    f = await _file(session, admin_user.organization_id, experiment_id=exp.id, external_id=external_id)
    for s in (s2, s1):
        await FileService.link_file_to_sample(session, f.id, s.id)
    await session.commit()
    return f, project, exp, s1, s2


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_regular_response_carries_identity_and_references(client, admin_token, session, admin_user):
    f, project, exp, s1, s2 = await _linked(session, admin_user)

    body = (await client.get(f"/api/files/{f.id}", headers=_auth(admin_token))).json()

    assert body["id"] == f.id and body["external_id"] == "LIMS-F-9"
    assert body["experiment_id"] == exp.id and body["experiment_external_id"] == "EXP-1"
    # The existing project_id is unchanged (the file's own column); its external ID is paired with it.
    assert body["project_id"] is None and body["project_external_id"] is None
    assert body["sample_ids"] == sorted([s1.id, s2.id])
    assert [s["sample_id"] for s in body["samples"]] == body["sample_ids"]
    by_id = {s["sample_id"]: s for s in body["samples"]}
    assert by_id[s1.id] == {
        "sample_id": s1.id,
        "external_id": "S-002",
        "experiment_id": exp.id,
        "experiment_external_id": "EXP-1",
    }
    assert by_id[s2.id]["external_id"] is None
    assert body["ready_at"] is None
    assert body["verification"] is None


async def test_project_scoped_and_unassigned_files(client, admin_token, session, admin_user):
    project, *_ = await _tree(session, admin_user.organization_id)
    scoped = await _file(session, admin_user.organization_id, project_id=project.id)
    loose = await _file(session, admin_user.organization_id)
    loose.storage_uri = "s3://raw/b"
    await session.commit()

    scoped_body = (await client.get(f"/api/files/{scoped.id}", headers=_auth(admin_token))).json()
    loose_body = (await client.get(f"/api/files/{loose.id}", headers=_auth(admin_token))).json()
    assert scoped_body["project_id"] == project.id and scoped_body["project_external_id"] == "PRJ-1"
    assert loose_body["external_id"] is None
    assert loose_body["samples"] == [] and loose_body["sample_ids"] == []
    assert loose_body["experiment_external_id"] is None and loose_body["project_external_id"] is None


@pytest.mark.parametrize(
    ("record", "md5", "expected"),
    [
        (None, HEX, {"status": "unverified_legacy", "observed_checksum": HEX, "expected_checksum": None}),
        (
            {"status": "verifying", "bytes_processed": 50, "total_bytes": 100, "expected_checksum": HEX},
            None,
            {"status": "verifying", "bytes_processed": 50, "total_bytes": 100},
        ),
        (
            {
                "status": "checksum_mismatch",
                "expected_checksum": HEX,
                "observed_checksum": "f" * 32,
                "observed_method": "streamed",
                "error_code": "checksum_mismatch",
                "retryable": False,
            },
            None,
            {
                "status": "checksum_mismatch",
                "expected_checksum": HEX,
                "observed_checksum": "f" * 32,
                "method": "streamed",
                "error_code": "checksum_mismatch",
                "retryable": False,
            },
        ),
        ({"status": "not_required"}, None, {"status": "not_required", "expected_checksum": None}),
        (
            {"status": "failed", "error_code": "storage_unavailable", "retryable": True, "expected_checksum": HEX},
            None,
            {"status": "failed", "error_code": "storage_unavailable", "retryable": True},
        ),
        ({"status": "waiting_expectation"}, None, {"status": "waiting_expectation"}),
    ],
)
async def test_verification_states_are_distinguished(client, admin_token, session, admin_user, record, md5, expected):
    f = await _file(session, admin_user.organization_id, md5_checksum=md5)
    if record is not None:
        session.add(
            FileVerification(
                organization_id=admin_user.organization_id,
                file_id=f.id,
                expected_algorithm="md5" if record.get("expected_checksum") else None,
                **record,
            )
        )
    await session.commit()

    body = (await client.get(f"/api/files/{f.id}", headers=_auth(admin_token))).json()
    for key, value in expected.items():
        assert body["verification"][key] == value, key
    count = len((await session.execute(select(FileVerification))).scalars().all())
    assert count == (0 if record is None else 1)  # the read created no work


async def test_verified_files_show_time_and_readiness(client, admin_token, session, admin_user):
    f = await _file(session, admin_user.organization_id)
    when = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    f.ready_at = when
    session.add(
        FileVerification(
            organization_id=admin_user.organization_id,
            file_id=f.id,
            status="verified",
            expected_algorithm="md5",
            expected_checksum=HEX,
            observed_checksum=HEX,
            observed_method="provider",
            verified_at=when,
        )
    )
    await session.commit()
    body = (await client.get(f"/api/files/{f.id}", headers=_auth(admin_token))).json()
    assert body["ready_at"].startswith("2026-11-15T12:00:00")
    assert body["verification"]["verified_at"].startswith("2026-11-15T12:00:00")
    assert body["verification"]["method"] == "provider"


async def test_listing_carries_the_same_fields(client, admin_token, session, admin_user):
    f, *_ = await _linked(session, admin_user)
    body = (await client.get("/api/files", headers=_auth(admin_token))).json()
    [row] = [r for r in body["files"] if r["id"] == f.id]
    assert row["external_id"] == "LIMS-F-9" and len(row["samples"]) == 2 and "verification" in row


async def test_integration_api_carries_identity_and_verification(client, integration_api_key, session, admin_user):
    f, project, exp, s1, s2 = await _linked(session, admin_user)
    session.add(
        FileVerification(
            organization_id=admin_user.organization_id,
            file_id=f.id,
            status="queued",
            expected_algorithm="md5",
            expected_checksum=HEX,
        )
    )
    await session.commit()
    headers = integration_api_key["headers"]

    body = (await client.get(f"/api/v1/integrations/files/{f.id}", headers=headers)).json()
    assert body["external_id"] == "LIMS-F-9"
    assert body["experiment_external_id"] == "EXP-1"
    assert [s["sample_id"] for s in body["samples"]] == sorted([s1.id, s2.id])
    assert body["verification"]["status"] == "queued" and body["verification"]["expected_checksum"] == HEX
    assert body["ready_at"] is None
    assert "gcs_uri" not in body and "storage_uri" not in body

    listing = (await client.get("/api/v1/integrations/files", headers=headers)).json()
    assert listing["items"][0]["external_id"] == "LIMS-F-9"


async def test_another_organizations_file_stays_hidden(client, integration_api_key, admin_token, session):
    other = Organization(name="Other")
    session.add(other)
    await session.flush()
    f = await _file(session, other.id, external_id="SECRET")
    await session.commit()
    assert (
        await client.get(f"/api/v1/integrations/files/{f.id}", headers=integration_api_key["headers"])
    ).status_code == 404
    assert (await client.get(f"/api/files/{f.id}", headers=_auth(admin_token))).status_code == 404


# --- retry ------------------------------------------------------------------------------------


async def _failed(session, admin_user, status="failed"):
    f = await _file(session, admin_user.organization_id)
    session.add(
        FileVerification(
            organization_id=admin_user.organization_id,
            file_id=f.id,
            status=status,
            expected_algorithm="md5",
            expected_checksum=HEX,
            error_code="storage_permission_denied",
            retryable=False,
            attempt_count=1,
        )
    )
    await session.commit()
    return f


async def test_an_editor_can_retry_a_failed_verification(client, admin_token, session, admin_user):
    f = await _failed(session, admin_user)
    r = await client.post(f"/api/files/{f.id}/verification/retry", headers=_auth(admin_token))
    assert r.status_code == 200
    assert r.json()["verification"]["status"] == "queued"


async def test_retry_refuses_in_progress_work(client, admin_token, session, admin_user):
    f = await _failed(session, admin_user, status="verifying")
    r = await client.post(f"/api/files/{f.id}/verification/retry", headers=_auth(admin_token))
    assert r.status_code == 409


async def test_retry_needs_edit_permission_and_the_same_organization(
    client, viewer_token, admin_token, session, admin_user
):
    f = await _failed(session, admin_user)
    assert (await client.post(f"/api/files/{f.id}/verification/retry", headers=_auth(viewer_token))).status_code == 403
    other = Organization(name="Other")
    session.add(other)
    await session.flush()
    foreign = await _file(session, other.id)
    session.add(FileVerification(organization_id=other.id, file_id=foreign.id, status="failed"))
    await session.commit()
    r = await client.post(f"/api/files/{foreign.id}/verification/retry", headers=_auth(admin_token))
    assert r.status_code == 404


async def test_retry_of_a_file_with_no_verification_is_404(client, admin_token, session, admin_user):
    f = await _file(session, admin_user.organization_id)
    await session.commit()
    r = await client.post(f"/api/files/{f.id}/verification/retry", headers=_auth(admin_token))
    assert r.status_code == 404


async def test_deleted_files_keep_their_identity_for_provenance(client, integration_api_key, session, admin_user):
    f = await _file(session, admin_user.organization_id, external_id="KEEP")
    f.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    body = (await client.get(f"/api/v1/integrations/files/{f.id}", headers=integration_api_key["headers"])).json()
    assert body["external_id"] == "KEEP"
    assert (await session.get(File, f.id)).external_id == "KEEP"
