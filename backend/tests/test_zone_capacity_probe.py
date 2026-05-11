"""Tests for app.services.zone_capacity_probe.

The probe selects a zone with e2-medium (or specified type) capacity by
attempting a throwaway instance insert in each candidate zone. The first
zone whose insert operation does not return a stockout error wins; any
created probe instance is deleted before returning.

These tests stub the compute client so no real GCP calls are made.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import zone_capacity_probe
from app.services.zone_capacity_probe import (
    AllZonesExhaustedError,
    probe_zones,
)


class _FakeOperation:
    """Mimics the operation object returned by compute.instances.insert."""

    def __init__(self, name: str, error_codes: list[str] | None = None) -> None:
        self.name = name
        self._error_codes = error_codes or []

    def error_code(self) -> str | None:
        return self._error_codes[0] if self._error_codes else None

    def error_message(self) -> str | None:
        if not self._error_codes:
            return None
        return f"simulated {self._error_codes[0]}"


class _FakeInstancesClient:
    """In-memory stand-in for google.cloud.compute_v1.InstancesClient."""

    def __init__(self, zone_outcomes: dict[str, list[str]]) -> None:
        self._zone_outcomes = zone_outcomes
        self.inserted: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def insert(self, *, project: str, zone: str, instance_resource: Any) -> _FakeOperation:
        self.inserted.append((zone, instance_resource.name))
        return _FakeOperation(
            name=f"op-insert-{zone}",
            error_codes=self._zone_outcomes.get(zone, []),
        )

    def delete(self, *, project: str, zone: str, instance: str) -> _FakeOperation:
        self.deleted.append((zone, instance))
        return _FakeOperation(name=f"op-delete-{zone}")


@pytest.fixture
def patch_client(monkeypatch):
    """Return a helper that swaps the compute client factory for a fake."""

    def _install(outcomes: dict[str, list[str]]) -> _FakeInstancesClient:
        fake = _FakeInstancesClient(outcomes)
        monkeypatch.setattr(
            zone_capacity_probe,
            "_build_instances_client",
            lambda credentials: fake,
        )
        return fake

    return _install


def test_probe_returns_first_zone_with_capacity(patch_client):
    """When the first zone is healthy, probe returns it and cleans up."""
    fake = patch_client({"us-central1-a": []})

    selected = probe_zones(
        zones=["us-central1-a", "us-central1-b", "us-central1-f"],
        project_id="bioaf-test",
        credentials=object(),
    )

    assert selected == "us-central1-a"
    # Probe inserted exactly one instance and deleted it.
    assert len(fake.inserted) == 1
    assert fake.inserted[0][0] == "us-central1-a"
    assert len(fake.deleted) == 1
    assert fake.deleted[0][0] == "us-central1-a"


def test_probe_skips_stocked_out_zones(patch_client):
    """ZONE_RESOURCE_POOL_EXHAUSTED in zone A should advance to zone B."""
    fake = patch_client(
        {
            "us-central1-a": ["ZONE_RESOURCE_POOL_EXHAUSTED"],
            "us-central1-b": [],
        }
    )

    selected = probe_zones(
        zones=["us-central1-a", "us-central1-b"],
        project_id="bioaf-test",
        credentials=object(),
    )

    assert selected == "us-central1-b"
    # Both zones were probed in order.
    assert [z for z, _ in fake.inserted] == ["us-central1-a", "us-central1-b"]
    # Only the winning zone's instance was deleted (failed insert leaves
    # nothing behind in the real API).
    assert [z for z, _ in fake.deleted] == ["us-central1-b"]


def test_probe_recognizes_stockout_alias(patch_client):
    """GCE_STOCKOUT (alternate error code) is also treated as no capacity."""
    fake = patch_client(
        {
            "us-central1-f": ["GCE_STOCKOUT"],
            "us-central1-a": [],
        }
    )

    selected = probe_zones(
        zones=["us-central1-f", "us-central1-a"],
        project_id="bioaf-test",
        credentials=object(),
    )

    assert selected == "us-central1-a"
    assert [z for z, _ in fake.inserted] == ["us-central1-f", "us-central1-a"]


def test_probe_raises_when_all_zones_exhausted(patch_client):
    """If every zone is stocked out, raise AllZonesExhaustedError."""
    patch_client(
        {
            "us-central1-a": ["ZONE_RESOURCE_POOL_EXHAUSTED"],
            "us-central1-b": ["ZONE_RESOURCE_POOL_EXHAUSTED"],
            "us-central1-f": ["GCE_STOCKOUT"],
        }
    )

    with pytest.raises(AllZonesExhaustedError) as exc:
        probe_zones(
            zones=["us-central1-a", "us-central1-b", "us-central1-f"],
            project_id="bioaf-test",
            credentials=object(),
        )

    assert "us-central1-a" in str(exc.value)
    assert "us-central1-f" in str(exc.value)


def test_probe_non_stockout_errors_propagate(patch_client):
    """Unrecognized errors (quota, permission) should not be swallowed."""
    patch_client({"us-central1-a": ["QUOTA_EXCEEDED"]})

    with pytest.raises(RuntimeError) as exc:
        probe_zones(
            zones=["us-central1-a"],
            project_id="bioaf-test",
            credentials=object(),
        )

    assert "QUOTA_EXCEEDED" in str(exc.value)
