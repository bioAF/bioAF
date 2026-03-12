"""Tests for Phase 23 file structure: Docker Compose, nginx, env, and bioaf script."""

import os
import stat

import pytest
import yaml


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# --- Step 2: Docker Compose consolidation ---


def test_docker_compose_yml_exists():
    """docker/docker-compose.yml exists in the repo."""
    path = os.path.join(REPO_ROOT, "docker", "docker-compose.yml")
    assert os.path.isfile(path), f"Expected file at {path}"


def test_nginx_conf_exists():
    """docker/nginx.conf exists in the repo."""
    path = os.path.join(REPO_ROOT, "docker", "nginx.conf")
    assert os.path.isfile(path), f"Expected file at {path}"


def test_env_example_has_required_vars():
    """docker/.env.example contains required environment variables."""
    path = os.path.join(REPO_ROOT, "docker", ".env.example")
    assert os.path.isfile(path), f"Expected file at {path}"

    with open(path) as f:
        content = f.read()

    required_vars = [
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "DATABASE_URL",
        "SECRET_KEY",
    ]
    for var in required_vars:
        assert var in content, f"Missing required variable {var} in .env.example"


def test_docker_compose_build_contexts_are_relative():
    """docker-compose.yml build contexts point to paths within the main repo."""
    path = os.path.join(REPO_ROOT, "docker", "docker-compose.yml")
    with open(path) as f:
        compose = yaml.safe_load(f)

    services = compose.get("services", {})
    backend = services.get("backend", {})
    frontend = services.get("frontend", {})

    backend_ctx = backend.get("build", {}).get("context", "")
    frontend_ctx = frontend.get("build", {}).get("context", "")

    # Contexts should NOT reference a sibling repo (../../bioAF)
    assert "../../bioAF" not in backend_ctx, "Backend context still references sibling repo"
    assert "../../bioAF" not in frontend_ctx, "Frontend context still references sibling repo"


def test_docker_compose_nginx_uses_new_conf():
    """docker-compose.yml nginx volume mounts nginx.conf, not nginx.poc.conf."""
    path = os.path.join(REPO_ROOT, "docker", "docker-compose.yml")
    with open(path) as f:
        compose = yaml.safe_load(f)

    nginx = compose["services"]["nginx"]
    volumes = nginx.get("volumes", [])
    volume_str = " ".join(str(v) for v in volumes)
    assert "nginx.conf" in volume_str
    assert "nginx.poc.conf" not in volume_str


# --- Step 3: bioaf CLI script ---


def test_bioaf_script_exists_and_executable():
    """bioaf script exists at repo root and is executable."""
    path = os.path.join(REPO_ROOT, "bioaf")
    assert os.path.isfile(path), f"Expected bioaf script at {path}"
    mode = os.stat(path).st_mode
    assert mode & stat.S_IXUSR, "bioaf script is not executable"


def test_bioaf_help_lists_commands():
    """bioaf help output lists key commands."""
    import subprocess

    result = subprocess.run(
        [os.path.join(REPO_ROOT, "bioaf"), "help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for cmd in ["setup", "start", "stop", "restart", "status", "logs", "migrate", "help"]:
        assert cmd in result.stdout, f"Missing command '{cmd}' in help output"


def test_bioaf_unknown_command_shows_help():
    """bioaf with unknown command shows help and exits non-zero."""
    import subprocess

    result = subprocess.run(
        [os.path.join(REPO_ROOT, "bioaf"), "nonexistent-command"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "help" in result.stdout.lower() or "usage" in result.stdout.lower() or "help" in result.stderr.lower()


# --- Step 4: First-run detection ---


@pytest.mark.asyncio
async def test_bootstrap_status_returns_complete_when_admin_exists(client, session):
    """GET /api/bootstrap/status returns setup_complete=true when org has setup_complete=true."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="Test Org", setup_complete=True)
    session.add(org)
    await session.flush()

    user = User(
        email="cli-admin@example.com",
        password_hash=AuthService.hash_password("pass123"),
        role="admin",
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.commit()

    resp = await client.get("/api/bootstrap/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["setup_complete"] is True


@pytest.mark.asyncio
async def test_bootstrap_status_returns_incomplete_when_no_org(client):
    """GET /api/bootstrap/status returns setup_complete=false when no org exists."""
    resp = await client.get("/api/bootstrap/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["setup_complete"] is False


@pytest.mark.asyncio
async def test_create_admin_blocked_after_cli_setup(client, session):
    """POST /api/bootstrap/create-admin returns 409 after CLI admin creation."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="CLI Org", setup_complete=True)
    session.add(org)
    await session.flush()

    user = User(
        email="cli-admin@example.com",
        password_hash=AuthService.hash_password("pass123"),
        role="admin",
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.commit()

    resp = await client.post(
        "/api/bootstrap/create-admin",
        json={"email": "other@example.com", "password": "pass", "name": "Other"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_login_works_after_cli_admin_creation(client, session):
    """Admin created via CLI can log in through the normal login endpoint."""
    from app.cli.create_admin import create_admin_user

    await create_admin_user(
        session,
        email="cli-admin@example.com",
        password="SecurePass123!",
        org_name="CLI Org",
        org_slug="cli-org",
    )

    resp = await client.post(
        "/api/auth/login",
        json={"email": "cli-admin@example.com", "password": "SecurePass123!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
