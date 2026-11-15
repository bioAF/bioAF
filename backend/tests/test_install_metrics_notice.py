"""The disclosure version the frontend saves a choice against is the one the backend accepts (ADR-075)."""

import re
from pathlib import Path

from app.services.install_metrics.state import NOTICE_VERSION

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "installMetrics.ts"


def test_the_frontend_notice_version_matches_the_backend():
    match = re.search(r'INSTALL_METRICS_NOTICE_VERSION = "([^"]+)"', FRONTEND.read_text())

    assert match is not None
    assert match.group(1) == NOTICE_VERSION
