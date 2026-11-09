"""Tests that docker-compose.yml configures the backend for real pipeline execution.

The backend service must set BIOAF_COMPUTE_MODE so the K8s compute adapter
runs in k8s mode rather than the local mock mode.
"""

from pathlib import Path

import pytest
import yaml


COMPOSE_FILE = Path(__file__).parent.parent.parent / "docker" / "docker-compose.yml"


@pytest.fixture
def compose_config():
    return yaml.safe_load(COMPOSE_FILE.read_text())


class TestBackendComputeConfig:
    def test_compute_mode_set_to_k8s(self, compose_config):
        """Backend service must set BIOAF_COMPUTE_MODE to k8s."""
        backend_env = compose_config["services"]["backend"]["environment"]
        assert "BIOAF_COMPUTE_MODE" in backend_env, "BIOAF_COMPUTE_MODE not set in backend environment"
        value = backend_env["BIOAF_COMPUTE_MODE"]
        # The value may use variable substitution like ${BIOAF_COMPUTE_MODE:-k8s}
        assert "k8s" in str(value), f"Expected BIOAF_COMPUTE_MODE to default to k8s, got {value}"

    def test_gcp_project_id_passed(self, compose_config):
        """Backend service must pass GCP_PROJECT_ID for GKE cluster access."""
        backend_env = compose_config["services"]["backend"]["environment"]
        assert "BIOAF_GCP_PROJECT_ID" in backend_env, "BIOAF_GCP_PROJECT_ID not set in backend environment"


DEV_COMPOSE_FILE = COMPOSE_FILE.parent / "docker-compose.dev.yml"
DOCKERFILE = COMPOSE_FILE.parent / "Dockerfile.backend"


class TestBackendShutdownBudget:
    """Plan 14: uvicorn's request grace (10 s) and the container's stop grace (60 s).

    The backend's lifespan shutdown drains owned work for 10 s, cancels for up to 20 s, and
    disposes resources for up to 5 s. Compose's default 10 s stop grace would SIGKILL inside that;
    60 s is the hard stop, including when the event loop is blocked. The development file is used
    on its own, so it carries the same settings.
    """

    @pytest.mark.parametrize("path", [COMPOSE_FILE, DEV_COMPOSE_FILE], ids=["production", "development"])
    def test_backend_stop_grace_is_60_seconds(self, path):
        backend = yaml.safe_load(path.read_text())["services"]["backend"]
        assert backend.get("stop_grace_period") == "60s"

    def test_image_bounds_uvicorns_request_grace(self):
        cmd = next(line for line in DOCKERFILE.read_text().splitlines() if line.startswith("CMD "))
        assert '"--timeout-graceful-shutdown", "10"' in cmd

    def test_development_command_bounds_uvicorns_request_grace(self):
        backend = yaml.safe_load(DEV_COMPOSE_FILE.read_text())["services"]["backend"]
        assert "--timeout-graceful-shutdown 10" in backend["command"]
