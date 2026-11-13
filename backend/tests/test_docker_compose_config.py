"""Tests that docker-compose.yml configures the backend for real pipeline execution.

Production Compose supplies BIOAF_COMPUTE_MODE=kubernetes when the host leaves it unset or empty, so
the mode-aware adapters contact their real backends; other host values reach the application's own
validation. The standalone development file always supplies local.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.settings_contract import ComputeMode, parse_compute_mode


COMPOSE_FILE = Path(__file__).parent.parent.parent / "docker" / "docker-compose.yml"


@pytest.fixture
def compose_config():
    return yaml.safe_load(COMPOSE_FILE.read_text())


class TestBackendComputeConfig:
    def test_compute_mode_falls_back_to_the_canonical_real_mode(self, compose_config):
        """The deployment boundary's fallback is spelled canonically (the app has no default)."""
        backend_env = compose_config["services"]["backend"]["environment"]
        assert backend_env["BIOAF_COMPUTE_MODE"] == "${BIOAF_COMPUTE_MODE:-kubernetes}"

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


# --- Effective compute mode, rendered by Compose itself -------------------------------------------

needs_compose = pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose CLI not installed")


def _render(compose: Path, tmp_path: Path, env_lines: list[str], shell: dict[str, str] | None = None) -> dict:
    """``docker compose config`` with a synthetic host env file and a shell holding nothing else."""
    env_file = tmp_path / "host.env"
    env_file.write_text("".join(f"{line}\n" for line in env_lines))
    proc = subprocess.run(
        ["docker", "compose", "-f", str(compose), "--env-file", str(env_file), "config", "--format", "json"],
        # Only what docker needs to find its compose plugin: no inherited value can be interpolated.
        env={k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_CONFIG") if k in os.environ} | (shell or {}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["services"]["backend"]


def _effective(raw: str | None) -> ComputeMode | None:
    try:
        return parse_compute_mode(raw)
    except ValueError:
        return None


@needs_compose
@pytest.mark.parametrize(
    ("host_lines", "rendered", "effective"),
    [
        ([], "kubernetes", ComputeMode.KUBERNETES),
        (["BIOAF_COMPUTE_MODE="], "kubernetes", ComputeMode.KUBERNETES),
        (["BIOAF_COMPUTE_MODE=local"], "local", ComputeMode.LOCAL),
        (["BIOAF_COMPUTE_MODE=kubernetes"], "kubernetes", ComputeMode.KUBERNETES),
        (["BIOAF_COMPUTE_MODE=k8s"], "k8s", ComputeMode.KUBERNETES),
        (["BIOAF_COMPUTE_MODE=K8S"], "K8S", ComputeMode.KUBERNETES),
        (["BIOAF_COMPUTE_MODE=LOCAL"], "LOCAL", ComputeMode.LOCAL),
        (["BIOAF_COMPUTE_MODE=slurm"], "slurm", ComputeMode.SLURM),
        (["BIOAF_COMPUTE_MODE=typo"], "typo", None),
    ],
)
def test_production_compose_supplies_real_mode_unless_the_host_sets_one(tmp_path, host_lines, rendered, effective):
    backend = _render(COMPOSE_FILE, tmp_path, host_lines)
    assert backend["environment"]["BIOAF_COMPUTE_MODE"] == rendered
    assert _effective(rendered) is effective


@needs_compose
@pytest.mark.parametrize("host_lines", [[], ["BIOAF_COMPUTE_MODE=kubernetes"]])
def test_development_compose_always_supplies_local(tmp_path, host_lines):
    backend = _render(DEV_COMPOSE_FILE, tmp_path, host_lines)
    assert backend["environment"]["BIOAF_COMPUTE_MODE"] == "local"


@needs_compose
def test_the_host_env_file_is_mounted_beside_not_as_the_application_dotenv(tmp_path):
    # The image works in /app; Pydantic reads /app/.env. The host file is only /host/.env.
    targets = {v["target"] for v in _render(COMPOSE_FILE, tmp_path, [])["volumes"]}
    assert "/host/.env" in targets
    assert "/app/.env" not in targets


@needs_compose
def test_a_previous_release_tag_in_the_env_file_renders_that_backend_image(tmp_path):
    """Configuration-only rollback: the pinned tag picks the image whatever this checkout's spelling."""
    backend = _render(COMPOSE_FILE, tmp_path, ["BIOAF_IMAGE_TAG=v2026.11.20"])
    assert backend["image"] == "ghcr.io/bioaf/bioaf-backend:v2026.11.20"
    # An exported shell tag overrides the env file: the rollback note tells operators to unset it.
    shadowed = _render(COMPOSE_FILE, tmp_path, ["BIOAF_IMAGE_TAG=v2026.11.20"], {"BIOAF_IMAGE_TAG": "v2026.11.21"})
    assert shadowed["image"] == "ghcr.io/bioaf/bioaf-backend:v2026.11.21"


class TestBackendLogDelivery:
    """Plan 22, D1: both Compose files select stdout delivery, agreeing with the application default.

    Production lets the host override it (``direct`` only where no collector ships container output);
    the development file pins stdout. Neither enables the direct sink alongside a collector.
    """

    def test_production_falls_back_to_stdout(self, compose_config):
        backend_env = compose_config["services"]["backend"]["environment"]
        assert backend_env["BIOAF_LOG_DELIVERY"] == "${BIOAF_LOG_DELIVERY:-stdout}"

    def test_development_pins_stdout(self):
        backend_env = yaml.safe_load(DEV_COMPOSE_FILE.read_text())["services"]["backend"]["environment"]
        assert "BIOAF_LOG_DELIVERY=stdout" in backend_env

    def test_the_compose_default_is_the_application_default(self):
        from app.config import Settings

        assert Settings.model_fields["log_delivery"].default == "stdout"
