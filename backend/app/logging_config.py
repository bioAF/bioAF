"""Application logging: one hierarchy, one sanitizer, single-line JSON on stdout.

Every application module logs under ``bioaf`` (``get_app_logger(__name__)`` maps ``app.x`` to
``bioaf.x``). ``configure_logging`` installs the one application-owned stdout handler, which writes
each record as one JSON object on one line. Root, Uvicorn, SQLAlchemy, Alembic and cloud SDK loggers
are left alone, and ``bioaf`` keeps propagating so capture and embedding hosts still see records.
"""

import functools
import json
import logging
import math
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
# Public placeholders shipped in the repository (settings defaults, Compose and .env examples). They
# protect nothing, and redacting them would mangle ordinary words such as "password" in every line.
_PUBLIC_PLACEHOLDERS = frozenset(
    {
        "password",
        "devpassword",
        "change_me",
        "change_me_to_random_string",
        "dev-secret-key-change-in-production",
        "dev-secret-key-do-not-use-in-production",
    }
)
# Structured record attributes the sanitizer supports. Other ``extra`` values are not covered.
_STRUCTURED_ATTRIBUTES = ("json_fields", "labels", "http_request")
_MAX_DEPTH = 32
_SANITIZED = "_bioaf_sanitized"


@functools.lru_cache(maxsize=8)
def _database_password_forms(url: str) -> tuple[str, ...]:
    """The decoded password in ``url`` and its spelling inside the URL (raw or percent-encoded).

    Parses in memory only: never connects, and never logs the URL when it does not parse.
    """
    from urllib.parse import quote, urlsplit

    from sqlalchemy.engine import make_url

    forms: list[str] = []
    try:
        password = make_url(url).password
    except Exception:
        password = None
    if password:
        forms += [str(password), quote(str(password), safe="")]
    try:
        spelled = urlsplit(url).password
    except Exception:
        spelled = None
    if spelled:
        forms.append(spelled)
    return tuple(forms)


def _known_secrets() -> list[str]:
    """The live values that must never reach an application sink, longest first.

    Read from the settings singleton at emission, so values loaded later (SMTP from the database, a
    Secret Manager database password) and rotations are covered. Only these settings: SMTP password,
    JWT signing key, the Slack client and signing secret slots, each at-rest encryption key, and the
    database URL's password. Organization credentials stored in the database (bot tokens, webhook URLs,
    per-organization Slack secrets not mirrored into settings) are not here: their callers must keep
    them out of log calls. No database or network reads happen on this path.
    """
    from app.config import parse_encryption_keys

    candidates: list[Any] = [
        getattr(settings, "smtp_password", ""),
        getattr(settings, "jwt_secret_key", ""),
        getattr(settings, "slack_client_secret", ""),
        getattr(settings, "slack_signing_secret", ""),
    ]
    raw_keys = getattr(settings, "encryption_keys", "")
    if isinstance(raw_keys, str):
        candidates += parse_encryption_keys(raw_keys)
    url = getattr(settings, "database_url", "")
    if isinstance(url, str) and url:
        candidates += _database_password_forms(url)
    eligible = {
        value
        for value in candidates
        if isinstance(value, str) and len(value) >= _MIN_SECRET_LEN and value.lower() not in _PUBLIC_PLACEHOLDERS
    }
    return sorted(eligible, key=len, reverse=True)


def _scrub(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, _REDACTION)
    return text


def _clean(value: Any, secrets: list[str], depth: int = 0, path: frozenset[int] = frozenset()) -> Any:
    """A sanitized JSON-like copy: str, int, float, bool, None, and dicts/lists/tuples of them.

    Anything else becomes a type placeholder (never its ``repr``). Cycles and excessive nesting are
    cut off with fixed markers. The input is never mutated.
    """
    if isinstance(value, str):
        return _scrub(value, secrets)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, (dict, list, tuple)):
        return f"<unsupported {type(value).__name__}>"
    if id(value) in path:
        return "<cycle>"
    if depth >= _MAX_DEPTH:
        return "<too deep>"
    path = path | {id(value)}
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) or key is None or isinstance(key, (bool, int, float)):
                name = _scrub(str(key), secrets)
            else:
                name = f"<unsupported {type(key).__name__}>"
            cleaned[name] = _clean(item, secrets, depth + 1, path)
        return cleaned
    return [_clean(item, secrets, depth + 1, path) for item in value]


def _withhold(record: logging.LogRecord, reason: Exception) -> None:
    """Replace the record's content with a fixed diagnostic; keep its level, logger and origin."""
    record.msg = f"log record withheld: it could not be sanitized (error_type={type(reason).__name__})"
    record.args = None
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    for attribute in _STRUCTURED_ATTRIBUTES:
        record.__dict__.pop(attribute, None)


def _sanitize(record: logging.LogRecord) -> None:
    secrets = _known_secrets()
    if isinstance(record.msg, dict):
        record.msg = _clean(record.msg, secrets)
        record.args = None
    else:
        message = record.getMessage()
        scrubbed = _scrub(message, secrets)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = None
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if isinstance(record.exc_text, str) and record.exc_text:
        scrubbed = _scrub(record.exc_text, secrets)
        if scrubbed != record.exc_text:
            record.exc_text = scrubbed
            # The cached text is now authoritative; a formatter that re-renders must not see the original.
            record.exc_info = None
    if isinstance(record.stack_info, str) and record.stack_info:
        record.stack_info = _scrub(record.stack_info, secrets)
    for attribute in _STRUCTURED_ATTRIBUTES:
        if attribute in record.__dict__:
            cleaned = _clean(record.__dict__[attribute], secrets)
            if isinstance(cleaned, dict):
                record.__dict__[attribute] = cleaned
            else:
                record.__dict__.pop(attribute)


class RedactSecretsFilter(logging.Filter):
    """Sanitize an application record in place, once, before any sink formats it.

    Covers the message (a mapping message stays a mapping), fresh and cached exception text
    (chained exceptions included), stack information, and the supported structured attributes
    (``json_fields``, ``labels``, ``http_request``). Because the record itself is rewritten, sinks
    after this one, and capture or root handlers it propagates to, see the sanitized record.

    Never raises and never passes an unsafe record through: if sanitizing (or formatting the
    message) fails, the record's content is replaced by a fixed diagnostic naming the error class.
    It never logs from inside the filter.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.__dict__.get(_SANITIZED):
            return True
        try:
            _sanitize(record)
        except Exception as exc:
            try:
                _withhold(record, exc)
            except Exception:
                pass
        record.__dict__[_SANITIZED] = True
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
    # allow_nan=False: NaN/Infinity are not standard JSON; such a record falls back to the diagnostic.
    return json.dumps(entry, ensure_ascii=True, allow_nan=False, default=lambda o: f"<unsupported {type(o).__name__}>")


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
