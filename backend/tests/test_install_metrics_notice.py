"""The disclosure version the frontend saves a choice against is the one the backend accepts (ADR-075)."""

import re
from pathlib import Path

from app.services.install_metrics.state import NOTICE_VERSION

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "installMetrics.ts"


def test_the_frontend_notice_version_matches_the_backend():
    match = re.search(r'INSTALL_METRICS_NOTICE_VERSION = "([^"]+)"', FRONTEND.read_text())

    assert match is not None
    assert match.group(1) == NOTICE_VERSION


def test_the_public_notice_names_the_responsible_party_and_a_deletion_contact():
    notice = (Path(__file__).resolve().parents[2] / "docs" / "installation-metrics.md").read_text()

    assert "privacy@bioaf.co" in notice
    assert "**Responsible party:** bioAF" in notice
    assert "publishes a verified privacy contact here" not in notice
