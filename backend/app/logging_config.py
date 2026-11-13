"""Application logging: one hierarchy, one sanitizer, single-line JSON on stdout.

Every application module logs under ``bioaf`` (``get_app_logger(__name__)`` maps ``app.x`` to
``bioaf.x``). ``configure_logging`` installs the one application-owned stdout handler, which writes
each record as one JSON object on one line. Root, Uvicorn, SQLAlchemy, Alembic and cloud SDK loggers
are left alone, and ``bioaf`` keeps propagating so capture and embedding hosts still see records.
"""

import json
import logging
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.config import settings

APP_LOGGER = "bioaf"
_GCE_METADATA_URL = "http://169.254.169.254/computeMetadata/v1/project/project-id"
_METADATA_HEADERS = {"Metadata-Flavor": "Google"}

# Replacement token for redacted secrets.
_REDACTION = "***"
# Don't redact values shorter than this: a trivially short "secret" would risk
# mangling unrelated text, and offers little protection anyway.
_MIN_SECRET_LEN = 4


def _live_secrets() -> list[str]:
    """Plaintext secret values that must never reach a log sink.

    Read from the live settings singleton so the current value is redacted.
    Currently the SMTP password; extend this list as other in-memory secrets
    need the same protection.
    """
    candidates = [settings.smtp_password]
    return [s for s in candidates if isinstance(s, str) and len(s) >= _MIN_SECRET_LEN]


class RedactSecretsFilter(logging.Filter):
    """Scrub known plaintext secrets from log records before they are emitted.

    Defense in depth: even if a log call, exception message, or third-party
    output routed through our logger includes a live secret (e.g. the SMTP
    password), it is replaced with ``***``. Attach to handlers rather than a
    logger so it also runs on records propagated up from child loggers.

    Never raises and never drops a record: on any error it lets the original
    record through unchanged rather than losing the log line.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            secrets = _live_secrets()
            if not secrets:
                return True
            message = record.getMessage()
            redacted = message
            for secret in secrets:
                redacted = redacted.replace(secret, _REDACTION)
            if redacted != message:
                record.msg = redacted
                record.args = None
            if isinstance(record.exc_text, str) and record.exc_text:
                for secret in secrets:
                    record.exc_text = record.exc_text.replace(secret, _REDACTION)
        except Exception:
            # Defensive by design: logging filters must not raise or drop records.
            # If redaction fails, allow the original record through unchanged.
            pass
        return True


def is_running_on_gce() -> bool:
    """Return True if the process is running on a GCE instance."""
    try:
        req = urllib.request.Request(_GCE_METADATA_URL, headers=_METADATA_HEADERS)
        with urllib.request.urlopen(req, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_gce_project_id() -> str:
    """Fetch the GCP project ID from the GCE metadata server."""
    req = urllib.request.Request(_GCE_METADATA_URL, headers=_METADATA_HEADERS)
    with urllib.request.urlopen(req, timeout=2) as resp:
        return resp.read().decode().strip()


def get_app_logger(name: str) -> logging.Logger:
    """The standard logger for module ``name`` inside the application hierarchy.

    ``app.services.x`` becomes ``bioaf.services.x``; names already under ``bioaf`` are kept. Installs
    no handlers: the shared configuration applies through propagation.
    """
    if name == APP_LOGGER or name.startswith(APP_LOGGER + "."):
        return logging.getLogger(name)
    if name == "app":
        return logging.getLogger(APP_LOGGER)
    if name.startswith("app."):
        name = name[len("app.") :]
    return logging.getLogger(f"{APP_LOGGER}.{name}")


# Handlers this module installed, by role ("stdout", "direct"). Only these are ever replaced or closed.
_owned: dict[str, logging.Handler] = {}


class JsonFormatter(logging.Formatter):
    """One JSON object per record, on one physical line.

    ``time``, ``severity``, ``logger`` and ``message`` belong to the formatter; supported structured
    data (a mapping message, ``json_fields``) is nested under ``fields`` so it cannot overwrite them.
    ``json.dumps`` escapes CR, LF, other control characters and every non-ASCII character
    (including U+2028/U+2029), so a traceback or untrusted text stays inside its one record.
    """

    def format(self, record: logging.LogRecord) -> str:
        try:
            return _dumps(self._entry(record))
        except Exception as exc:
            return _dumps(
                {
                    "time": _timestamp(getattr(record, "created", None)),
                    "severity": _severity(record),
                    "logger": _safe_name(record),
                    "message": f"log record withheld: it could not be formatted (error_type={type(exc).__name__})",
                }
            )

    def _entry(self, record: logging.LogRecord) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if isinstance(record.msg, dict):
            fields.update(record.msg)
            message = ""
        else:
            message = record.getMessage()
        json_fields = getattr(record, "json_fields", None)
        if isinstance(json_fields, dict):
            fields.update(json_fields)
        entry: dict[str, Any] = {
            "time": _timestamp(record.created),
            "severity": _severity(record),
            "logger": record.name,
            "message": message,
        }
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            entry["exception"] = record.exc_text
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        if fields:
            entry["fields"] = fields
        return entry


def _dumps(entry: dict[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=True, default=lambda o: f"<unsupported {type(o).__name__}>")


def _timestamp(created: float | None) -> str:
    moment = (
        datetime.fromtimestamp(created, timezone.utc)
        if isinstance(created, (int, float))
        else datetime.now(timezone.utc)
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _severity(record: logging.LogRecord) -> str:
    name = getattr(record, "levelname", None)
    return name if isinstance(name, str) else "DEFAULT"


def _safe_name(record: logging.LogRecord) -> str:
    name = getattr(record, "name", None)
    return name if isinstance(name, str) else APP_LOGGER


def configure_logging(*, debug: bool) -> None:
    """Install (or replace) the application-owned stdout handler and set the application level.

    Safe to call repeatedly: the previously owned stdout handler is removed and closed, and only it.
    Handlers others attached to ``bioaf`` (capture, tests, an embedding host, a direct sink) are kept.
    The handler writes to the current ``sys.stdout``.
    """
    bioaf_logger = logging.getLogger(APP_LOGGER)
    bioaf_logger.setLevel(logging.DEBUG if debug else logging.INFO)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(JsonFormatter())
    stdout_handler.addFilter(RedactSecretsFilter())
    previous = _owned.pop("stdout", None)
    if previous is not None:
        bioaf_logger.removeHandler(previous)
        previous.close()
    _owned["stdout"] = stdout_handler
    bioaf_logger.addHandler(stdout_handler)


def attach_cloud_logging(
    project_id: str,
    credentials: Any = None,
    *,
    debug: bool = False,
) -> None:
    """Attach a centralized-logging handler to the ``bioaf`` logger.

    The backend-specific handler is built by the BAL LogSinkProvider (Phase 9G),
    so this module holds no cloud SDK. Uses the provided *credentials* (typically
    the app's configured service account); when ``None`` the GCP client falls
    back to Application Default Credentials. Secret redaction and the stdout
    fallback stay here.
    """
    from app.adapters.observability import create_log_sink_provider
    from app.platform.cloud_provider import backend_for

    bioaf_logger = logging.getLogger("bioaf")
    provider = create_log_sink_provider(project_id, credentials, backend=backend_for("log_sink"))
    handler = provider.build_handler(debug=debug)
    if handler is None:
        bioaf_logger.warning("Centralized logging unavailable, stdout only")
        return

    handler.addFilter(RedactSecretsFilter())
    bioaf_logger.addHandler(handler)
    bioaf_logger.info("Cloud Logging enabled (project=%s)", project_id)
