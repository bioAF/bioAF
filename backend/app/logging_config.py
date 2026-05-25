"""Logging configuration with Cloud Logging support.

Sets up stdout logging at import time.  After the database is available,
``attach_cloud_logging`` can be called with the app's configured GCP
credentials so structured logs flow to Cloud Console using the same
service account the rest of the platform uses.
"""

import logging
import sys
import urllib.request
from typing import Any

from app.config import settings

try:
    import google.cloud.logging as cloud_logging
except ImportError:  # pragma: no cover
    cloud_logging = None  # type: ignore[assignment]

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
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
            record
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


def configure_logging(*, debug: bool) -> None:
    """Set up the ``bioaf`` logger with stdout only.

    Cloud Logging is attached later via ``attach_cloud_logging`` once the
    database is available and GCP credentials can be loaded.
    """
    bioaf_logger = logging.getLogger("bioaf")
    bioaf_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    bioaf_logger.handlers.clear()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    stdout_handler.addFilter(RedactSecretsFilter())
    bioaf_logger.addHandler(stdout_handler)


def attach_cloud_logging(
    project_id: str,
    credentials: Any = None,
    *,
    debug: bool = False,
) -> None:
    """Attach a Cloud Logging handler to the ``bioaf`` logger.

    Uses the provided *credentials* (typically the app's configured service
    account).  When *credentials* is ``None``, the client falls back to
    Application Default Credentials.
    """
    if cloud_logging is None:
        logging.getLogger("bioaf").warning("google-cloud-logging not installed, Cloud Logging unavailable")
        return

    bioaf_logger = logging.getLogger("bioaf")
    try:
        client = cloud_logging.Client(project=project_id, credentials=credentials)
        cloud_handler = client.get_default_handler()
        cloud_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        cloud_handler.addFilter(RedactSecretsFilter())
        bioaf_logger.addHandler(cloud_handler)
        bioaf_logger.info("Cloud Logging enabled (project=%s)", project_id)
    except Exception as exc:
        bioaf_logger.warning("Cloud Logging unavailable, stdout only: %s", exc)
