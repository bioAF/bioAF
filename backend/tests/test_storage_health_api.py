"""Storage health and bucket confirmation routes (storage lifecycle 11C; mockups M14, M15).

Reading health needs infrastructure view; Check again needs infrastructure deploy or
configure; confirming buckets needs infrastructure configure, a reason and the
explicit confirmation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.adapters.models import ContainerObservation, ContainerPresence

RAW = "bioaf-raw-acme-abc123"
RESULTS = "bioaf-results-acme-abc123"


class FakeAdapter:
    named_containers = True

    def __init__(self, observations):
        self.observations = observations
        self.calls = 0

    def build_uri(self, bucket, key):
        return f"gs://{bucket}/{key}"

    async def inspect_container(self, uri):
        self.calls += 1
        bucket = uri.removeprefix("gs://").split("/", 1)[0]
        return self.observations.get(bucket, ContainerObservation(presence=ContainerPresence.MISSING))


PRESENT = ContainerObservation(
    presence=ContainerPresence.PRESENT,
    owner="1234",
    created_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
    labels={"managed_by": "bioaf"},
)


@pytest_asyncio.fixture
async def buckets(session, admin_user):
    for k, v in {"raw_bucket_name": RAW, "results_bucket_name": RESULTS, "storage_stack_uid": "abc123"}.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health_lists_each_configured_bucket(client, admin_token, buckets):
    adapter = FakeAdapter({RAW: PRESENT})
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.get("/api/v1/infrastructure/storage/health", headers=_auth(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert [(s["store"], s["bucket_name"], s["health"]) for s in body["stores"]] == [
        ("raw", RAW, "not_confirmed"),
        ("results", RESULTS, "not_found"),
    ]
    assert body["unconfirmed"] == 1
    assert body["checked_at"] is not None


@pytest.mark.asyncio
async def test_health_read_reuses_the_last_check(client, admin_token, buckets):
    adapter = FakeAdapter({RAW: PRESENT})
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await client.get("/api/v1/infrastructure/storage/health", headers=_auth(admin_token))
        calls = adapter.calls
        await client.get("/api/v1/infrastructure/storage/health", headers=_auth(admin_token))
    assert adapter.calls == calls


@pytest.mark.asyncio
async def test_comp_bio_can_read_health_but_not_check_again(client, comp_bio_token, buckets):
    adapter = FakeAdapter({RAW: PRESENT})
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        read = await client.get("/api/v1/infrastructure/storage/health", headers=_auth(comp_bio_token))
        check = await client.post("/api/v1/infrastructure/storage/health/check", headers=_auth(comp_bio_token))
    assert read.status_code == 200
    assert check.status_code == 403


@pytest.mark.asyncio
async def test_bench_cannot_read_health(client, bench_token, buckets):
    resp = await client.get("/api/v1/infrastructure/storage/health", headers=_auth(bench_token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_check_again_inspects_now(client, admin_token, buckets):
    adapter = FakeAdapter({})
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await client.get("/api/v1/infrastructure/storage/health", headers=_auth(admin_token))
        adapter.observations[RESULTS] = ContainerObservation(presence=ContainerPresence.FORBIDDEN)
        resp = await client.post("/api/v1/infrastructure/storage/health/check", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert {s["store"]: s["health"] for s in resp.json()["stores"]}["results"] == "no_access"


@pytest.mark.asyncio
async def test_confirm_buckets_records_the_baseline(client, admin_token, buckets):
    adapter = FakeAdapter({RAW: PRESENT})
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/v1/infrastructure/storage/identity/confirm",
            json={"stores": ["raw"], "reason": "Our original bucket", "confirm": True},
            headers=_auth(admin_token),
        )
        health = await client.get("/api/v1/infrastructure/storage/health", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert {s["store"]: (s["health"], s["basis"]) for s in health.json()["stores"]}["raw"] == ("available", "enrolled")


@pytest.mark.asyncio
async def test_confirm_buckets_needs_the_checkbox(client, admin_token, buckets):
    with patch("app.adapters.registry.get_storage_adapter", return_value=FakeAdapter({RAW: PRESENT})):
        resp = await client.post(
            "/api/v1/infrastructure/storage/identity/confirm",
            json={"stores": ["raw"], "reason": "Our original bucket", "confirm": False},
            headers=_auth(admin_token),
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_confirm_buckets_refuses_another_installations_bucket(client, admin_token, buckets):
    other = ContainerObservation(presence=ContainerPresence.PRESENT, owner="other-account")
    with patch("app.adapters.registry.get_storage_adapter", return_value=FakeAdapter({RAW: other})):
        resp = await client.post(
            "/api/v1/infrastructure/storage/identity/confirm",
            json={"stores": ["raw"], "reason": "Mine", "confirm": True},
            headers=_auth(admin_token),
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_comp_bio_cannot_confirm_buckets(client, comp_bio_token, buckets):
    resp = await client.post(
        "/api/v1/infrastructure/storage/identity/confirm",
        json={"stores": ["raw"], "reason": "Mine", "confirm": True},
        headers=_auth(comp_bio_token),
    )
    assert resp.status_code == 403
