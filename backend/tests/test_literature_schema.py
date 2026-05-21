"""Schema-level tests for the Literature Library (ADR-056, migration 083).

Covers:
- Model registration and basic write/read for every literature_* table.
- DOI uniqueness per org and the title+author fallback dedup constraint.
- Partial-unique index on literature_associations (re-add after soft-removal).
- Permission bootstrap: built-in roles get the right literature actions seeded.
- bootstrap_literature_sources seeds the four sources for a new org.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import (
    EXTERNAL_SOURCES,
    EXTRACTION_NONE,
    LiteratureAssociation,
    LiteraturePaper,
    LiteraturePaperComment,
    LiteraturePaperDismissal,
    LiteraturePaperReadingStatus,
    LiteratureSourcesConfig,
    PROVENANCE_USER_UPLOAD,
    READING_READING,
    READING_READ,
    SCOPE_GLOBAL,
    SOURCE_PUBMED,
    derive_bucket,
)
from app.services import role_service
from app.services.bootstrap_literature import seed_literature_sources


def _make_paper(org_id: int, *, doi: str | None = None, title: str = "A Title", added_by: int) -> LiteraturePaper:
    return LiteraturePaper(
        organization_id=org_id,
        doi=doi,
        title=title,
        title_normalized=title.lower(),
        authors_json=[{"given": "Sarah", "family": "Chen"}],
        first_author_key="ChenS",
        last_author_key="ChenS",
        provenance=PROVENANCE_USER_UPLOAD,
        added_by_user_id=added_by,
        source="upload",
        extraction_status=EXTRACTION_NONE,
    )


@pytest.mark.asyncio
async def test_paper_insert_minimal(session: AsyncSession, admin_user):
    paper = _make_paper(admin_user.organization_id, doi="10.1000/x.1", added_by=admin_user.id)
    session.add(paper)
    await session.commit()
    assert paper.id is not None
    assert paper.extraction_status == EXTRACTION_NONE


@pytest.mark.asyncio
async def test_paper_doi_unique_per_org(session: AsyncSession, admin_user):
    session.add(_make_paper(admin_user.organization_id, doi="10.1000/dup", added_by=admin_user.id))
    await session.commit()
    session.add(_make_paper(admin_user.organization_id, doi="10.1000/dup", added_by=admin_user.id))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_paper_title_author_fallback_unique(session: AsyncSession, admin_user):
    session.add(_make_paper(admin_user.organization_id, doi=None, title="Same Title", added_by=admin_user.id))
    await session.commit()
    session.add(_make_paper(admin_user.organization_id, doi=None, title="Same Title", added_by=admin_user.id))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_comment_thread(session: AsyncSession, admin_user):
    paper = _make_paper(admin_user.organization_id, doi="10.1/c", added_by=admin_user.id)
    session.add(paper)
    await session.flush()
    parent = LiteraturePaperComment(paper_id=paper.id, user_id=admin_user.id, body="top level")
    session.add(parent)
    await session.flush()
    reply = LiteraturePaperComment(paper_id=paper.id, user_id=admin_user.id, parent_id=parent.id, body="a reply")
    session.add(reply)
    await session.commit()
    assert reply.parent_id == parent.id


@pytest.mark.asyncio
async def test_association_active_unique_prevents_duplicate(session: AsyncSession, admin_user):
    paper = _make_paper(admin_user.organization_id, doi="10.1/a", added_by=admin_user.id)
    session.add(paper)
    await session.flush()
    paper_id = paper.id
    a1 = LiteratureAssociation(
        paper_id=paper_id,
        scope_type=SCOPE_GLOBAL,
        scope_id=None,
        added_by_user_id=admin_user.id,
    )
    session.add(a1)
    await session.commit()
    a_dup = LiteratureAssociation(
        paper_id=paper_id,
        scope_type=SCOPE_GLOBAL,
        scope_id=None,
        added_by_user_id=admin_user.id,
    )
    session.add(a_dup)
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_association_reusable_after_remove(db_engine, admin_user):
    """A new active association can be created once the prior one is soft-removed."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as s:
        paper = _make_paper(admin_user.organization_id, doi="10.1/areuse", added_by=admin_user.id)
        s.add(paper)
        await s.flush()
        paper_id = paper.id
        a1 = LiteratureAssociation(
            paper_id=paper_id,
            scope_type=SCOPE_GLOBAL,
            scope_id=None,
            added_by_user_id=admin_user.id,
        )
        s.add(a1)
        await s.commit()
        a1_id = a1.id

    async with factory() as s:
        await s.execute(
            sa_text("UPDATE literature_associations SET removed_at = now() WHERE id = :id").bindparams(id=a1_id)
        )
        await s.commit()

    async with factory() as s:
        a2 = LiteratureAssociation(
            paper_id=paper_id,
            scope_type=SCOPE_GLOBAL,
            scope_id=None,
            added_by_user_id=admin_user.id,
        )
        s.add(a2)
        await s.commit()
        assert a2.id != a1_id


@pytest.mark.asyncio
async def test_reading_status_upsert_pattern(session: AsyncSession, admin_user):
    paper = _make_paper(admin_user.organization_id, doi="10.1/r", added_by=admin_user.id)
    session.add(paper)
    await session.flush()
    rs = LiteraturePaperReadingStatus(paper_id=paper.id, user_id=admin_user.id, status=READING_READING)
    session.add(rs)
    await session.commit()
    rs.status = READING_READ
    await session.commit()
    result = await session.execute(
        select(LiteraturePaperReadingStatus).where(LiteraturePaperReadingStatus.paper_id == paper.id)
    )
    row = result.scalar_one()
    assert row.status == READING_READ


@pytest.mark.asyncio
async def test_paper_dismissal_and_reverse(session: AsyncSession, admin_user):
    paper = _make_paper(admin_user.organization_id, doi="10.1/d", added_by=admin_user.id)
    session.add(paper)
    await session.flush()
    dismissal = LiteraturePaperDismissal(
        paper_id=paper.id,
        organization_id=admin_user.organization_id,
        dismissed_by_user_id=admin_user.id,
        reason="off topic",
    )
    session.add(dismissal)
    await session.commit()
    assert dismissal.reversed_at is None


def test_derive_bucket_thresholds():
    assert derive_bucket(0.99) == "high"
    assert derive_bucket(0.66) == "high"
    assert derive_bucket(0.5) == "medium"
    assert derive_bucket(0.33) == "medium"
    assert derive_bucket(0.32999) == "low"
    assert derive_bucket(0.0) == "low"


@pytest.mark.asyncio
async def test_bootstrap_literature_sources_seeds_four(session: AsyncSession, admin_user):
    # admin_user fixture already seeds roles; literature sources are not seeded
    # automatically by that fixture (only the bootstrap API/CLI path does).
    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()
    result = await session.execute(
        select(LiteratureSourcesConfig.source).where(
            LiteratureSourcesConfig.organization_id == admin_user.organization_id
        )
    )
    sources = {row[0] for row in result.fetchall()}
    assert sources == set(EXTERNAL_SOURCES)


@pytest.mark.asyncio
async def test_bootstrap_literature_sources_idempotent(session: AsyncSession, admin_user):
    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()
    # Second call must not duplicate rows.
    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()
    result = await session.execute(
        select(LiteratureSourcesConfig).where(LiteratureSourcesConfig.organization_id == admin_user.organization_id)
    )
    rows = list(result.scalars().all())
    assert len(rows) == len(EXTERNAL_SOURCES)


@pytest.mark.asyncio
async def test_admin_role_has_all_literature_permissions(session: AsyncSession, admin_user):
    role_id = admin_user.role_id
    expected = {
        "view",
        "upload",
        "comment",
        "associate",
        "delete_own_comment",
        "delete_any_comment",
        "delete_paper",
        "dismiss",
        "reverse_dismiss",
        "run_search",
        "run_lit_review",
        "configure_sources",
    }
    for action in expected:
        assert await role_service.has_permission(session, role_id, "literature", action), action


@pytest.mark.asyncio
async def test_viewer_role_has_view_only(session: AsyncSession, admin_user, viewer_user):
    role_id = viewer_user.role_id
    assert await role_service.has_permission(session, role_id, "literature", "view")
    for action in ("upload", "comment", "dismiss", "run_search", "run_lit_review"):
        assert not await role_service.has_permission(session, role_id, "literature", action), action


@pytest.mark.asyncio
async def test_bench_role_has_basic_actions(session: AsyncSession, admin_user):
    # Create a bench user via the existing role_map on admin.
    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    role_id = role_map["bench"]
    assert await role_service.has_permission(session, role_id, "literature", "view")
    assert await role_service.has_permission(session, role_id, "literature", "upload")
    assert await role_service.has_permission(session, role_id, "literature", "run_search")
    assert not await role_service.has_permission(session, role_id, "literature", "dismiss")
    assert not await role_service.has_permission(session, role_id, "literature", "run_lit_review")


def test_migration_086_adds_automation_columns_and_seeds_rule():
    """086 adds the run trigger + the three org cadence columns and seeds an
    in-app notification rule for the auto-review event, with a clean downgrade."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parent.parent / "alembic" / "versions" / "086_literature_automation.py"
    ).read_text()

    # Upgrade adds every column.
    assert 'add_column(\n        "literature_review_runs"' in text or "literature_review_runs" in text
    assert "trigger" in text
    assert "lit_review_auto_enabled" in text
    assert "lit_review_auto_cadence" in text
    assert "lit_review_max_runs_per_tick" in text
    # Seeds the in-app rule for the auto-review event for existing orgs.
    assert "literature.auto_review_recommendations" in text
    assert "notification_rules" in text
    assert "'in_app'" in text
    # Downgrade reverses both the columns and the rule.
    assert "drop_column" in text
    assert "DELETE FROM notification_rules" in text


def test_migration_084_qualifies_ambiguous_columns():
    """Migration 084's UPDATE joins two tables that both define created_at.

    Asserts the SQL qualifies created_at (and other join-table columns) so
    Postgres does not raise AmbiguousColumnError. Catches the exact class of
    bug seen on the demo instance when 083 -> 084 ran.
    """
    import re
    from pathlib import Path

    text = (
        Path(__file__).resolve().parent.parent / "alembic" / "versions" / "084_literature_in_library.py"
    ).read_text()

    # Pull the UPDATE block that joins literature_recommendations and
    # literature_review_runs (the one that backfills 'accepted').
    update_re = re.compile(
        r"UPDATE\s+literature_recommendations[\s\S]+?literature_review_runs[\s\S]+?status\s*=\s*'pending'",
        re.IGNORECASE,
    )
    matches = update_re.findall(text)
    assert matches, "migration 084 should contain the recommendations backfill UPDATE"
    sql = matches[0]

    # Any reference to a column defined on both tables must be qualified.
    # created_at exists on both literature_recommendations and literature_review_runs.
    bare_created_at = re.search(r"(?<![\w.])created_at(?![\w.])", sql)
    assert bare_created_at is None, (
        "migration 084 has an unqualified 'created_at' reference; both tables "
        "in the UPDATE define this column and Postgres will raise "
        "AmbiguousColumnError. Use literature_recommendations.created_at."
    )


@pytest.mark.asyncio
async def test_sources_config_api_key_encrypted(session: AsyncSession, admin_user):
    cfg = LiteratureSourcesConfig(
        organization_id=admin_user.organization_id,
        source=SOURCE_PUBMED,
        enabled=True,
        api_key="secret-test-key",
    )
    session.add(cfg)
    await session.commit()
    # The Python value round-trips...
    await session.refresh(cfg)
    assert cfg.api_key == "secret-test-key"
    # ...but the raw column value is ciphertext (Fernet token), not plaintext.
    raw = await session.execute(
        sa_text("SELECT api_key FROM literature_sources_config WHERE id = :id").bindparams(id=cfg.id)
    )
    stored = raw.scalar_one()
    assert stored != "secret-test-key"
    assert "secret-test-key" not in (stored or "")
