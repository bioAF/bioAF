"""Installation metrics configuration and the no-egress guards (ADR-075).

The release's public destination ships in the backend image; a runtime override replaces it only as a
complete, valid pair; the operator override disables delivery whatever is configured. Tests, CI and
development never reach PostHog: the suite's transport refuses every request, even when a test enables
reporting with production-shaped configuration.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

import app.database as database
from app import install_metrics_release as release
from app.config import Settings, settings
from app.models.install_metric_event import InstallMetricEvent
from app.services.install_metrics import sender
from app.services.install_metrics.state import NOTICE_VERSION, InstallMetricsService

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
NOW = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def configure(monkeypatch):
    def apply(*, disabled=False, token="", host=""):
        monkeypatch.setattr(settings, "install_metrics_disabled", disabled)
        monkeypatch.setattr(settings, "posthog_project_token", token)
        monkeypatch.setattr(settings, "posthog_host", host)

    return apply


def test_the_release_destination_is_the_public_us_cloud_project():
    assert release.POSTHOG_HOST == "https://us.i.posthog.com"
    assert release.POSTHOG_PROJECT_TOKEN.startswith("phc_")


def test_with_no_override_reports_go_to_the_release_destination(configure):
    configure()

    destination = sender.resolve_destination()

    assert destination == sender.Destination(release.POSTHOG_PROJECT_TOKEN, release.POSTHOG_HOST)
    assert destination.url == "https://us.i.posthog.com/i/v0/e/"


def test_a_complete_valid_override_replaces_the_release_pair(configure):
    configure(token="phc_" + "a" * 40, host="https://eu.i.posthog.com/")

    assert sender.resolve_destination() == sender.Destination("phc_" + "a" * 40, "https://eu.i.posthog.com")


@pytest.mark.parametrize(
    ("token", "host"),
    [
        ("phc_" + "a" * 40, ""),
        ("", "https://eu.i.posthog.com"),
        ("phc_" + "a" * 40, "http://eu.i.posthog.com"),
        ("phc_" + "a" * 40, "https://user:pw@eu.i.posthog.com"),
        ("phc_" + "a" * 40, "https://eu.i.posthog.com/i/v0/e/"),
        ("phc_" + "a" * 40, "https://eu.i.posthog.com?x=1"),
        ("phx_personal_key_000000000000000000000", "https://eu.i.posthog.com"),
        ("phc_short", "https://eu.i.posthog.com"),
        ("phc_" + "a" * 40, "not a url"),
    ],
)
def test_a_partial_or_invalid_override_disables_delivery_without_echoing_it(configure, caplog, token, host):
    configure(token=token, host=host)

    assert sender.resolve_destination() is None
    for value in (token, host):
        if value:
            assert value not in caplog.text


def test_the_operator_override_wins_over_any_destination(configure):
    configure(disabled=True, token="phc_" + "a" * 40, host="https://eu.i.posthog.com")

    assert sender.resolve_destination() is None


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes"])
def test_the_operator_override_is_read_from_the_environment(monkeypatch, raw):
    monkeypatch.setenv("BIOAF_INSTALL_METRICS_DISABLED", raw)

    assert Settings().install_metrics_disabled is True  # type: ignore[call-arg]


def test_the_suite_runs_with_the_operator_override_on():
    assert os.environ["BIOAF_INSTALL_METRICS_DISABLED"] == "1"
    assert settings.install_metrics_disabled is True


def test_importing_the_release_module_and_sender_makes_no_network_request():
    """A child process with sockets disabled imports both; a connection attempt would fail it."""
    code = (
        "import socket\n"
        "def refuse(*a, **k): raise SystemExit('network attempted')\n"
        "socket.socket.connect = refuse\n"
        "socket.create_connection = refuse\n"
        "import app.install_metrics_release, app.services.install_metrics.sender\n"
    )
    env = {**os.environ, "BIOAF_COMPUTE_MODE": "local", "BIOAF_INSTALL_METRICS_DISABLED": "1"}
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr[-500:]


@pytest.mark.asyncio
async def test_the_suite_transport_blocks_egress_even_with_production_shaped_configuration(
    session, admin_user, configure
):
    """A test that accidentally enables reporting still cannot reach the real project."""
    configure()  # override off, the release's real destination
    state = await InstallMetricsService.choose(session, choice="enabled", notice_version=NOTICE_VERSION, revision=0)
    await session.commit()
    assert state.installation_id is not None

    counts = await sender.send_pending(database.async_session_factory, now=NOW)

    assert counts["sent"] == 0
    assert counts["failed"] == 1
    (row,) = (await session.execute(select(InstallMetricEvent))).scalars().all()
    assert row.status == "pending"
    assert row.last_error == "transport"


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".github/workflows/backend-benchmark.yml",
        ".github/workflows/build.yml",
        "docker/docker-compose.dev.yml",
    ],
)
def test_ci_and_development_configuration_disable_reporting(path):
    import yaml

    config = yaml.safe_load((REPO / path).read_text())
    if "services" in config:
        assert "BIOAF_INSTALL_METRICS_DISABLED=1" in config["services"]["backend"]["environment"]
    else:
        assert config["env"]["BIOAF_INSTALL_METRICS_DISABLED"] == "1"


def test_the_image_check_passes_the_override_into_the_backend_container():
    build = (REPO / ".github" / "workflows" / "build.yml").read_text()

    assert "-e BIOAF_INSTALL_METRICS_DISABLED=1" in build
    assert "--network none" in build


def test_production_compose_passes_the_runtime_overrides_but_never_disables_reporting():
    import yaml

    compose = yaml.safe_load((REPO / "docker" / "docker-compose.yml").read_text())
    env = compose["services"]["backend"]["environment"]

    # Only the host env file decides; the default is empty (reporting follows the admin's choice).
    assert env["BIOAF_INSTALL_METRICS_DISABLED"] == "${BIOAF_INSTALL_METRICS_DISABLED:-}"
    assert env["BIOAF_POSTHOG_PROJECT_TOKEN"] == "${BIOAF_POSTHOG_PROJECT_TOKEN:-}"
    assert env["BIOAF_POSTHOG_HOST"] == "${BIOAF_POSTHOG_HOST:-}"
    dockerfile = (REPO / "docker" / "Dockerfile.backend").read_text()
    assert "INSTALL_METRICS_DISABLED" not in dockerfile
