"""Glossary terms surface in global search (AC-B13, F-LKB-09)."""

import pytest

from app.services.lab_glossary_service import LabGlossaryService
from app.services.search_service import FULL_SEARCH_TYPES, SearchService


@pytest.mark.asyncio
async def test_lab_glossary_term_is_a_full_search_type():
    assert "lab_glossary_term" in FULL_SEARCH_TYPES


@pytest.mark.asyncio
async def test_full_search_finds_glossary_term(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await LabGlossaryService.create_term(
        session,
        org_id=org_id,
        user_id=uid,
        term="Cryoprotectant",
        definition="A substance that protects cells during freezing.",
    )
    results, total, counts = await SearchService.full_search(
        session, org_id, "Cryoprotectant", entity_types=["lab_glossary_term"], count_types=["lab_glossary_term"]
    )
    assert total == 1
    assert results[0]["entity_type"] == "lab_glossary_term"
    assert results[0]["url"].startswith("/lab-knowledge/glossary")
    assert counts["lab_glossary_term"] == 1


@pytest.mark.asyncio
async def test_full_search_matches_definition_and_aliases(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await LabGlossaryService.create_term(
        session,
        org_id=org_id,
        user_id=uid,
        term="VAO",
        definition="Visually acceptable oocyte at intake QC.",
        aliases=["visually acceptable oocyte"],
    )
    by_def, total_def, _ = await SearchService.full_search(
        session, org_id, "intake QC", entity_types=["lab_glossary_term"], count_types=["lab_glossary_term"]
    )
    assert total_def == 1
    by_alias, total_alias, _ = await SearchService.full_search(
        session, org_id, "visually acceptable", entity_types=["lab_glossary_term"], count_types=["lab_glossary_term"]
    )
    assert total_alias == 1


@pytest.mark.asyncio
async def test_quick_search_finds_glossary_term(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Passage Number", definition="Subculture count"
    )
    hits = await SearchService.quick_search(session, org_id, "Passage")
    assert any(h["entity_type"] == "lab_glossary_term" and h["name"] == "Passage Number" for h in hits)
