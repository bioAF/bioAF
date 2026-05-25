import logging

import pytest

from app.config import settings

SECRET = "TOPSECRETpw-12345"


@pytest.fixture
def smtp_password():
    """Set a known SMTP password in settings for the test, then restore it."""
    original = settings.smtp_password
    settings.smtp_password = SECRET
    yield SECRET
    settings.smtp_password = original


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


def test_filter_scrubs_smtp_password_from_message_args(smtp_password):
    """The live SMTP password is replaced with *** in a record's rendered message."""
    from app.logging_config import RedactSecretsFilter

    f = RedactSecretsFilter()
    record = _record("login failed for %s", (smtp_password,))

    assert f.filter(record) is True
    rendered = record.getMessage()
    assert smtp_password not in rendered
    assert "***" in rendered


def test_filter_scrubs_password_embedded_in_msg(smtp_password):
    """Redaction works when the secret is in the message string itself, not args."""
    from app.logging_config import RedactSecretsFilter

    f = RedactSecretsFilter()
    record = _record(f"connecting with password {smtp_password}")

    assert f.filter(record) is True
    assert smtp_password not in record.getMessage()


def test_filter_noop_when_password_unset():
    """An empty/unset password must not be 'redacted' into mangled output."""
    from app.logging_config import RedactSecretsFilter

    original = settings.smtp_password
    settings.smtp_password = ""
    try:
        f = RedactSecretsFilter()
        record = _record("ordinary message with no secret")
        assert f.filter(record) is True
        assert record.getMessage() == "ordinary message with no secret"
    finally:
        settings.smtp_password = original


def test_configure_logging_redacts_password_on_stdout(smtp_password, capsys):
    """configure_logging wires the filter so propagated child-logger records are scrubbed."""
    from app.logging_config import configure_logging

    configure_logging(debug=False)
    logging.getLogger("bioaf.email").warning("auth used password %s", smtp_password)

    out = capsys.readouterr().out
    assert smtp_password not in out
    assert "***" in out
