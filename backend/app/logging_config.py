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

try:
    import google.cloud.logging as cloud_logging
except ImportError:  # pragma: no cover
    cloud_logging = None  # type: ignore[assignment]

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_GCE_METADATA_URL = "http://169.254.169.254/computeMetadata/v1/project/project-id"
_METADATA_HEADERS = {"Metadata-Flavor": "Google"}


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
        bioaf_logger.addHandler(cloud_handler)
        bioaf_logger.info("Cloud Logging enabled (project=%s)", project_id)
    except Exception as exc:
        bioaf_logger.warning("Cloud Logging unavailable, stdout only: %s", exc)
