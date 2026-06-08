"""Phase 9G: centralized logging goes through a BAL LogSinkProvider."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.observability import create_log_sink_provider
from app.adapters.observability.base import LogSinkProvider
from app.adapters.observability.gcp import GcpLogSinkProvider


def test_factory_returns_gcp_provider_by_default():
    provider = create_log_sink_provider("proj")
    assert isinstance(provider, GcpLogSinkProvider)
    assert isinstance(provider, LogSinkProvider)


def test_unknown_log_sink_backend_raises():
    with pytest.raises(ValueError):
        create_log_sink_provider("proj", backend="loki")


def test_build_handler_returns_cloud_handler():
    mock_client = MagicMock()
    mock_handler = MagicMock(spec=logging.Handler)
    mock_handler.level = logging.NOTSET
    mock_client.get_default_handler.return_value = mock_handler

    with patch("app.adapters.observability.gcp.cloud_logging") as mod:
        mod.Client.return_value = mock_client
        handler = create_log_sink_provider("proj", "creds").build_handler(debug=False)

    assert handler is mock_handler
    mod.Client.assert_called_once_with(project="proj", credentials="creds")


def test_build_handler_returns_none_when_sdk_missing():
    with patch("app.adapters.observability.gcp.cloud_logging", None):
        handler = create_log_sink_provider("proj").build_handler()
    assert handler is None


def test_build_handler_returns_none_on_client_error():
    with patch("app.adapters.observability.gcp.cloud_logging") as mod:
        mod.Client.side_effect = Exception("bad creds")
        handler = create_log_sink_provider("proj").build_handler()
    assert handler is None
