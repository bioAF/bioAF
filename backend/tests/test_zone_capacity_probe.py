"""Tests for the GCE zone-capacity probe (app.adapters.work_nodes.gce_capacity).

The probe selects a zone with e2-medium (or specified type) capacity by
attempting a throwaway instance insert in each candidate zone. The first
zone whose insert operation does not return a stockout error wins; any
created probe instance is deleted before returning.

These tests build fake operations that mirror the SHAPE of the real
google-cloud-compute ``ExtendedOperation``: ``error_code`` and
``error_message`` are PROPERTIES (not methods) and the specific GCE
error codes (``ZONE_RESOURCE_POOL_EXHAUSTED`` etc.) live in
``op._extended_operation.error.errors[].code``. An earlier iteration of
this test used a hand-rolled fake with method-style accessors, which
passed unit tests but blew up in production with
``TypeError: 'int' object is not callable`` the first time the real
SDK was hit. Hence: use the real proto types for the underlying
operation here so the test exercises the real access pattern.
"""

from __future__ import annotations

from typing import Any

import pytest
from google.cloud.compute_v1.types import Errors as ProtoErrorsItem
from google.cloud.compute_v1.types import Operation as ProtoOperation

from app.adapters.work_nodes import gce_capacity as zone_capacity_probe
from app.adapters.work_nodes.gce_capacity import (
    AllZonesExhaustedError,
    probe_zones,
)


class _FakeExtendedOperation:
    """Stand-in for compute_v1's ``_CustomOperation`` (ExtendedOperation).

    Mirrors the real SDK's surface: ``error_code`` and ``error_message``
    are properties that delegate to the underlying ``Operation`` proto's
    ``http_error_status_code`` / ``http_error_message`` fields. The
    specific GCE error codes live on ``_extended_operation.error.errors``.

    ``result()`` raises ``GoogleAPICallError`` when an HTTP-level error
    is set, matching the real SDK's behaviour.
    """

    def __init__(
        self,
        *,
        gce_error_codes: list[str] | None = None,
        http_status: int = 0,
        http_message: str = "",
    ) -> None:
        underlying = ProtoOperation()
        underlying.http_error_status_code = http_status
        if http_message:
            underlying.http_error_message = http_message
        for code in gce_error_codes or []:
            underlying.error.errors.append(ProtoErrorsItem(code=code, message=f"simulated {code}"))
        self._extended_operation = underlying

    @property
    def error_code(self) -> int:
        return self._extended_operation.http_error_status_code

    @property
    def error_message(self) -> str:
        return self._extended_operation.http_error_message

    def result(self, timeout: float | None = None) -> None:
        if self.error_code or self.error_message:
            from google.api_core.exceptions import GoogleAPICallError

            raise GoogleAPICallError(self.error_message or f"err {self.error_code}")
        return None


class _FakeInstancesClient:
    """In-memory stand-in for google.cloud.compute_v1.InstancesClient.

    Each entry in ``zone_outcomes`` is a list of GCE error codes for
    that zone (e.g. ``["ZONE_RESOURCE_POOL_EXHAUSTED"]``). Empty list =
    successful insert.
    """

    def __init__(self, zone_outcomes: dict[str, list[str]]) -> None:
        self._zone_outcomes = zone_outcomes
        self.inserted: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def insert(self, *, project: str, zone: str, instance_resource: Any) -> _FakeExtendedOperation:
        self.inserted.append((zone, instance_resource.name))
        codes = self._zone_outcomes.get(zone, [])
        # If any code is set, mirror the real SDK by also setting an
        # http status. 503 covers GCE_STOCKOUT in the real responses.
        http_status = 503 if codes else 0
        return _FakeExtendedOperation(
            gce_error_codes=codes,
            http_status=http_status,
        )

    def delete(self, *, project: str, zone: str, instance: str) -> _FakeExtendedOperation:
        self.deleted.append((zone, instance))
        return _FakeExtendedOperation()


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
    """Unrecognized errors (quota, permission) should not be swallowed.

    The real SDK reports these via the same error.errors[] list with a
    different code, OR via a top-level http_error_status_code without
    any specific GCE code. We test the latter shape here since it's the
    case that previously confused the probe into thinking the operation
    had succeeded.
    """
    patch_client({"us-central1-a": ["QUOTA_EXCEEDED"]})

    with pytest.raises(RuntimeError) as exc:
        probe_zones(
            zones=["us-central1-a"],
            project_id="bioaf-test",
            credentials=object(),
        )

    assert "QUOTA_EXCEEDED" in str(exc.value)


def test_probe_handles_http_error_with_no_gce_code(patch_client, monkeypatch):
    """An HTTP error (403, 500) with no GCE-specific code surfaces as RuntimeError.

    This is the failure mode that bit us in prod the first time: the
    probe must not treat ``op.error_code = 503`` with no
    ``error.errors[]`` entries as "operation succeeded."
    """
    # Build a fake client that returns an operation with http_status but
    # no specific GCE error codes (i.e. error.errors[] is empty).
    fake_op = _FakeExtendedOperation(http_status=500, http_message="Internal Server Error")

    class _BrokenClient:
        def __init__(self):
            self.inserted = []

        def insert(self, *, project, zone, instance_resource):
            self.inserted.append((zone, instance_resource.name))
            return fake_op

        def delete(self, *, project, zone, instance):
            return _FakeExtendedOperation()

    broken = _BrokenClient()
    monkeypatch.setattr(zone_capacity_probe, "_build_instances_client", lambda credentials: broken)

    with pytest.raises(RuntimeError) as exc:
        probe_zones(
            zones=["us-central1-a"],
            project_id="bioaf-test",
            credentials=object(),
        )

    assert "500" in str(exc.value) or "Internal Server Error" in str(exc.value)
