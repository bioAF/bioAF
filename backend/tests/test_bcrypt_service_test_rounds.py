"""API keys and setup codes hash at the test cost in the suite and at 12 in production.

Each service owns its cost (``HASH_ROUNDS``); conftest lowers it per test with
monkeypatch rather than touching the shared bcrypt module, and every hash is
still real bcrypt with a fresh salt.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import api_key_service
from app.services.setup_code_service import SetupCodeService
from tests.plugins.bcrypt_rounds import DEFAULT_TEST_ROUNDS

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _cost(hashed: str) -> int:
    return int(hashed.split("$")[2])


def _expected_rounds() -> int:
    return int(os.environ.get("BIOAF_TEST_BCRYPT_ROUNDS") or DEFAULT_TEST_ROUNDS)


@pytest.mark.asyncio
async def test_minted_api_keys_hash_at_the_test_cost_and_verify(session, admin_user):
    from app.services import service_account_service

    sa = await service_account_service.create(
        session,
        org_id=admin_user.organization_id,
        display_name="SA",
        role_id=admin_user.role_id,
        created_by_user_id=admin_user.id,
    )
    row, secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=sa.id,
        name="k",
        scopes=["projects:view"],
        created_by_user_id=admin_user.id,
    )
    other_row, other_secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=sa.id,
        name="k2",
        scopes=["projects:view"],
        created_by_user_id=admin_user.id,
    )
    await session.commit()

    assert _cost(row.key_hash) == _expected_rounds()
    assert row.key_hash != other_row.key_hash
    assert (await api_key_service.verify(session, secret)) is not None
    assert (await api_key_service.verify(session, other_secret)) is not None


@pytest.mark.asyncio
async def test_setup_codes_hash_at_the_test_cost(session):
    from app.models.organization import Organization

    org = Organization(name="Org")
    session.add(org)
    await session.flush()
    code = await SetupCodeService.generate_code(session, org)
    assert _cost(org.setup_code_hash) == _expected_rounds()
    assert await SetupCodeService.verify_code(session, org, code)


def test_production_services_hash_at_twelve_in_a_fresh_process(tmp_path):
    probe = (
        "from app.services import api_key_service, setup_code_service\n"
        "print(api_key_service._hash('k').split('$')[2], setup_code_service.HASH_ROUNDS,"
        " api_key_service.HASH_ROUNDS)\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("BIOAF_")}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    # Importing the services needs an encryption key; it has no bearing on hashing cost.
    env["BIOAF_ENCRYPTION_KEYS"] = os.environ["BIOAF_ENCRYPTION_KEYS"]
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["12", "12", "12"]


def test_the_shared_bcrypt_module_is_not_patched():
    import bcrypt

    assert bcrypt.gensalt.__module__.startswith("bcrypt")
    assert bcrypt.hashpw.__module__.startswith("bcrypt")
