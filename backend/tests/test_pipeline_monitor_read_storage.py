"""Parity tests for pipeline_monitor_service._read_gcs_text after it was routed
through the storage adapter (Phase 3).

Behavior contract preserved: returns the object text on success, None for a
non-gs URI, None when the object is missing, and None on any other error.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.adapters.models import StorageObjectNotFound
from app.services import pipeline_monitor_service
from app.services.pipeline_monitor_service import _read_gcs_text


@pytest.mark.asyncio
async def test_returns_text_via_adapter():
    adapter = AsyncMock()
    adapter.read_text.return_value = "log contents"
    with patch.object(pipeline_monitor_service, "get_storage_adapter", return_value=adapter):
        result = await _read_gcs_text("gs://bucket/path/log.txt")
    assert result == "log contents"
    adapter.read_text.assert_awaited_once_with("gs://bucket/path/log.txt")


@pytest.mark.asyncio
async def test_non_gs_uri_returns_none_without_calling_adapter():
    adapter = AsyncMock()
    with patch.object(pipeline_monitor_service, "get_storage_adapter", return_value=adapter):
        assert await _read_gcs_text("/local/path") is None
    adapter.read_text.assert_not_called()


@pytest.mark.asyncio
async def test_missing_object_returns_none():
    adapter = AsyncMock()
    adapter.read_text.side_effect = StorageObjectNotFound("gs://b/x")
    with patch.object(pipeline_monitor_service, "get_storage_adapter", return_value=adapter):
        assert await _read_gcs_text("gs://b/x") is None


@pytest.mark.asyncio
async def test_other_error_returns_none():
    adapter = AsyncMock()
    adapter.read_text.side_effect = RuntimeError("boom")
    with patch.object(pipeline_monitor_service, "get_storage_adapter", return_value=adapter):
        assert await _read_gcs_text("gs://b/x") is None
