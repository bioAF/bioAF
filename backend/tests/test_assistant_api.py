"""Tests for the Assistant HTTP API: conversations, messages (runs the loop), confirm.

This is the Phase 1 capstone: it exercises the whole spine through HTTP. The headline test
goes intent -> recommend_pipeline -> proposed plan -> confirm -> fully-formed launch request,
entirely through the tool layer, with the provider mocked via respx and NO PipelineRun ever
created. Confirm re-checks the underlying tool permission, so a bench user (can use the
assistant, cannot launch) is denied.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.models.assistant import AssistantActionPlan, AssistantConversation
from app.models.experiment import Experiment
from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.user import User
from app.services import llm_provider_config_service
from app.services.auth_service import AuthService
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


# ---- Helpers ----


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _configure_anthropic(session, org_id, user_id):
    await llm_provider_config_service.upsert(
        session, org_id=org_id, provider="anthropic", api_key="sk-ant-LAST5", model="claude-x", actor_user_id=user_id
    )
    await llm_provider_config_service.set_active(session, org_id=org_id, provider="anthropic", actor_user_id=user_id)
    await session.commit()


async def _bulk_mouse_experiment(session, user):
    exp = Experiment(
        organization_id=user.organization_id, name="Bulk mouse", owner_user_id=user.id, status="fastq_uploaded"
    )
    session.add(exp)
    await session.flush()
    session.add(
        Sample(
            experiment_id=exp.id,
            external_id="B1",
            organism="Mus musculus",
            molecule_type="total RNA",
            library_prep_method="TruSeq Stranded mRNA",
        )
    )
    session.add(
        PipelineCatalogEntry(
            organization_id=user.organization_id,
            pipeline_key="nf-core/rnaseq",
            name="nf-core/rnaseq",
            source_type="github",
            version="3.14.0",
            default_params_json={"aligner": "star_salmon"},
            enabled=True,
        )
    )
    await session.flush()
    await session.commit()
    return exp


async def _bench_user_token(session, admin_user):
    role_map = admin_user._test_role_map
    user = User(
        email="bench@test.com",
        password_hash=AuthService.hash_password("benchpass123"),
        role_id=role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name="bench")
    return user, token


async def _run_count(session):
    return (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one()


def _anthropic_tool_use(name, args):
    return httpx.Response(
        200,
        json={"content": [{"type": "tool_use", "id": "tu", "name": name, "input": args}], "stop_reason": "tool_use"},
    )


# ---- Conversations ----


async def test_create_conversation(client, session, admin_user, admin_token):
    await _configure_anthropic(session, admin_user.organization_id, admin_user.id)
    resp = await client.post("/api/assistant/conversations", json={"title": "my run"}, headers=_auth(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    assert body["id"] > 0


async def test_create_conversation_forbidden_for_viewer(client, viewer_token):
    resp = await client.post("/api/assistant/conversations", json={"title": "x"}, headers=_auth(viewer_token))
    assert resp.status_code == 403


# ---- Messages (runs the loop) + confirm ----


async def test_message_reaches_plan_then_confirm_builds_launch_request(client, session, admin_user, admin_token):
    await _configure_anthropic(session, admin_user.organization_id, admin_user.id)
    exp = await _bulk_mouse_experiment(session, admin_user)

    create = await client.post("/api/assistant/conversations", json={}, headers=_auth(admin_token))
    conv_id = create.json()["id"]

    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        r.post("/messages").mock(
            side_effect=[
                _anthropic_tool_use("recommend_pipeline", {"experiment_id": exp.id}),
                _anthropic_tool_use("launch_run", {"experiment_id": exp.id, "pipeline_key": "nf-core/rnaseq"}),
            ]
        )
        msg = await client.post(
            f"/api/assistant/conversations/{conv_id}/messages",
            json={"text": "run differential expression on experiment 7"},
            headers=_auth(admin_token),
        )

    assert msg.status_code == 200
    body = msg.json()
    assert body["status"] == "awaiting_confirmation"
    plan_id = body["action_plan_id"]
    assert plan_id is not None
    # The proposed plan is surfaced so the confirm UI can show WHAT is being confirmed before
    # the user clicks (spec-03 / ADR-067: catch "not that sample" before spend).
    assert body["plan_steps"]
    assert body["plan_steps"][0]["tool"] == "launch_run"
    assert body["plan_steps"][0]["args"]["pipeline_key"] == "nf-core/rnaseq"
    assert await _run_count(session) == 0  # nothing launched

    confirm = await client.post(f"/api/assistant/action-plans/{plan_id}/confirm", headers=_auth(admin_token))
    assert confirm.status_code == 200
    cbody = confirm.json()
    assert cbody["status"] == "approved"
    assert cbody["executed"] is False  # spend: built, not executed in v1
    assert cbody["result"]["pipeline_key"] == "nf-core/rnaseq"
    # The load-bearing constraint: confirm builds the request but does NOT launch in v1.
    assert await _run_count(session) == 0


async def test_message_forbidden_for_viewer(client, viewer_token, session, admin_user):
    # A conversation the viewer should not be able to message (and viewer lacks assistant:use anyway).
    conv = AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id, status="active")
    session.add(conv)
    await session.flush()
    await session.commit()
    resp = await client.post(
        f"/api/assistant/conversations/{conv.id}/messages", json={"text": "hi"}, headers=_auth(viewer_token)
    )
    assert resp.status_code == 403


async def test_confirm_install_plan_executes_the_install(client, session, admin_user, admin_token):
    """A mutating action (install) actually RUNS on confirm, unlike a v1 spend action which only
    builds. Confirming an install plan installs the pipeline into the org catalog and reports
    executed=True."""
    session.add(
        NfCoreRegistryPipeline(
            name="scrnaseq",
            full_name="nf-core/scrnaseq",
            description="single-cell RNA-seq",
            releases_json=[{"tag_name": "4.1.0", "published_at": "2024-01-01", "has_schema": True}],
            default_branch="master",
        )
    )
    conv = AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id, status="active")
    session.add(conv)
    await session.flush()
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[{"tool": "install", "args": {"name": "scrnaseq"}}],
        status="proposed",
    )
    session.add(plan)
    await session.flush()
    await session.commit()

    # Stub the network schema fetch so install stays hermetic.
    with patch(
        "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
        new=AsyncMock(return_value={}),
    ):
        resp = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["executed"] is True
    assert body["result"]["pipeline_key"] == "nf-core/scrnaseq"
    count = (
        await session.execute(
            select(func.count())
            .select_from(PipelineCatalogEntry)
            .where(
                PipelineCatalogEntry.organization_id == admin_user.organization_id,
                PipelineCatalogEntry.pipeline_key == "nf-core/scrnaseq",
            )
        )
    ).scalar_one()
    assert count == 1


async def test_confirm_denied_without_launch_permission(client, session, admin_user):
    bench, bench_token = await _bench_user_token(session, admin_user)
    conv = AssistantConversation(organization_id=bench.organization_id, user_id=bench.id, status="active")
    session.add(conv)
    await session.flush()
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[{"tool": "launch_run", "args": {"experiment_id": 1, "pipeline_key": "nf-core/rnaseq"}}],
        status="proposed",
    )
    session.add(plan)
    await session.flush()
    await session.commit()

    resp = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(bench_token))
    assert resp.status_code == 403
    assert await _run_count(session) == 0
