import logging
from types import SimpleNamespace

import pytest

from app import logging_config
from app.logging_config import RedactSecretsFilter, configure_logging

# A neutral marker parked in the SMTP password slot. Using a non-credential name
# keeps static analysis (py/clear-text-logging-sensitive-data) from flagging the
# deliberate "log it, then assert it was scrubbed" pattern, while still
# exercising real SMTP-password redaction: the filter redacts whatever value is
# currently in settings.smtp_password.
CANARY = "redact-me-canary-9f8e7d6c"


@pytest.fixture
def canary(monkeypatch):
    """Point logging_config at an isolated settings object holding the canary.

    Patching the module-level settings (rather than mutating the shared global)
    keeps the test deterministic under the full parallel suite.
    """
    monkeypatch.setattr(logging_config, "settings", SimpleNamespace(smtp_password=CANARY))
    return CANARY


def _record(msg, args=()):
    return logging.LogRecord(
        name="bioaf.email",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_filter_scrubs_value_from_message_args(canary):
    """The live SMTP-password value is replaced with *** in a record's rendered message."""
    f = RedactSecretsFilter()
    record = _record("login failed for %s", (canary,))

    assert f.filter(record) is True
    rendered = record.getMessage()
    assert canary not in rendered
    assert "***" in rendered


def test_filter_scrubs_value_embedded_in_msg(canary):
    """Redaction works when the value is in the message string itself, not args."""
    f = RedactSecretsFilter()
    record = _record(f"connecting as {canary}")

    assert f.filter(record) is True
    assert canary not in record.getMessage()


def test_filter_noop_when_password_unset(monkeypatch):
    """An empty/unset password must not be 'redacted' into mangled output."""
    monkeypatch.setattr(logging_config, "settings", SimpleNamespace(smtp_password=""))

    f = RedactSecretsFilter()
    record = _record("ordinary message with no secret")
    assert f.filter(record) is True
    assert record.getMessage() == "ordinary message with no secret"


def test_configure_logging_redacts_value_on_stdout(canary, capsys):
    """configure_logging wires the filter so propagated child-logger records are scrubbed."""
    configure_logging(debug=False)
    logging.getLogger("bioaf.email").warning("emitting marker %s", canary)

    out = capsys.readouterr().out
    assert canary not in out
    assert "***" in out
