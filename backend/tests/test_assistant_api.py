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


def _anthropic_tool_uses(*calls):
    """A single Anthropic response carrying SEVERAL parallel tool_use blocks (name, args)."""
    content = [
        {"type": "tool_use", "id": f"tu{i}", "name": name, "input": args} for i, (name, args) in enumerate(calls)
    ]
    return httpx.Response(200, json={"content": content, "stop_reason": "tool_use"})


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


async def test_confirm_create_experiment_plan_creates_the_experiment(client, session, admin_user, admin_token):
    """create_experiment is mutating, so it RUNS on confirm: the experiment is created in the org and
    executed=True is reported."""
    conv = AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id, status="active")
    session.add(conv)
    await session.flush()
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[{"tool": "create_experiment", "args": {"name": "Cortex E14.5 scRNA"}}],
        status="proposed",
    )
    session.add(plan)
    await session.flush()
    await session.commit()

    resp = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["executed"] is True
    assert body["result"]["name"] == "Cortex E14.5 scRNA"
    count = (
        await session.execute(
            select(func.count())
            .select_from(Experiment)
            .where(
                Experiment.organization_id == admin_user.organization_id,
                Experiment.name == "Cortex E14.5 scRNA",
            )
        )
    ).scalar_one()
    assert count == 1


async def test_assistant_created_experiment_appears_in_the_experiments_list(client, session, admin_user, admin_token):
    """Reproduces the live report: the assistant says it created the experiment, but the user does not
    see it in their Experiments list. Create via the assistant confirm path, then read the SAME endpoint
    the UI uses (GET /api/experiments) and assert the new experiment is present."""
    conv = AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id, status="active")
    session.add(conv)
    await session.flush()
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[{"tool": "create_experiment", "args": {"name": "Mouse Gut Serotonin Investigation"}}],
        status="proposed",
    )
    session.add(plan)
    await session.flush()
    await session.commit()

    confirm = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert confirm.status_code == 200, confirm.text
    new_id = confirm.json()["result"]["experiment_id"]

    listing = await client.get("/api/experiments", headers=_auth(admin_token))
    assert listing.status_code == 200, listing.text
    names = [e["name"] for e in listing.json()["experiments"]]
    ids = [e["id"] for e in listing.json()["experiments"]]
    assert new_id in ids, f"created experiment {new_id} missing from the list (ids={ids})"
    assert "Mouse Gut Serotonin Investigation" in names


async def test_confirm_create_sample_plan_creates_the_sample(client, session, admin_user, admin_token):
    """create_sample is mutating: confirming adds the sample (with its first-class assay) to the
    target experiment."""
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id, status="active")
    session.add(conv)
    await session.flush()
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[
            {
                "tool": "create_sample",
                "args": {"experiment_id": exp.id, "external_id": "C1", "organism": "Mus musculus", "assay": "scrna"},
            }
        ],
        status="proposed",
    )
    session.add(plan)
    await session.flush()
    await session.commit()

    resp = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["executed"] is True
    assert body["result"]["assay"] == "scrna"
    sample = (await session.execute(select(Sample).where(Sample.external_id == "C1"))).scalar_one()
    assert sample.experiment_id == exp.id
    assert sample.assay == "scrna"


async def test_multi_step_plan_confirms_and_runs_install_then_launch(client, session, admin_user, admin_token):
    """L3 capstone: the agent proposes install AND launch in ONE turn; that becomes a single
    two-step plan; one confirmation runs the install (mutating) and builds the launch (spend),
    in order, still creating NO PipelineRun in v1."""
    await _configure_anthropic(session, admin_user.organization_id, admin_user.id)
    exp = await _bulk_mouse_experiment(session, admin_user)  # seeds nf-core/rnaseq, NOT scrnaseq
    session.add(
        NfCoreRegistryPipeline(
            name="scrnaseq",
            full_name="nf-core/scrnaseq",
            description="single-cell RNA-seq",
            releases_json=[{"tag_name": "4.1.0", "published_at": "2024-01-01", "has_schema": True}],
            default_branch="master",
        )
    )
    await session.flush()
    await session.commit()

    async def _scrnaseq_installed():
        return (
            await session.execute(
                select(func.count())
                .select_from(PipelineCatalogEntry)
                .where(
                    PipelineCatalogEntry.organization_id == admin_user.organization_id,
                    PipelineCatalogEntry.pipeline_key == "nf-core/scrnaseq",
                )
            )
        ).scalar_one()

    assert await _scrnaseq_installed() == 0  # not installed yet

    create = await client.post("/api/assistant/conversations", json={}, headers=_auth(admin_token))
    conv_id = create.json()["id"]

    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        r.post("/messages").mock(
            side_effect=[
                _anthropic_tool_uses(
                    ("install", {"name": "scrnaseq"}),
                    ("launch_run", {"experiment_id": exp.id, "pipeline_key": "nf-core/scrnaseq"}),
                ),
            ]
        )
        msg = await client.post(
            f"/api/assistant/conversations/{conv_id}/messages",
            json={"text": "install nf-core/scrnaseq and run it on experiment 7"},
            headers=_auth(admin_token),
        )

    assert msg.status_code == 200
    body = msg.json()
    assert body["status"] == "awaiting_confirmation"
    # ONE plan spanning both steps, in order, surfaced for pre-confirm review.
    assert [s["tool"] for s in body["plan_steps"]] == ["install", "launch_run"]
    plan_id = body["action_plan_id"]
    assert await _scrnaseq_installed() == 0  # still nothing executed at plan time
    assert await _run_count(session) == 0

    with patch(
        "app.services.pipeline_catalog_service.PipelineCatalogService.fetch_pipeline_schema",
        new=AsyncMock(return_value={}),
    ):
        confirm = await client.post(f"/api/assistant/action-plans/{plan_id}/confirm", headers=_auth(admin_token))

    assert confirm.status_code == 200
    cbody = confirm.json()
    assert cbody["status"] == "approved"
    assert cbody["executed"] is True  # the install (mutating) ran
    # The install actually installed nf-core/scrnaseq, and the launch only built its request: no run.
    assert await _scrnaseq_installed() == 1
    assert await _run_count(session) == 0


async def test_confirm_fetchngs_launch_builds_request_with_accessions(client, session, admin_user, admin_token):
    """'Import by accession' is a fetchngs LAUNCH (spend). Confirm builds the launch request carrying
    the accessions in parameters; v1 does NOT execute it (no PipelineRun created)."""
    conv = AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id, status="active")
    session.add(conv)
    await session.flush()
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[
            {
                "tool": "launch_run",
                "args": {
                    "experiment_id": 1,
                    "pipeline_key": "nf-core/fetchngs",
                    "accessions": ["GSE1", "SRR2"],
                },
            }
        ],
        status="proposed",
    )
    session.add(plan)
    await session.flush()
    await session.commit()

    resp = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["executed"] is False  # spend: built, not run in v1
    assert body["result"]["pipeline_key"] == "nf-core/fetchngs"
    assert body["result"]["parameters"]["accessions"] == ["GSE1", "SRR2"]
    assert await _run_count(session) == 0


# ---- Assistant settings (per-org launch toggle) ----


async def test_get_assistant_settings_defaults_false(client, admin_token):
    resp = await client.get("/api/assistant/settings", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json()["launch_enabled"] is False


async def test_put_assistant_settings_enables_launch(client, admin_token):
    resp = await client.put("/api/assistant/settings", json={"launch_enabled": True}, headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json()["launch_enabled"] is True
    # Persisted: a fresh GET reflects it.
    get = await client.get("/api/assistant/settings", headers=_auth(admin_token))
    assert get.json()["launch_enabled"] is True


async def test_put_assistant_settings_forbidden_without_settings_configure(client, session, admin_user):
    bench, bench_token = await _bench_user_token(session, admin_user)
    # Bench can SEE the setting (assistant:use) but cannot change it (no settings:configure).
    assert (await client.get("/api/assistant/settings", headers=_auth(bench_token))).status_code == 200
    resp = await client.put("/api/assistant/settings", json={"launch_enabled": True}, headers=_auth(bench_token))
    assert resp.status_code == 403


async def test_confirm_launches_real_run_when_org_launch_enabled(client, session, admin_user, admin_token):
    """With the per-org assistant_launch_enabled toggle ON, confirming a launch plan ACTUALLY
    creates a PipelineRun via the normal launch path (executed=True, run_id surfaced) instead of
    only building the request. Uses a fetchngs launch so no per-sample files are required; conftest
    pins BIOAF_COMPUTE_MODE=local, so the compute adapter is the in-memory stub (no real spend)."""
    from app.models.organization import Organization

    org = await session.get(Organization, admin_user.organization_id)
    org.assistant_launch_enabled = True  # opt this org in to real launches
    exp = Experiment(
        organization_id=admin_user.organization_id, name="Import", owner_user_id=admin_user.id, status="registered"
    )
    session.add(exp)
    session.add(
        PipelineCatalogEntry(
            organization_id=admin_user.organization_id,
            pipeline_key="nf-core/fetchngs",
            name="nf-core/fetchngs",
            source_type="nf-core",
            version="1.12.0",
            default_params_json={},
            enabled=True,
        )
    )
    await session.flush()
    conv = AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id, status="active")
    session.add(conv)
    await session.flush()
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[
            {
                "tool": "launch_run",
                "args": {"experiment_id": exp.id, "pipeline_key": "nf-core/fetchngs", "accessions": ["SRR1"]},
            }
        ],
        status="proposed",
    )
    session.add(plan)
    await session.flush()
    await session.commit()

    assert await _run_count(session) == 0
    resp = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    assert body["executed"] is True  # spend actually ran now (toggle ON)
    assert body["result"]["launched"] is True
    assert body["run_id"]  # a real run id is surfaced
    assert await _run_count(session) == 1  # a PipelineRun was created


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


async def test_confirm_launch_scopes_to_requested_sample_ids(client, session, admin_user, admin_token):
    """Reproduces the live-test failure: a user asks to run on ONE sample, but a launch with no
    sample_ids defaults to EVERY sample in the experiment and fails the per-sample-FASTQ check when a
    sibling sample has no files ('Some selected samples have no linked input files'). With sample_ids
    threaded through confirm, the launch is scoped to just the requested sample (which HAS files) and
    succeeds, and the run is linked to exactly that sample."""
    from app.models.file import File
    from app.models.organization import Organization
    from app.models.pipeline_run import PipelineRunSample
    from app.models.sample import sample_files

    org = await session.get(Organization, admin_user.organization_id)
    org.assistant_launch_enabled = True
    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="scRNA mixed",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    session.add(
        PipelineCatalogEntry(
            organization_id=admin_user.organization_id,
            pipeline_key="nf-core/scrnaseq",
            name="nf-core/scrnaseq",
            source_type="nf-core",
            version="4.1.0",
            default_params_json={},
            enabled=True,
        )
    )
    await session.flush()

    # Sample WITH linked FASTQ (the one the user named) and a sibling WITHOUT files.
    sample_with_files = Sample(experiment_id=exp.id, external_id="101", organism="Homo sapiens")
    sample_no_files = Sample(experiment_id=exp.id, external_id="102", organism="Homo sapiens")
    session.add_all([sample_with_files, sample_no_files])
    await session.flush()
    for read in ("R1", "R2"):
        f = File(
            organization_id=exp.organization_id,
            experiment_id=exp.id,
            gcs_uri=f"gs://bucket/101_{read}.fastq.gz",
            filename=f"101_{read}.fastq.gz",
            file_type="fastq",
        )
        session.add(f)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample_with_files.id, file_id=f.id))

    conv = AssistantConversation(organization_id=admin_user.organization_id, user_id=admin_user.id, status="active")
    session.add(conv)
    await session.flush()
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[
            {
                "tool": "launch_run",
                "args": {
                    "experiment_id": exp.id,
                    "pipeline_key": "nf-core/scrnaseq",
                    "sample_ids": [sample_with_files.id],
                    "reference_genome": "GRCh38",
                },
            }
        ],
        status="proposed",
    )
    session.add(plan)
    await session.flush()
    await session.commit()

    resp = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executed"] is True
    assert body["run_id"]  # the scoped launch succeeded despite sibling 102 having no files
    # The run is linked to exactly the requested sample, not the whole experiment.
    linked = (
        await session.execute(
            select(PipelineRunSample.sample_id).where(PipelineRunSample.pipeline_run_id == body["run_id"])
        )
    ).scalars()
    assert set(linked) == {sample_with_files.id}
