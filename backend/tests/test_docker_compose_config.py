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
