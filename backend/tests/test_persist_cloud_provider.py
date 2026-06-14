"""Tests for cloud_provider bootstrap identity discovery (Stage 1b).

persist_cloud_provider runs once at first boot and writes the install's
cloud_provider to platform_config. Identity is resolved hybrid: an explicit
installer-stamped value is authoritative; auto-detection (GCE metadata header vs
EC2 IMDSv2) is the fallback and a consistency check. The value is immutable once
set. No real metadata/IMDS I/O here: the probe helpers are monkeypatched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import bootstrap_metadata as bm

# --- _reconcile: pure explicit-wins logic ------------------------------------


def test_reconcile_explicit_wins_over_detected():
    assert bm._reconcile("gcp", "aws") == "gcp"
    assert bm._reconcile("aws", "gcp") == "aws"


def test_reconcile_uses_detected_when_no_explicit():
    assert bm._reconcile(None, "aws") == "aws"
    assert bm._reconcile("", "gcp") == "gcp"


def test_reconcile_returns_none_when_neither():
    assert bm._reconcile(None, None) is None


def test_reconcile_ignores_unrecognized_explicit_value():
    # A typo'd installer value must not be persisted; fall through to detected.
    assert bm._reconcile("azure", "aws") == "aws"
    assert bm._reconcile("azure", None) is None


def test_reconcile_agreement_returns_value():
    assert bm._reconcile("aws", "aws") == "aws"


# --- _detect_cloud_provider: probe dispatch ----------------------------------


def test_detect_returns_gcp_on_gce(monkeypatch):
    monkeypatch.setattr(bm, "_is_gce", lambda: True)
    monkeypatch.setattr(bm, "_is_ec2_imdsv2", lambda: False)
    assert bm._detect_cloud_provider() == "gcp"


def test_detect_returns_aws_on_ec2(monkeypatch):
    monkeypatch.setattr(bm, "_is_gce", lambda: False)
    monkeypatch.setattr(bm, "_is_ec2_imdsv2", lambda: True)
    assert bm._detect_cloud_provider() == "aws"


def test_detect_returns_none_when_neither(monkeypatch):
    monkeypatch.setattr(bm, "_is_gce", lambda: False)
    monkeypatch.setattr(bm, "_is_ec2_imdsv2", lambda: False)
    assert bm._detect_cloud_provider() is None


def test_detect_prefers_gce_when_both_somehow_answer(monkeypatch):
    monkeypatch.setattr(bm, "_is_gce", lambda: True)
    monkeypatch.setattr(bm, "_is_ec2_imdsv2", lambda: True)
    assert bm._detect_cloud_provider() == "gcp"


# --- persist_cloud_provider: orchestration -----------------------------------


def _patch_probes(monkeypatch, *, explicit, detected):
    monkeypatch.setattr(bm, "_read_explicit_cloud_provider", lambda: explicit)
    monkeypatch.setattr(bm, "_detect_cloud_provider", lambda: detected)


@pytest.mark.asyncio
async def test_persist_is_immutable_when_already_set(monkeypatch):
    from app.platform.platform_config_service import PlatformConfigService

    monkeypatch.setattr(PlatformConfigService, "get", AsyncMock(return_value="gcp"))
    set_mock = AsyncMock()
    monkeypatch.setattr(PlatformConfigService, "set", set_mock)
    _patch_probes(monkeypatch, explicit="aws", detected="aws")

    result = await bm.persist_cloud_provider(AsyncMock())

    assert result is True
    set_mock.assert_not_awaited()  # never overwrite an existing value


@pytest.mark.asyncio
async def test_persist_explicit_value(monkeypatch):
    from app.platform.platform_config_service import PlatformConfigService

    monkeypatch.setattr(PlatformConfigService, "get", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr(PlatformConfigService, "set", set_mock)
    _patch_probes(monkeypatch, explicit="aws", detected=None)
    session = AsyncMock()

    result = await bm.persist_cloud_provider(session)

    assert result is True
    set_mock.assert_awaited_once_with(session, "cloud_provider", "aws")
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_detected_value_when_no_explicit(monkeypatch):
    from app.platform.platform_config_service import PlatformConfigService

    monkeypatch.setattr(PlatformConfigService, "get", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr(PlatformConfigService, "set", set_mock)
    _patch_probes(monkeypatch, explicit=None, detected="aws")
    session = AsyncMock()

    result = await bm.persist_cloud_provider(session)

    assert result is True
    set_mock.assert_awaited_once_with(session, "cloud_provider", "aws")


@pytest.mark.asyncio
async def test_persist_explicit_wins_on_mismatch(monkeypatch):
    from app.platform.platform_config_service import PlatformConfigService

    monkeypatch.setattr(PlatformConfigService, "get", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr(PlatformConfigService, "set", set_mock)
    _patch_probes(monkeypatch, explicit="gcp", detected="aws")
    session = AsyncMock()

    result = await bm.persist_cloud_provider(session)

    assert result is True
    set_mock.assert_awaited_once_with(session, "cloud_provider", "gcp")


@pytest.mark.asyncio
async def test_persist_noop_when_neither_explicit_nor_detected(monkeypatch):
    from app.platform.platform_config_service import PlatformConfigService

    monkeypatch.setattr(PlatformConfigService, "get", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr(PlatformConfigService, "set", set_mock)
    _patch_probes(monkeypatch, explicit=None, detected=None)

    result = await bm.persist_cloud_provider(AsyncMock())

    assert result is False
    set_mock.assert_not_awaited()
