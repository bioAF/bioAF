"""Deployment settings data exports depend on (plan 24 contract 4)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parent.parent.parent / "docker"
NGINX = (DOCKER / "nginx.conf").read_text()


def _api_location() -> str:
    start = NGINX.index("location /api/ {")
    return NGINX[start : NGINX.index("}", start)]


def test_nginx_pins_the_client_send_timeout_to_the_export_stall_limit():
    assert re.search(r"^\s*send_timeout 60s;", _api_location(), re.MULTILINE)


def test_nginx_honours_the_backends_buffering_header():
    """X-Accel-Buffering: no on an export response must reach nginx's decision, not be ignored."""
    assert "proxy_ignore_headers" not in NGINX
    assert "proxy_buffering on" not in NGINX


def test_nginx_keeps_its_upstream_timeouts():
    location = _api_location()
    assert "proxy_read_timeout 3600s;" in location
    assert "proxy_send_timeout 3600s;" in location


def test_compose_passes_the_export_settings_with_their_defaults():
    env = yaml.safe_load((DOCKER / "docker-compose.yml").read_text())["services"]["backend"]["environment"]

    assert env["BIOAF_EXPORT_TRANSFER_STALL_SECONDS"] == "${BIOAF_EXPORT_TRANSFER_STALL_SECONDS:-60}"
    assert env["BIOAF_EXPORT_SIGNED_MIN_TTL_SECONDS"] == "${BIOAF_EXPORT_SIGNED_MIN_TTL_SECONDS:-3600}"


def test_the_image_runs_one_serving_process():
    """The export slot, like the task owner, is per process; more workers would each have one."""
    dockerfile = (DOCKER / "Dockerfile.backend").read_text()
    [cmd] = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]

    assert "--workers" not in cmd
    assert "uvicorn" in cmd


def test_the_settings_defaults_match_the_accepted_envelope():
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["export_transfer_stall_seconds"].default == 60
    assert fields["export_signed_min_ttl_seconds"].default == 3600
    assert fields["export_scratch_dir"].default == "/tmp/bioaf-exports"
