"""The opt-in direct sink: what the real Google handler serializes, and who owns the handler.

Serialization uses a real ``CloudLoggingHandler`` (google-cloud-logging as locked) with an explicit
``Resource`` and a recording transport, supplied through the existing patched-client seam. Resource
detection, metadata and network access are forbidden, not assumed absent. Nothing here proves
authentication, agent ingestion or delivery to Cloud Logging.

Ownership: at most one application-owned direct handler; attaching again is a no-op; the cleanup
handle removes and closes only what its own attach installed; caller handlers are never touched.
"""

from __future__ import annotations

import json
import logging
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud.logging_v2.client import Client
from google.cloud.logging_v2.handlers import CloudLoggingHandler
from google.cloud.logging_v2.handlers.transports.base import Transport
from google.cloud.logging_v2.resource import Resource

from app import logging_config
from app.logging_config import attach_cloud_logging, configure_logging, get_app_logger

SMTP = "direct-smtp-canary-31x"
JWT = "direct-jwt-canary-0123456789abcdef0123456789abcdef"


class RecordingTransport(Transport):
    sent: list[dict] = []
    fail_send = False
    fail_close = False
    closed = 0

    def __init__(self, client, name, resource=None, **kwargs):
        self.client, self.name, self.resource = client, name, resource

    def send(self, record, message, **kwargs):
        if RecordingTransport.fail_send:
            raise ConnectionError("transport down")
        RecordingTransport.sent.append({"message": message, **kwargs})

    def flush(self):
        if RecordingTransport.fail_close:
            raise ConnectionError("flush failed")

    def close(self):
        RecordingTransport.closed += 1
        if RecordingTransport.fail_close:
            raise ConnectionError("close failed")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No metadata server, resource detection, or sockets; fresh transport recording."""
    RecordingTransport.sent = []
    RecordingTransport.fail_send = RecordingTransport.fail_close = False
    RecordingTransport.closed = 0

    def forbidden(*args, **kwargs):
        raise AssertionError("network or metadata access attempted")

    # Production configures the application level before any attach; these tests may skip stdout.
    logging.getLogger("bioaf").setLevel(logging.INFO)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("google.cloud.logging_v2.handlers.handlers.detect_resource", forbidden)
    monkeypatch.setattr(
        logging_config,
        "settings",
        SimpleNamespace(
            smtp_password=SMTP,
            jwt_secret_key=JWT,
            slack_client_secret="",
            slack_signing_secret="",
            encryption_keys="",
            database_url="",
        ),
    )


def _real_handler() -> CloudLoggingHandler:
    client = Client(project="offline-project", credentials=AnonymousCredentials(), _use_grpc=False)
    return CloudLoggingHandler(client, resource=Resource(type="global", labels={}), transport=RecordingTransport)


def _attach(handler=None):
    handler = handler or _real_handler()
    with patch("app.adapters.observability.gcp.cloud_logging.Client") as client_cls:
        client_cls.return_value.get_default_handler.return_value = handler
        detach = attach_cloud_logging("offline-project", None, debug=False)
    return handler, detach


def _direct_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger("bioaf").handlers if isinstance(h, CloudLoggingHandler)]


# --- serialization --------------------------------------------------------------------------


def test_a_fresh_traceback_is_clean_in_the_serialized_direct_entry():
    """Direct sink alone (no stdout ahead of it), so it must sanitize for itself."""
    _attach()
    try:
        raise RuntimeError(f"connect failed for {SMTP}")
    except RuntimeError:
        get_app_logger("app.x").exception("direct failure")
    entry = RecordingTransport.sent[-1]
    assert isinstance(entry["message"], str)
    assert SMTP not in entry["message"]
    assert "direct failure" in entry["message"] and "RuntimeError: connect failed for ***" in entry["message"]


def test_structured_payloads_stay_structured_and_sanitized_in_the_direct_entry():
    _attach()
    log = get_app_logger("app.x")
    log.info({"event": "probe", "detail": f"t {JWT}", "n": 2})
    log.info(
        "with fields",
        extra={
            "json_fields": {"op": "sync", "inner": [SMTP]},
            "labels": {"tenant": f"x{JWT}"},
            "http_request": {"requestUrl": f"/cb?code={SMTP}", "status": 500},
        },
    )
    mapping, fielded = RecordingTransport.sent[-2:]
    assert mapping["message"] == {"event": "probe", "detail": "t ***", "n": 2}
    assert fielded["message"] == {"op": "sync", "inner": ["***"], "message": "with fields"}
    assert fielded["labels"]["tenant"] == "x***"
    assert fielded["labels"]["python_logger"] == "bioaf.x"
    assert fielded["http_request"] == {"requestUrl": "/cb?code=***", "status": 500}
    assert SMTP not in json.dumps(RecordingTransport.sent, default=str)
    assert JWT not in json.dumps(RecordingTransport.sent, default=str)


def test_the_direct_sink_and_stdout_agree_and_stdout_is_kept(capsys):
    configure_logging(debug=False)
    _attach()
    get_app_logger("app.x").warning("both %s", SMTP)
    out = capsys.readouterr().out
    assert SMTP not in out and "both ***" in out
    assert RecordingTransport.sent[-1]["message"] == "both ***"


def test_an_adopted_module_reaches_the_direct_sink_at_production_levels():
    configure_logging(debug=False)
    _attach()
    log = get_app_logger("app.services.work_dir_reaper")
    log.debug("hidden")
    log.info("shown")
    adopted = [e for e in RecordingTransport.sent if e["labels"]["python_logger"] == "bioaf.services.work_dir_reaper"]
    assert [e["message"] for e in adopted] == ["shown"]


# --- ownership ------------------------------------------------------------------------------


def test_attaching_twice_keeps_one_direct_handler():
    first, detach_first = _attach()
    second, detach_second = _attach()
    assert _direct_handlers() == [first]
    get_app_logger("app.x").info("once")
    assert [e["message"] for e in RecordingTransport.sent if e["message"] == "once"] == ["once"]
    detach_second()
    assert _direct_handlers() == [first], "a no-op attach's cleanup must not detach the owner"
    detach_first()
    assert _direct_handlers() == []


def test_cleanup_removes_and_closes_its_handler_once():
    handler, detach = _attach()
    detach()
    detach()
    assert _direct_handlers() == []
    assert RecordingTransport.closed == 1
    sent_before = len(RecordingTransport.sent)
    get_app_logger("app.x").info("after cleanup")
    assert len(RecordingTransport.sent) == sent_before


def test_an_older_cleanup_cannot_detach_a_later_owner():
    _first, detach_first = _attach()
    detach_first()
    second, _detach_second = _attach()
    detach_first()
    assert _direct_handlers() == [second]


def test_reconfiguring_stdout_keeps_the_direct_sink_and_caller_handlers(capsys):
    external = logging.StreamHandler()
    bioaf = logging.getLogger("bioaf")
    bioaf.addHandler(external)
    configure_logging(debug=False)
    handler, detach = _attach()
    configure_logging(debug=True)
    configure_logging(debug=False)
    assert handler in bioaf.handlers and external in bioaf.handlers
    stdout_handlers = [h for h in bioaf.handlers if isinstance(h.formatter, logging_config.JsonFormatter)]
    assert len(stdout_handlers) == 1
    detach()
    assert external in bioaf.handlers
    assert external.stream is not None


def test_an_unavailable_sink_leaves_stdout_and_returns_no_cleanup(capsys):
    configure_logging(debug=False)
    with patch("app.adapters.observability.gcp.cloud_logging", None):
        detach = attach_cloud_logging("offline-project", None, debug=False)
    assert detach is None
    assert _direct_handlers() == []
    out = capsys.readouterr().out
    assert "stdout only" in out


def test_a_failing_transport_does_not_break_the_caller_or_stdout(capsys):
    configure_logging(debug=False)
    _attach()
    RecordingTransport.fail_send = True
    get_app_logger("app.x").error("still logged %s", SMTP)
    captured = capsys.readouterr()
    assert "still logged ***" in captured.out
    [diagnostic] = [json.loads(line) for line in captured.err.splitlines() if "direct log sink" in line]
    assert diagnostic["message"] == "direct log sink emit failed (error_type=ConnectionError)"
    assert SMTP not in captured.err and "still logged" not in captured.err


def test_a_failing_flush_or_close_does_not_raise_from_cleanup():
    _handler, detach = _attach()
    RecordingTransport.fail_close = True
    detach()
    assert _direct_handlers() == []
