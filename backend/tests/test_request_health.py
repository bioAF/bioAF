import pytest
from httpx import AsyncClient

from app.services.request_health import clear, get_service_health, record


@pytest.fixture(autouse=True)
def reset_counters():
    clear()
    yield
    clear()


def test_record_and_get_healthy():
    """Service with all successful requests is healthy."""
    for _ in range(10):
        record("/api/experiments/123", 200)
    health = get_service_health()
    assert health["experiments"] == "healthy"


def test_record_mixed_degraded():
    """Service with 60% success rate is degraded."""
    for _ in range(6):
        record("/api/experiments/123", 200)
    for _ in range(4):
        record("/api/experiments/123", 500)
    health = get_service_health()
    assert health["experiments"] == "degraded"


def test_record_mostly_failures_unhealthy():
    """Service with <50% success rate is unhealthy."""
    for _ in range(3):
        record("/api/experiments/123", 200)
    for _ in range(7):
        record("/api/experiments/123", 500)
    health = get_service_health()
    assert health["experiments"] == "unhealthy"


def test_single_failure_is_unhealthy():
    """A single failed request with no successes is unhealthy."""
    record("/api/experiments/123", 500)
    health = get_service_health()
    assert health["experiments"] == "unhealthy"


def test_no_traffic_is_unknown():
    """Services with no traffic don't appear in health."""
    health = get_service_health()
    assert "experiments" not in health


def test_unmapped_routes_ignored():
    """Routes that don't match any service prefix are ignored."""
    record("/api/some-unknown-route", 200)
    health = get_service_health()
    assert len(health) == 0


def test_multiple_services():
    """Multiple services tracked independently."""
    record("/api/experiments/1", 200)
    record("/api/samples/1", 500)
    health = get_service_health()
    assert health["experiments"] == "healthy"
    assert health["samples"] == "unhealthy"


def test_route_mapping():
    """Various routes map to correct services."""
    record("/api/experiments/1/status", 200)
    record("/api/v1/notebooks/sessions", 200)
    record("/api/pipeline-runs/5/logs", 200)
    record("/api/files/upload", 200)
    health = get_service_health()
    assert "experiments" in health
    assert "notebooks" in health
    assert "pipelines" in health
    assert "storage" in health


@pytest.mark.asyncio
async def test_service_health_endpoint(client: AsyncClient, admin_token: str):
    """The /api/health/services endpoint returns service health."""
    # Generate some traffic first
    await client.get("/api/experiments", headers={"Authorization": f"Bearer {admin_token}"})

    response = await client.get("/api/health/services")
    assert response.status_code == 200
    data = response.json()
    assert "services" in data
