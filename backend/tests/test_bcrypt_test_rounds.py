"""The suite hashes passwords at a cheap test-only cost; production keeps its default of 12.

``BIOAF_TEST_BCRYPT_ROUNDS`` (default 4) is applied by conftest before any
application import. It is the only switch: a developer's own
``BIOAF_BCRYPT_ROUNDS`` does not leak into the suite, and production code never
reads the test switch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.services.auth_service import AuthService
from tests.plugins.bcrypt_rounds import DEFAULT_TEST_ROUNDS, apply_test_bcrypt_rounds
from tests.test_auth import _start_reset

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _cost(hashed: str) -> int:
    return int(hashed.split("$")[2])


def _scrubbed_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("BIOAF_")}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env.update(extra)
    return env


# --- the switch itself ------------------------------------------------------


def test_switch_defaults_to_four_rounds():
    env: dict[str, str] = {}
    assert apply_test_bcrypt_rounds(env) == 4
    assert env["BIOAF_BCRYPT_ROUNDS"] == "4"
    assert DEFAULT_TEST_ROUNDS == 4


def test_switch_set_to_twelve_restores_the_production_cost():
    env = {"BIOAF_TEST_BCRYPT_ROUNDS": "12"}
    assert apply_test_bcrypt_rounds(env) == 12
    assert env["BIOAF_BCRYPT_ROUNDS"] == "12"


def test_a_developers_application_setting_does_not_reach_the_suite():
    env = {"BIOAF_BCRYPT_ROUNDS": "10"}
    assert apply_test_bcrypt_rounds(env) == 4
    assert env["BIOAF_BCRYPT_ROUNDS"] == "4"


@pytest.mark.parametrize("value", ["abc", "", "3", "32", "4.5"])
def test_switch_refuses_values_bcrypt_cannot_use(value):
    with pytest.raises(ValueError, match="BIOAF_TEST_BCRYPT_ROUNDS"):
        apply_test_bcrypt_rounds({"BIOAF_TEST_BCRYPT_ROUNDS": value})


# --- what this run actually does --------------------------------------------


def _expected_rounds() -> int:
    return int(os.environ.get("BIOAF_TEST_BCRYPT_ROUNDS") or DEFAULT_TEST_ROUNDS)


def test_password_hashes_use_the_test_round_count():
    hashed = AuthService.hash_password("correct horse")
    assert _cost(hashed) == _expected_rounds()


def test_password_created_at_the_test_cost_verifies_and_rejects_a_wrong_password():
    hashed = AuthService.hash_password("correct horse")
    assert AuthService.verify_password("correct horse", hashed)
    assert not AuthService.verify_password("wrong horse", hashed)


def test_two_hashes_of_one_password_differ():
    """Real bcrypt with a fresh salt each time, not a precomputed hash."""
    assert AuthService.hash_password("same") != AuthService.hash_password("same")


@pytest.mark.parametrize("rounds", ["12", "4"])
def test_the_switch_reaches_hashing_through_conftest(rounds):
    """Both the unchanged-baseline and candidate settings take effect end to end."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-n0",
            "-q",
            "-p",
            "no:cacheprovider",
            f"{__file__}::test_password_hashes_use_the_test_round_count",
        ],
        cwd=BACKEND_DIR,
        env=_scrubbed_env(BIOAF_TEST_BCRYPT_ROUNDS=rounds, BIOAF_BCRYPT_ROUNDS="10"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_production_default_is_twelve_rounds_in_a_fresh_process(tmp_path):
    """No test override and no developer .env: the application hashes at 12."""
    probe = (
        "from app.config import settings\n"
        "from app.services.auth_service import AuthService\n"
        "print(settings.bcrypt_rounds, AuthService.hash_password('x').split('$')[2])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,  # no .env here for pydantic-settings to read
        env=_scrubbed_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["12", "12"]


def test_production_code_never_reads_the_test_switch():
    app_dir = BACKEND_DIR / "app"
    offenders = [str(p) for p in app_dir.rglob("*.py") if "BIOAF_TEST_BCRYPT_ROUNDS" in p.read_text()]
    assert offenders == []


# --- auth contracts at the test cost ----------------------------------------


@pytest.mark.asyncio
async def test_login_accepts_the_right_password_and_refuses_the_wrong_one(client: AsyncClient, admin_user):
    ok = await client.post("/api/auth/login", json={"email": "admin@test.com", "password": "testpassword123"})
    assert ok.status_code == 200
    bad = await client.post("/api/auth/login", json={"email": "admin@test.com", "password": "nottheone"})
    assert bad.status_code == 401
    assert _cost(admin_user.password_hash) == _expected_rounds()


@pytest.mark.asyncio
async def test_password_reset_replaces_the_old_password(client: AsyncClient, admin_user, monkeypatch):
    token, code = await _start_reset(client, monkeypatch)
    reset = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "code": code, "new_password": "freshpass123"},
    )
    assert reset.status_code == 200

    old = await client.post("/api/auth/login", json={"email": "admin@test.com", "password": "testpassword123"})
    assert old.status_code == 401
    new = await client.post("/api/auth/login", json={"email": "admin@test.com", "password": "freshpass123"})
    assert new.status_code == 200
