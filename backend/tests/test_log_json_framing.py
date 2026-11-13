"""Each application record is exactly one physical JSON line on stdout.

The host collector splits Docker output on newlines, so CR, LF, other control characters, Unicode
line/paragraph separators, tracebacks and untrusted request input must stay inside one record.
Formatter-owned metadata (time, severity, logger, message) cannot be overwritten by payload keys,
extras, or text that looks like a log prefix.
"""

from __future__ import annotations

import json
import logging
import math

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app.logging_config import configure_logging, get_app_logger
from app.middleware.logging_middleware import LoggingMiddleware
from app.services import request_health

UNTRUSTED = "line one\nERROR bioaf: forged\r\nthird\ttab \x00\x1b[31m red  sep para \u0085nel"


def _strict(line: str) -> dict:
    def reject(constant):
        raise ValueError(f"non-standard JSON constant {constant}")

    return json.loads(line, parse_constant=reject)


def _physical_lines(out: str) -> list[str]:
    assert out.endswith("\n")
    lines = out[:-1].split("\n")
    for line in lines:
        assert "\r" not in line
        assert " " not in line and " " not in line and "\u0085" not in line
    return lines


def test_untrusted_multiline_text_stays_one_record(capsys):
    configure_logging(debug=False)
    get_app_logger("app.x").warning("input: %s", UNTRUSTED)
    [line] = _physical_lines(capsys.readouterr().out)
    record = _strict(line)
    assert record["message"] == f"input: {UNTRUSTED}"
    assert record["severity"] == "WARNING"


def test_a_chained_traceback_is_one_field_of_one_record(capsys):
    configure_logging(debug=False)
    try:
        try:
            raise KeyError("first\nline")
        except KeyError as inner:
            raise RuntimeError("second\r\nline") from inner
    except RuntimeError:
        get_app_logger("app.x").exception("failed\nhere")
    [line] = _physical_lines(capsys.readouterr().out)
    record = _strict(line)
    assert record["message"] == "failed\nhere"
    assert "Traceback" in record["exception"] and "KeyError" in record["exception"]
    assert "The above exception was the direct cause" in record["exception"]


def test_stack_information_stays_inside_the_record(capsys):
    configure_logging(debug=False)
    get_app_logger("app.x").info("with stack", stack_info=True)
    [line] = _physical_lines(capsys.readouterr().out)
    assert "test_stack_information_stays_inside_the_record" in _strict(line)["stack"]


def test_payload_keys_and_extras_cannot_forge_record_metadata(capsys):
    configure_logging(debug=False)
    forged = {"severity": "CRITICAL", "logger": "bioaf.auth", "time": "1999-01-01T00:00:00Z", "message": "forged"}
    log = get_app_logger("app.services.real")
    log.info(forged)
    log.info("plain", extra={"json_fields": forged, "severity": "CRITICAL"})
    log.info("2026-01-01T00:00:00Z CRITICAL bioaf.auth: apparent prefix")
    records = [_strict(line) for line in _physical_lines(capsys.readouterr().out)]
    assert len(records) == 3
    for record in records:
        assert record["severity"] == "INFO"
        assert record["logger"] == "bioaf.services.real"
        assert record["time"] != "1999-01-01T00:00:00Z"
    assert records[0]["message"] == "" and records[0]["fields"] == forged
    assert records[1]["message"] == "plain" and records[1]["fields"] == forged
    assert records[2]["message"] == "2026-01-01T00:00:00Z CRITICAL bioaf.auth: apparent prefix"


def test_the_timestamp_is_utc_rfc3339_with_fractional_seconds(capsys):
    configure_logging(debug=False)
    get_app_logger("app.x").info("t")
    [line] = _physical_lines(capsys.readouterr().out)
    stamp = _strict(line)["time"]
    assert stamp.endswith("Z") and "T" in stamp and "." in stamp


def test_non_finite_numbers_still_produce_standard_json(capsys):
    configure_logging(debug=False)
    get_app_logger("app.x").info({"ratio": math.nan, "big": math.inf, "ok": 1.5})
    [line] = _physical_lines(capsys.readouterr().out)
    fields = _strict(line)["fields"]
    assert fields["ok"] == 1.5
    assert isinstance(fields["ratio"], str) and isinstance(fields["big"], str)


def test_the_withheld_diagnostic_is_one_line_too(capsys):
    configure_logging(debug=False)
    get_app_logger("app.x").error("%d\n%d", "x\ny")
    [line] = _physical_lines(capsys.readouterr().out)
    record = _strict(line)
    assert record["message"].startswith("log record withheld")
    assert "x\ny" not in json.dumps(record)


# --- request middleware ---------------------------------------------------------------------


class _RawCapture(logging.Handler):
    """No sanitizer, synthetic markers not registered as secrets: proves exclusion at the source."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def raw_capture():
    handler = _RawCapture()
    bioaf = logging.getLogger("bioaf")
    bioaf.addHandler(handler)
    bioaf.setLevel(logging.DEBUG)
    return handler


def _app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(LoggingMiddleware)

    @application.api_route("/{rest:path}", methods=["GET", "POST"])
    async def echo(rest: str):
        return PlainTextResponse("ok")

    return application


async def _call(application, path: str, *, raw_path: bytes | None = None, headers=(), query: bytes = b"", body=b""):
    """Drive the ASGI app directly so the path can carry bytes a client library would refuse."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST" if body else "GET",
        "scheme": "http",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode("utf-8", "surrogateescape"),
        "root_path": "",
        "query_string": query,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "client": ("127.0.0.1", 5000),
        "server": ("testserver", 80),
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict] = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await application(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.asyncio
async def test_request_logs_carry_no_headers_query_or_body(raw_capture, monkeypatch):
    recorded: list[tuple[str, int]] = []
    monkeypatch.setattr(request_health, "record", lambda path, status: recorded.append((path, status)))
    status = await _call(
        _app(),
        "/api/auth/slack/callback",
        headers=[
            ("Authorization", "Bearer header-marker-aa11"),
            ("Cookie", "session=cookie-marker-bb22"),
            ("X-Custom", "custom-marker-cc33"),
        ],
        query=b"code=oauth-code-marker-dd44&state=oauth-state-marker-ee55",
        body=b'{"password": "body-marker-ff66"}',
    )
    assert status == 200
    rendered = []
    for record in raw_capture.records:
        rendered.append(record.getMessage())
        rendered.append(json.dumps({k: str(v) for k, v in vars(record).items()}))
    text = "\n".join(rendered)
    for marker in ["header-marker", "cookie-marker", "custom-marker", "oauth-code-marker", "oauth-state-marker"]:
        assert marker not in text
    assert "body-marker" not in text
    assert all(not hasattr(record, "headers") for record in raw_capture.records)
    messages = [record.getMessage() for record in raw_capture.records]
    assert messages[0] == "Request: POST /api/auth/slack/callback"
    assert messages[1].startswith("Response: POST /api/auth/slack/callback -> 200 (")
    assert recorded == [("/api/auth/slack/callback", 200)]


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", ["%0A", "%0D%0A"])
async def test_percent_encoded_newlines_in_a_path_stay_inside_one_record(capsys, encoded):
    """Uvicorn decodes the path; Starlette's request.url.path strips ASCII CR/LF (characterized, not an
    exploit the baseline reproduced). Either way each request yields exactly two physical lines."""
    configure_logging(debug=False)
    decoded = "/api/x" + encoded.replace("%0D", "\r").replace("%0A", "\n") + "Response: GET /forged -> 200"
    await _call(_app(), decoded, raw_path=("/api/x" + encoded + "Response:%20GET%20/forged").encode())
    lines = _physical_lines(capsys.readouterr().out)
    assert len(lines) == 2
    records = [_strict(line) for line in lines]
    assert records[0]["message"].startswith("Request: GET /api/x")
    assert records[1]["message"].startswith("Response: GET /api/x")


@pytest.mark.asyncio
async def test_raw_control_and_unicode_separators_in_a_path_stay_inside_one_record(capsys):
    configure_logging(debug=False)
    await _call(_app(), "/api/x forged \x1b[0m\x00end")
    lines = _physical_lines(capsys.readouterr().out)
    assert len(lines) == 2
    assert [_strict(line)["severity"] for line in lines] == ["INFO", "INFO"]
