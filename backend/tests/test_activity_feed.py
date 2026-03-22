import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_activity_feed_empty(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/activity-feed",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["events"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_activity_feed_viewer_allowed(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/activity-feed",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_activity_feed_with_data(client: AsyncClient, admin_token: str, admin_user, session):
    from app.services.activity_feed_service import ActivityFeedService

    await ActivityFeedService.add_event(
        session,
        admin_user.organization_id,
        admin_user.id,
        "experiment.status_changed",
        "Experiment EXP-001 moved to sequencing",
        entity_type="experiment",
        entity_id=1,
    )
    await ActivityFeedService.add_event(
        session,
        admin_user.organization_id,
        admin_user.id,
        "pipeline.completed",
        "Pipeline RNA-seq completed successfully",
        entity_type="pipeline_run",
        entity_id=5,
    )
    await session.commit()

    response = await client.get(
        "/api/activity-feed",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_activity_feed_includes_user_email_and_severity(
    client: AsyncClient, admin_token: str, admin_user, session
):
    from app.services.activity_feed_service import ActivityFeedService

    await ActivityFeedService.add_event(
        session,
        admin_user.organization_id,
        admin_user.id,
        "pipeline.failed",
        "Pipeline RNA-seq failed",
        entity_type="pipeline_run",
        entity_id=10,
    )
    await session.commit()

    response = await client.get(
        "/api/activity-feed",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    event = data["events"][0]
    assert event["user_email"] == admin_user.email
    assert event["severity"] == "critical"


@pytest.mark.asyncio
async def test_filter_activity_feed_by_event_type(client: AsyncClient, admin_token: str, admin_user, session):
    from app.services.activity_feed_service import ActivityFeedService

    await ActivityFeedService.add_event(
        session,
        admin_user.organization_id,
        admin_user.id,
        "pipeline.completed",
        "Pipeline done",
    )
    await ActivityFeedService.add_event(
        session,
        admin_user.organization_id,
        admin_user.id,
        "experiment.status_changed",
        "Experiment updated",
    )
    await session.commit()

    response = await client.get(
        "/api/activity-feed?event_type=pipeline.completed",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    for event in response.json()["events"]:
        assert event["event_type"] == "pipeline.completed"
