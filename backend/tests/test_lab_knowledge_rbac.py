"""RBAC defaults for Lab Knowledge resources (ADR-059, ADR-060, ADR-063)."""

import pytest

from app.services import role_service
from app.services.bootstrap_roles import seed_builtin_roles


@pytest.mark.asyncio
async def test_admin_has_all_lab_knowledge_manage_permissions(session, admin_user):
    role_map = admin_user._test_role_map
    admin_role = role_map["admin"]

    for resource, action in [
        ("lab_documents", "view"),
        ("lab_documents", "manage"),
        ("lab_document_tags", "manage"),
        ("lab_glossary", "view"),
        ("lab_glossary", "manage"),
        ("lab_glossary", "delete"),
        ("sdr", "view"),
        ("sdr", "author"),
        ("sdr", "manage"),
    ]:
        assert await role_service.has_permission(session, admin_role, resource, action), (
            f"admin should have {resource}:{action}"
        )


@pytest.mark.asyncio
async def test_all_roles_can_view_lab_knowledge(session, admin_user):
    role_map = admin_user._test_role_map
    for role_name in ("admin", "comp_bio", "bench", "viewer"):
        for resource in ("lab_documents", "lab_glossary", "sdr"):
            assert await role_service.has_permission(session, role_map[role_name], resource, "view"), (
                f"{role_name} should have {resource}:view"
            )


@pytest.mark.asyncio
async def test_comp_bio_can_author_sdr_but_not_manage(session, admin_user):
    role_map = admin_user._test_role_map
    assert await role_service.has_permission(session, role_map["comp_bio"], "sdr", "author")
    assert not await role_service.has_permission(session, role_map["comp_bio"], "sdr", "manage")


@pytest.mark.asyncio
async def test_viewer_cannot_manage_documents_or_author_sdr(session, admin_user):
    role_map = admin_user._test_role_map
    assert not await role_service.has_permission(session, role_map["viewer"], "lab_documents", "manage")
    assert not await role_service.has_permission(session, role_map["viewer"], "lab_document_tags", "manage")
    assert not await role_service.has_permission(session, role_map["viewer"], "sdr", "author")
    assert not await role_service.has_permission(session, role_map["viewer"], "lab_glossary", "delete")


@pytest.mark.asyncio
async def test_fresh_seed_includes_lab_knowledge_view_for_bench(session, admin_user):
    """A newly seeded org (not the fixture's) also gets the defaults."""
    from app.models.organization import Organization

    org = Organization(name="Second Org", setup_complete=True)
    session.add(org)
    await session.flush()
    role_service.invalidate_cache()
    role_map = await seed_builtin_roles(session, org.id)
    await session.flush()
    assert await role_service.has_permission(session, role_map["bench"], "lab_documents", "view")
    assert await role_service.has_permission(session, role_map["bench"], "sdr", "view")
