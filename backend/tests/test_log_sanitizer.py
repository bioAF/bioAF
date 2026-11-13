"""One known-secret policy, applied to what application sinks actually emit.

Known secrets are the live values of a fixed settings allowlist (SMTP password, JWT signing key, the
Slack client and signing secret slots, each at-rest encryption key, and the password in the database
URL). Values shorter than four characters and public placeholders are not eligible. Sanitization
covers the message, fresh and cached tracebacks (chained too), stack information, a mapping message,
and the supported structured fields (``json_fields``, ``labels``, ``http_request``). A record that
cannot be sanitized or formatted is replaced by a fixed diagnostic, never passed through raw.

All values are synthetic. ``logging_config.settings`` is replaced per test, so nothing touches the
shared settings object.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app import logging_config
from app.logging_config import RedactSecretsFilter, configure_logging, get_app_logger

SMTP = "smtp-canary-1a2b3c"
JWT = "jwt-canary-4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f90"
SLACK_CLIENT = "slack-client-canary-77aa"
SLACK_SIGNING = "slack-signing-canary-88bb"
KEY_A = "encKeyCanaryAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
KEY_B = "encKeyCanaryBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
DB_PASSWORD = "db/pass@word:canary#1"
DB_ENCODED = "db%2Fpass%40word%3Acanary%231"


def _settings(**overrides):
    values = dict(
        smtp_password=SMTP,
        jwt_secret_key=JWT,
        slack_client_secret=SLACK_CLIENT,
        slack_signing_secret=SLACK_SIGNING,
        encryption_keys=f"{KEY_A}, {KEY_B}",
        database_url=f"postgresql+asyncpg://bioaf_app:{DB_ENCODED}@db:5432/bioaf",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def secrets(monkeypatch):
    fake = _settings()
    monkeypatch.setattr(logging_config, "settings", fake)
    return fake


def _lines(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def _emitted(capsys) -> str:
    return capsys.readouterr().out


@pytest.mark.parametrize("value", [SMTP, JWT, SLACK_CLIENT, SLACK_SIGNING, KEY_A, KEY_B, DB_PASSWORD, DB_ENCODED])
def test_every_known_secret_is_absent_from_emitted_text(secrets, capsys, value):
    configure_logging(debug=False)
    get_app_logger("app.services.probe").warning("value=%s and inline %s", value, f"<{value}>")
    out = _emitted(capsys)
    assert value not in out
    assert "value=*** and inline <***>" in out


def test_the_full_database_url_keeps_its_non_secret_parts(secrets, capsys):
    configure_logging(debug=False)
    get_app_logger("app.services.probe").info("url %s", secrets.database_url)
    message = _lines(capsys)[0]["message"]
    assert DB_ENCODED not in message
    assert message == "url postgresql+asyncpg://bioaf_app:***@db:5432/bioaf"


def test_a_secret_manager_style_raw_password_in_the_url_is_covered(monkeypatch, capsys):
    raw = "rawSecretManagerPw99"
    monkeypatch.setattr(
        logging_config, "settings", _settings(database_url=f"postgresql+asyncpg://bioaf_app:{raw}@db:5432/bioaf")
    )
    configure_logging(debug=False)
    get_app_logger("app.x").info("pw %s", raw)
    assert raw not in _emitted(capsys)


@pytest.mark.parametrize(
    "url",
    ["postgresql+asyncpg://bioaf_app@db:5432/bioaf", "not a url :: %%% @@", "", "postgresql://:@/"],
)
def test_a_url_without_a_usable_password_is_handled_without_disclosure(monkeypatch, capsys, url):
    monkeypatch.setattr(logging_config, "settings", _settings(database_url=url))
    configure_logging(debug=False)
    get_app_logger("app.x").info("ordinary line")
    lines = _lines(capsys)
    assert [line["message"] for line in lines] == ["ordinary line"]


def test_short_and_empty_values_are_not_eligible(monkeypatch, capsys):
    monkeypatch.setattr(
        logging_config,
        "settings",
        _settings(
            smtp_password="abc",
            jwt_secret_key="",
            slack_client_secret="",
            slack_signing_secret="",
            encryption_keys="",
            database_url="postgresql+asyncpg://bioaf_app@localhost:5432/bioaf",
        ),
    )
    configure_logging(debug=False)
    get_app_logger("app.x").info("abc reset the password for the abc lab")
    assert _lines(capsys)[0]["message"] == "abc reset the password for the abc lab"


@pytest.mark.parametrize(
    "overrides",
    [
        {"smtp_password": "PaSsWoRd"},
        {"smtp_password": "change_me"},
        {"database_url": "postgresql+asyncpg://bioaf_app:password@db:5432/bioaf"},
        {"database_url": "postgresql+asyncpg://bioaf:CHANGE_ME@db:5432/bioaf"},
    ],
)
def test_a_live_credential_is_redacted_even_when_it_looks_like_a_placeholder(monkeypatch, capsys, overrides):
    """A configured value is a credential whatever it looks like; weak or example-like values included."""
    fake = _settings(**overrides)
    monkeypatch.setattr(logging_config, "settings", fake)
    value = overrides.get("smtp_password") or fake.database_url.split(":")[2].split("@")[0]
    configure_logging(debug=False)
    get_app_logger("app.x").info("configured credential=%s", value)
    assert value not in _lines(capsys)[0]["message"]


class _ChangingArgument:
    """Renders ordinary text the first time and a known secret afterwards."""

    def __init__(self) -> None:
        self.calls = 0

    def __str__(self) -> str:
        self.calls += 1
        return "ordinary" if self.calls == 1 else f"late {SMTP}"


def test_the_sanitized_rendering_is_the_one_every_sink_emits(secrets, capsys, caplog):
    configure_logging(debug=False)
    caplog.set_level(logging.INFO)
    seen: list[str] = []

    class Later(logging.Handler):
        def emit(self, record):
            seen.append(self.format(record))

    logging.getLogger("bioaf").addHandler(Later())
    get_app_logger("app.x").info("value %s", _ChangingArgument())
    assert _lines(capsys)[0]["message"] == "value ordinary"
    assert seen == ["value ordinary"]
    assert SMTP not in caplog.text and "value ordinary" in caplog.text


def test_overlapping_secrets_are_replaced_longest_first(monkeypatch, capsys):
    monkeypatch.setattr(logging_config, "settings", _settings(smtp_password="overlap", jwt_secret_key="overlap-longer"))
    configure_logging(debug=False)
    get_app_logger("app.x").info("a overlap-longer b overlap c")
    assert _lines(capsys)[0]["message"] == "a *** b *** c"


def test_values_are_read_at_emission_so_later_loads_and_rotation_apply(monkeypatch, capsys):
    fake = _settings(smtp_password="")
    monkeypatch.setattr(logging_config, "settings", fake)
    configure_logging(debug=False)
    log = get_app_logger("app.x")
    fake.smtp_password = "loaded-later-smtp"
    log.info("first loaded-later-smtp")
    fake.smtp_password = "rotated-smtp-value"
    log.info("second rotated-smtp-value")
    assert [line["message"] for line in _lines(capsys)] == ["first ***", "second ***"]


def _raise_chained():
    try:
        raise KeyError(f"inner {SLACK_CLIENT}")
    except KeyError as inner:
        raise RuntimeError(f"outer {SMTP}") from inner


def test_a_fresh_traceback_is_clean_in_the_first_sink(secrets, capsys):
    configure_logging(debug=False)
    try:
        _raise_chained()
    except RuntimeError:
        get_app_logger("app.x").exception("operation failed")
    [line] = _lines(capsys)
    assert line["message"] == "operation failed"
    assert SMTP not in line["exception"] and SLACK_CLIENT not in line["exception"]
    assert "RuntimeError: outer ***" in line["exception"]
    assert "KeyError" in line["exception"] and "_raise_chained" in line["exception"]


def test_a_cached_traceback_is_sanitized_too(secrets):
    record = logging.LogRecord("bioaf.x", logging.ERROR, __file__, 1, "failed", None, None)
    record.exc_text = f"Traceback ...\nValueError: {JWT}"
    assert RedactSecretsFilter().filter(record) is True
    assert JWT not in record.exc_text and "ValueError: ***" in record.exc_text


def test_stack_information_is_sanitized(secrets, capsys):
    configure_logging(debug=False)
    record = logging.LogRecord("bioaf.x", logging.WARNING, __file__, 1, "with stack", None, None)
    record.stack_info = f'Stack (most recent call last):\n  File "x.py", line 1, in f\n    use("{KEY_A}")'
    logging.getLogger("bioaf.x").handle(record)
    [line] = _lines(capsys)
    assert KEY_A not in line["stack"] and "x.py" in line["stack"]


def test_the_first_sink_is_safe_whichever_order_sinks_were_added(secrets, capsys):
    """A sanitizing sink ahead of stdout must itself see a clean traceback, not rely on stdout."""
    configure_logging(debug=False)
    seen: list[str] = []

    class First(logging.Handler):
        def emit(self, record):
            seen.append(self.format(record))

    first = First()
    first.addFilter(RedactSecretsFilter())
    bioaf = logging.getLogger("bioaf")
    bioaf.handlers.insert(0, first)
    try:
        _raise_chained()
    except RuntimeError:
        get_app_logger("app.x").exception("ordered")
    assert seen and SMTP not in seen[0] and SLACK_CLIENT not in seen[0]
    assert SMTP not in _emitted(capsys)


def test_records_propagating_beyond_the_application_sinks_stay_sanitized(secrets, caplog):
    configure_logging(debug=False)
    caplog.set_level(logging.INFO)
    get_app_logger("app.x").warning("propagated %s", SMTP)
    assert SMTP not in caplog.text
    assert "propagated ***" in caplog.text


def test_a_mapping_message_stays_structured_and_sanitized(secrets, capsys):
    configure_logging(debug=False)
    payload = {"event": "probe", "detail": f"token {SLACK_SIGNING}", "count": 3, "nested": {"k": [JWT, 1.5, None]}}
    get_app_logger("app.x").info(payload)
    [line] = _lines(capsys)
    assert line["fields"] == {"event": "probe", "detail": "token ***", "count": 3, "nested": {"k": ["***", 1.5, None]}}
    assert payload["detail"] == f"token {SLACK_SIGNING}", "the caller's mapping is not mutated"


def test_json_fields_labels_and_http_request_are_sanitized_in_place_of_their_values(secrets, capsys):
    configure_logging(debug=False)
    fields: dict[str, Any] = {"operation": "sync", "detail": {"inner": [f"x{SMTP}x"]}}
    get_app_logger("app.x").info(
        "structured",
        extra={
            "json_fields": fields,
            "labels": {"who": JWT},
            "http_request": {"requestUrl": f"/cb?code={KEY_B}"},
        },
    )
    [line] = _lines(capsys)
    assert line["fields"] == {"operation": "sync", "detail": {"inner": ["x***x"]}}
    assert fields["detail"]["inner"] == [f"x{SMTP}x"], "the caller's fields are not mutated"
    out = json.dumps(line)
    assert JWT not in out and KEY_B not in out


def test_dictionary_keys_are_sanitized_too(secrets, capsys):
    configure_logging(debug=False)
    get_app_logger("app.x").info({SMTP: "value"})
    assert SMTP not in _emitted(capsys)


def test_unsupported_and_cyclic_payloads_have_bounded_safe_handling(secrets, capsys):
    class Opaque:
        def __repr__(self):
            return f"Opaque({SMTP})"

        __str__ = __repr__

    cyclic: dict = {"name": "loop"}
    cyclic["self"] = cyclic
    deep: dict = {}
    cursor = deep
    for _ in range(200):
        cursor["d"] = {}
        cursor = cursor["d"]
    configure_logging(debug=False)
    get_app_logger("app.x").info("odd", extra={"json_fields": {"obj": Opaque(), "cyclic": cyclic, "deep": deep}})
    out = _emitted(capsys)
    assert SMTP not in out
    [line] = [json.loads(x) for x in out.splitlines()]
    assert line["fields"]["obj"] == "<unsupported Opaque>"
    assert line["fields"]["cyclic"]["self"] == "<cycle>"
    assert "<too deep>" in out


def test_a_record_that_cannot_be_formatted_is_replaced_by_a_safe_diagnostic(secrets, capsys):
    configure_logging(debug=False)
    get_app_logger("app.services.bad").error("count=%d for %s", f"not-a-number {SMTP}", "x")
    [line] = _lines(capsys)
    assert SMTP not in json.dumps(line)
    assert line["severity"] == "ERROR"
    assert line["logger"] == "bioaf.services.bad"
    assert line["message"].startswith("log record withheld")
    assert "TypeError" in line["message"]


def test_a_sanitizer_failure_withholds_the_record_without_raising_or_recursing(monkeypatch, capsys):
    def broken():
        raise RuntimeError("supplier failed")

    monkeypatch.setattr(logging_config, "_known_secrets", broken)
    configure_logging(debug=False)
    try:
        raise ValueError("unsafe detail")
    except ValueError:
        get_app_logger("app.x").exception("raw %s", "argument")
    [line] = _lines(capsys)
    assert line["message"].startswith("log record withheld")
    assert "RuntimeError" in line["message"]
    assert "argument" not in json.dumps(line) and "unsafe detail" not in json.dumps(line)
    assert line["severity"] == "ERROR" and line["logger"] == "bioaf.x"
    assert "exception" not in line and "fields" not in line
