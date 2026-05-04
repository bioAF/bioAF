"""Tests for the new `references` resource in the permission catalog.

The Reference Data Ingest spec (ADR-047) calls for a dedicated `references`
resource with `view` and `upload` actions, replacing the prior reuse of
`pipelines:view` / `pipelines:create`. This test pins the catalog and
the per-role seeding so future drift is caught.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.role import RolePermission
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import ALL_RESOURCES_ACTIONS


def test_references_resource_in_catalog():
    """`references` resource must expose at least view and upload actions."""
    assert "references" in ALL_RESOURCES_ACTIONS
    actions = ALL_RESOURCES_ACTIONS["references"]
    assert "view" in actions
    assert "upload" in actions


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_refperm@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def bench_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench_refperm@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


async def _role_perms(session, role_id: int) -> set[tuple[str, str]]:
    result = await session.execute(
        select(RolePermission.resource, RolePermission.action).where(RolePermission.role_id == role_id)
    )
    return {(r, a) for r, a in result.all()}


@pytest.mark.asyncio
async def test_admin_role_seeded_with_references_permissions(session, admin_user):
    """admin role: references:view and references:upload."""
    role_id = admin_user._test_role_map["admin"]
    perms = await _role_perms(session, role_id)
    assert ("references", "view") in perms
    assert ("references", "upload") in perms


@pytest.mark.asyncio
async def test_comp_bio_role_seeded_with_references_permissions(session, admin_user, comp_bio_user):
    """comp_bio role: references:view and references:upload."""
    role_id = admin_user._test_role_map["comp_bio"]
    perms = await _role_perms(session, role_id)
    assert ("references", "view") in perms
    assert ("references", "upload") in perms


@pytest.mark.asyncio
async def test_bench_role_seeded_with_references_view(session, admin_user, bench_user):
    """bench role: references:view only (no upload)."""
    role_id = admin_user._test_role_map["bench"]
    perms = await _role_perms(session, role_id)
    assert ("references", "view") in perms
    assert ("references", "upload") not in perms


@pytest.mark.asyncio
async def test_viewer_role_seeded_with_references_view(session, admin_user):
    """viewer role: references:view only."""
    role_id = admin_user._test_role_map["viewer"]
    perms = await _role_perms(session, role_id)
    assert ("references", "view") in perms
    assert ("references", "upload") not in perms


def test_reference_status_enum_includes_uploading_and_failed():
    """Spec §2 introduces 'uploading' and 'failed' as new lifecycle statuses."""
    from app.models.reference_dataset import REFERENCE_STATUSES

    assert "uploading" in REFERENCE_STATUSES
    assert "failed" in REFERENCE_STATUSES
    # Existing statuses must still be present (additive only).
    assert "active" in REFERENCE_STATUSES
    assert "deprecated" in REFERENCE_STATUSES
    assert "pending_approval" in REFERENCE_STATUSES
