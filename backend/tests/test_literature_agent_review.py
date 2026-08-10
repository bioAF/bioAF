"""Literature input to Agent Review (ADR-057).

Covers the payload builder (toggles resolution, ordering, token cap,
abstracts/comments rendering) and the DOI rewrite post-processor."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.models.literature import (
    AgentReviewLiteratureConfig,
    LiteratureAssociation,
    LiteraturePaperComment,
    LiteraturePaperDismissal,
    PROVENANCE_LIT_REVIEW_RUN,
    PROVENANCE_USER_UPLOAD,
    SCOPE_EXPERIMENT,
)
from app.services.literature import agent_review_payload, paper_service


async def _make_experiment(session, admin_user):
    result = await session.execute(
        text(
            """
            INSERT INTO experiments (name, status, organization_id, owner_user_id, project_id)
            VALUES ('Lit Test Exp', 'registered', :org, :uid, NULL)
            RETURNING id
            """
        ).bindparams(org=admin_user.organization_id, uid=admin_user.id)
    )
    eid = result.scalar_one()
    await session.commit()
    return eid


async def _add_paper(session, admin_user, *, title, doi, provenance=PROVENANCE_USER_UPLOAD, pub_year=2024):
    paper = await paper_service.create_paper(
        session,
        org_id=admin_user.organization_id,
        user_id=admin_user.id,
        title=title,
        authors=[{"family": "Chen", "given": "Sarah"}],
        doi=doi,
        journal="Nature",
        publication_date=date(pub_year, 5, 1),
        abstract=f"Abstract for {title}.",
        provenance=provenance,
        source="upload" if provenance == PROVENANCE_USER_UPLOAD else "pubmed",
    )
    await session.commit()
    return paper


async def _associate(session, paper, experiment_id, admin_user):
    session.add(
        LiteratureAssociation(
            paper_id=paper.id,
            scope_type=SCOPE_EXPERIMENT,
            scope_id=experiment_id,
            added_by_user_id=admin_user.id,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_payload_builder_renders_papers_and_comments(session, admin_user):
    eid = await _make_experiment(session, admin_user)
    paper = await _add_paper(session, admin_user, title="TGF-beta in TNBC", doi="10.1/lit-1")
    await _associate(session, paper, eid, admin_user)
    session.add(LiteraturePaperComment(paper_id=paper.id, user_id=admin_user.id, body="Worth re-reading."))
    await session.commit()

    result = await agent_review_payload.build_literature_payload(
        session,
        org_id=admin_user.organization_id,
        scope_type="experiment",
        scope_id=eid,
    )
    assert paper.id in result.included_paper_ids
    assert "Associated Literature" in result.markdown
    assert "TGF-beta in TNBC" in result.markdown
    assert "Worth re-reading" in result.markdown


@pytest.mark.asyncio
async def test_payload_builder_orders_uploaded_with_comments_first(session, admin_user):
    eid = await _make_experiment(session, admin_user)
    plain_upload = await _add_paper(session, admin_user, title="Plain Upload", doi="10.1/p1")
    commented_upload = await _add_paper(session, admin_user, title="With Comment", doi="10.1/p2")
    from_run = await _add_paper(
        session,
        admin_user,
        title="From Run",
        doi="10.1/p3",
        provenance=PROVENANCE_LIT_REVIEW_RUN,
    )
    await _associate(session, plain_upload, eid, admin_user)
    await _associate(session, commented_upload, eid, admin_user)
    await _associate(session, from_run, eid, admin_user)

    session.add(LiteraturePaperComment(paper_id=commented_upload.id, user_id=admin_user.id, body="annotated"))
    await session.commit()

    result = await agent_review_payload.build_literature_payload(
        session,
        org_id=admin_user.organization_id,
        scope_type="experiment",
        scope_id=eid,
    )
    assert result.included_paper_ids[0] == commented_upload.id
    # plain_upload is tier 2; from_run is tier 4
    assert result.included_paper_ids.index(plain_upload.id) < result.included_paper_ids.index(from_run.id)


@pytest.mark.asyncio
async def test_payload_builder_skips_when_all_toggles_off(session, admin_user):
    eid = await _make_experiment(session, admin_user)
    paper = await _add_paper(session, admin_user, title="X", doi="10.1/x")
    await _associate(session, paper, eid, admin_user)

    session.add(
        AgentReviewLiteratureConfig(
            organization_id=admin_user.organization_id,
            scope_type="org",
            scope_id=None,
            abstracts_enabled=False,
            comments_enabled=False,
            full_text_enabled=False,
            updated_by_user_id=admin_user.id,
        )
    )
    await session.commit()

    result = await agent_review_payload.build_literature_payload(
        session,
        org_id=admin_user.organization_id,
        scope_type="experiment",
        scope_id=eid,
    )
    assert result.markdown == ""
    assert result.included_paper_ids == []


@pytest.mark.asyncio
async def test_payload_builder_excludes_dismissed_papers(session, admin_user):
    eid = await _make_experiment(session, admin_user)
    paper = await _add_paper(session, admin_user, title="Dismissed Paper", doi="10.1/dis")
    await _associate(session, paper, eid, admin_user)
    session.add(
        LiteraturePaperDismissal(
            paper_id=paper.id,
            organization_id=admin_user.organization_id,
            dismissed_by_user_id=admin_user.id,
        )
    )
    await session.commit()

    result = await agent_review_payload.build_literature_payload(
        session,
        org_id=admin_user.organization_id,
        scope_type="experiment",
        scope_id=eid,
    )
    assert paper.id not in result.included_paper_ids


@pytest.mark.asyncio
async def test_payload_builder_truncates_at_token_cap(session, admin_user):
    eid = await _make_experiment(session, admin_user)
    p1 = await _add_paper(session, admin_user, title="A first paper", doi="10.1/t1")
    p2 = await _add_paper(session, admin_user, title="A second paper", doi="10.1/t2")
    await _associate(session, p1, eid, admin_user)
    await _associate(session, p2, eid, admin_user)

    # Set a tiny token cap so only one paper fits.
    session.add(
        AgentReviewLiteratureConfig(
            organization_id=admin_user.organization_id,
            scope_type="org",
            scope_id=None,
            abstracts_enabled=True,
            comments_enabled=True,
            full_text_enabled=False,
            max_tokens=70,
            updated_by_user_id=admin_user.id,
        )
    )
    await session.commit()

    result = await agent_review_payload.build_literature_payload(
        session,
        org_id=admin_user.organization_id,
        scope_type="experiment",
        scope_id=eid,
    )
    assert len(result.included_paper_ids) == 1
    assert len(result.truncated_paper_ids) == 1
    assert "truncated" in result.markdown.lower()


@pytest.mark.asyncio
async def test_payload_full_text_shows_page_marker_note(session, admin_user, monkeypatch):
    """When full text is enabled, the rendered block tells the LLM how page
    markers work so it can cite the page (spec-automation, Feature C)."""
    eid = await _make_experiment(session, admin_user)
    paper = await _add_paper(session, admin_user, title="Full Text Paper", doi="10.1/ft")
    await _associate(session, paper, eid, admin_user)

    session.add(
        AgentReviewLiteratureConfig(
            organization_id=admin_user.organization_id,
            scope_type="org",
            scope_id=None,
            abstracts_enabled=True,
            comments_enabled=True,
            full_text_enabled=True,
            updated_by_user_id=admin_user.id,
        )
    )
    await session.commit()

    async def fake_full_text(_session, _paper):
        return "[Page 1]\nIntro text.\n\n[Page 2]\nResults that matter."

    monkeypatch.setattr(agent_review_payload, "_load_full_text", fake_full_text)

    result = await agent_review_payload.build_literature_payload(
        session,
        org_id=admin_user.organization_id,
        scope_type="experiment",
        scope_id=eid,
    )
    assert "[Page N]" in result.markdown
    assert "cite the page" in result.markdown.lower()
    assert "[Page 2]" in result.markdown
    # The header also instructs flagging unexpected/contradictory results.
    assert "unexpected" in result.markdown.lower()


@pytest.mark.asyncio
async def test_doi_rewrite_to_library_links(session, admin_user):
    paper = await _add_paper(session, admin_user, title="Cited Paper", doi="10.1038/s41592-cite-1")
    text_in = (
        "The reference 10.1038/s41592-cite-1 supports this hypothesis, while 10.9999/unknown is "
        "not in the local library."
    )
    out = await agent_review_payload.rewrite_dois_to_library_links(
        session, org_id=admin_user.organization_id, text=text_in
    )
    assert f"/lab-knowledge/literature/papers/{paper.id}" in out
    assert "https://doi.org/10.9999/unknown" in out


@pytest.mark.asyncio
async def test_agent_review_config_api_round_trip(client, admin_token, admin_user, session):
    headers = {"Authorization": f"Bearer {admin_token}"}
    # Default config returns the built-in defaults.
    r1 = await client.get("/api/literature/agent-review-config", headers=headers)
    assert r1.status_code == 200
    body = r1.json()
    assert body["abstracts_enabled"] is True
    assert body["full_text_enabled"] is False

    r2 = await client.put(
        "/api/literature/agent-review-config",
        json={"full_text_enabled": True, "max_tokens": 50_000},
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["full_text_enabled"] is True
    assert r2.json()["max_tokens"] == 50_000

    r3 = await client.get("/api/literature/agent-review-config", headers=headers)
    assert r3.json()["full_text_enabled"] is True
    assert r3.json()["max_tokens"] == 50_000
