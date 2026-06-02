"""Tests for ingest API endpoints."""

import pytest
import pytest_asyncio

from app.services.auto_ingest_gate import AUTO_INGEST_DISABLED

# Auto-ingest is gated off during the Naming Profile redesign. These tests
# assert behaviors of the gated body and will be re-enabled by the follow-up
# rework when AUTO_INGEST_DISABLED flips. See
# local/Naming Profiles/spec-auto-ingest-neutralize.md.
pytestmark = pytest.mark.skipif(
    AUTO_INGEST_DISABLED,
    reason="auto-ingest gated off during Naming Profile redesign",
)


@pytest_asyncio.fixture
async def _create_profile(client, admin_token):
    """Create a naming profile for ingest tests."""
    await client.post(
        "/api/naming-profiles",
        json={
            "name": "Ingest Test Profile",
            "segments": [
                {"position": 0, "field": "date", "format": "YYYY-MM-DD", "required": True},
                {"position": 1, "field": "project_code", "required": True},
                {"position": 2, "field": "experiment_code", "required": True},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )


@pytest.mark.asyncio
async def test_simulate_known_profile(client, admin_token, _create_profile):
    """Simulate endpoint with a matching filename produces cataloged status."""
    response = await client.post(
        "/api/ingest/simulate",
        json={"filename": "2026-03-10_TestPrj_TestExp.fastq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ingest_status"] == "cataloged"
    assert data["file_id"] is not None
    assert data["parsed_project_code"] == "TestPrj"


@pytest.mark.asyncio
async def test_simulate_unknown_naming(client, admin_token, _create_profile):
    """Simulate with an unrecognized filename produces unmatched status."""
    response = await client.post(
        "/api/ingest/simulate",
        json={"filename": "random_file.bam"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["ingest_status"] == "unmatched"


@pytest.mark.asyncio
async def test_list_ingest_events(client, admin_token, _create_profile):
    # Create some events
    await client.post(
        "/api/ingest/simulate",
        json={"filename": "2026-03-10_Prj1_Exp1.fastq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response = await client.get(
        "/api/ingest/events",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


@pytest.mark.asyncio
async def test_list_ingest_events_with_filter(client, admin_token, _create_profile):
    await client.post(
        "/api/ingest/simulate",
        json={"filename": "2026-03-10_PrjF_ExpF.fastq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response = await client.get(
        "/api/ingest/events?status=cataloged",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    for event in response.json():
        assert event["ingest_status"] == "cataloged"


@pytest.mark.asyncio
async def test_unclaimed_endpoint(client, admin_token, _create_profile):
    """Unclaimed endpoint returns only unclaimed entities."""
    # Simulate creates unclaimed entities
    await client.post(
        "/api/ingest/simulate",
        json={"filename": "2026-03-10_UnclaimedA_UnclaimedB.fastq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response = await client.get(
        "/api/ingest/unclaimed",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    unclaimed = response.json()
    assert len(unclaimed) >= 1


@pytest.mark.asyncio
async def test_claim_project(client, admin_token, _create_profile):
    """Claim endpoint sets is_unclaimed=false and writes audit log."""
    # Create unclaimed via simulate
    resp = await client.post(
        "/api/ingest/simulate",
        json={"filename": "2026-03-10_ClaimPrj_ClaimExp.fastq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    project_id = resp.json()["resolved_project_id"]

    # Claim it
    response = await client.post(
        f"/api/projects/{project_id}/claim",
        json={"name": "Claimed Project Name"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["is_unclaimed"] is False
    assert response.json()["name"] == "Claimed Project Name"


@pytest.mark.asyncio
async def test_claim_requires_role(client, viewer_token, _create_profile):
    """Claiming requires comp_bio or admin role."""
    response = await client.post(
        "/api/projects/999/claim",
        json={},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reassign_endpoint(client, admin_token, _create_profile):
    """Reassign endpoint updates file linkages."""
    # Create a file via simulate
    resp = await client.post(
        "/api/ingest/simulate",
        json={"filename": "2026-03-10_ReassignPrj_ReassignExp.fastq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    file_id = resp.json()["file_id"]

    response = await client.post(
        "/api/ingest/reassign",
        json={"file_ids": [file_id], "target_project_id": None},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["reassigned_count"] == 1
