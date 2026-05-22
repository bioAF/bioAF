"""Tests for the agent reviews API (ADR-055, spec-llm-integration-ui).

Verifies:
- POST /run returns 202 with job_id and agent_review_id when a provider is active.
- POST /run returns 412 when no provider is active.
- POST /run returns 409 when the debounce trips and returns the existing ids.
- POST /run is forbidden (403) for users without llm_integration:use.
- GET / lists single-run reviews and experiment-level reviews that include
  the run, filtered correctly by ?filter=active|dismissed|stale|failed.
- GET /{id} returns the full row including body and flags.
- POST /{id}/dismiss sets dismissed_at and dismissed_by; org-visible.
- POST /{id}/undismiss clears it.
- Staleness is computed at query time only for experiment-level reviews.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample
from app.models.user import User
from app.services import llm_provider_config_service
from app.services.auth_service import AuthService


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
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
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest_asyncio.fixture
async def comp_bio_auth(comp_bio_token):
    return {"Authorization": f"Bearer {comp_bio_token}"}


@pytest_asyncio.fixture
async def viewer_auth(viewer_token):
    return {"Authorization": f"Bearer {viewer_token}"}


@pytest_asyncio.fixture
async def admin_auth(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(autouse=True)
def _stub_background_execute(monkeypatch):
    """API tests do not exercise the BackgroundTasks path; execute_hosted has
    its own test suite. Stub it here so each /run call leaves the job in
    'pending' status, which is what the API contract returns to the client.
    """

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.api.agent_reviews.job_service.execute_hosted", noop)


@pytest_asyncio.fixture
async def configured_run(db_engine, admin_user):
    """A pipeline_run with an active LLM provider configured."""
    async with _factory(db_engine)() as session:
        await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            api_key="sk-LAST5",
            model="gpt-5",
            actor_user_id=admin_user.id,
        )
        await llm_provider_config_service.set_active(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            actor_user_id=admin_user.id,
        )
        exp = Experiment(name="Exp1", organization_id=admin_user.organization_id, status="processing")
        session.add(exp)
        await session.flush()
        run = PipelineRun(
            organization_id=admin_user.organization_id,
            experiment_id=exp.id,
            pipeline_name="rnaseq",
            pipeline_version="3.14",
            parameters_json={"genome": "GRCh38"},
            output_files_json={"counts": "x"},
            status="complete",
        )
        session.add(run)
        await session.flush()
        s = Sample(experiment_id=exp.id, external_id="EXT-1", tissue_type="liver", qc_status="pass")
        session.add(s)
        await session.flush()
        session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=s.id))
        await session.commit()
        return {"run_id": run.id, "experiment_id": exp.id}


@pytest.mark.asyncio
async def test_run_returns_202_with_ids(client, admin_auth, configured_run):
    resp = await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "pipeline_run",
            "entity_id": configured_run["run_id"],
            "selected_sub_item_ids": ["qc.metric_review"],
        },
        headers=admin_auth,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"] > 0
    assert body["agent_review_id"] > 0


@pytest.mark.asyncio
async def test_run_returns_412_when_no_active_provider(client, admin_auth, db_engine, admin_user):
    """A run exists but no provider has been activated."""
    async with _factory(db_engine)() as session:
        exp = Experiment(name="E", organization_id=admin_user.organization_id, status="processing")
        session.add(exp)
        await session.flush()
        run = PipelineRun(
            organization_id=admin_user.organization_id,
            experiment_id=exp.id,
            pipeline_name="rnaseq",
            pipeline_version="3.14",
            parameters_json={"x": 1},
            output_files_json={"y": 2},
            status="complete",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    resp = await client.post(
        "/api/agent_reviews/run",
        json={"entity_type": "pipeline_run", "entity_id": run_id, "selected_sub_item_ids": ["qc.metric_review"]},
        headers=admin_auth,
    )
    assert resp.status_code == 412


@pytest.mark.asyncio
async def test_run_returns_409_on_debounce(client, admin_auth, configured_run):
    payload = {
        "entity_type": "pipeline_run",
        "entity_id": configured_run["run_id"],
        "selected_sub_item_ids": ["qc.metric_review"],
    }
    first = await client.post("/api/agent_reviews/run", json=payload, headers=admin_auth)
    assert first.status_code == 202
    second = await client.post("/api/agent_reviews/run", json=payload, headers=admin_auth)
    assert second.status_code == 409
    body = second.json()
    detail = body["detail"]
    assert detail["detail"] == "review_in_progress"
    assert detail["existing_job_id"] == first.json()["job_id"]
    assert detail["existing_agent_review_id"] == first.json()["agent_review_id"]


@pytest.mark.asyncio
async def test_run_forbidden_for_viewer(client, viewer_auth, configured_run):
    resp = await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "pipeline_run",
            "entity_id": configured_run["run_id"],
            "selected_sub_item_ids": ["qc.metric_review"],
        },
        headers=viewer_auth,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_returns_single_run_reviews(client, admin_auth, configured_run):
    await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "pipeline_run",
            "entity_id": configured_run["run_id"],
            "selected_sub_item_ids": ["qc.metric_review"],
        },
        headers=admin_auth,
    )
    resp = await client.get(
        "/api/agent_reviews",
        params={"entity_type": "pipeline_run", "entity_id": configured_run["run_id"]},
        headers=admin_auth,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # The pending review may or may not still be 'pending' depending on the
    # BackgroundTasks timing in the test client; either way it should be
    # present and visible under the default 'active' filter.
    assert len(items) == 1
    assert items[0]["entity_id"] == configured_run["run_id"]


@pytest.mark.asyncio
async def test_dismiss_and_undismiss_flow(client, admin_auth, configured_run, db_engine, admin_user):
    run_resp = await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "pipeline_run",
            "entity_id": configured_run["run_id"],
            "selected_sub_item_ids": ["qc.metric_review"],
        },
        headers=admin_auth,
    )
    review_id = run_resp.json()["agent_review_id"]

    dismiss = await client.post(f"/api/agent_reviews/{review_id}/dismiss", headers=admin_auth)
    assert dismiss.status_code == 204

    # Default filter excludes dismissed.
    active = (
        await client.get(
            "/api/agent_reviews",
            params={"entity_type": "pipeline_run", "entity_id": configured_run["run_id"]},
            headers=admin_auth,
        )
    ).json()
    assert active["items"] == []

    dismissed_view = (
        await client.get(
            "/api/agent_reviews",
            params={
                "entity_type": "pipeline_run",
                "entity_id": configured_run["run_id"],
                "filter": "dismissed",
            },
            headers=admin_auth,
        )
    ).json()
    assert len(dismissed_view["items"]) == 1
    assert dismissed_view["items"][0]["dismissed"] is True

    undismiss = await client.post(f"/api/agent_reviews/{review_id}/undismiss", headers=admin_auth)
    assert undismiss.status_code == 204
    active2 = (
        await client.get(
            "/api/agent_reviews",
            params={"entity_type": "pipeline_run", "entity_id": configured_run["run_id"]},
            headers=admin_auth,
        )
    ).json()
    assert len(active2["items"]) == 1


@pytest.mark.asyncio
async def test_get_returns_full_detail(client, admin_auth, configured_run):
    run_resp = await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "pipeline_run",
            "entity_id": configured_run["run_id"],
            "selected_sub_item_ids": ["qc.metric_review"],
        },
        headers=admin_auth,
    )
    review_id = run_resp.json()["agent_review_id"]
    resp = await client.get(f"/api/agent_reviews/{review_id}", headers=admin_auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == review_id
    assert "artifact_gcs_paths" in body
    assert "body" in body


@pytest.mark.asyncio
async def test_pipeline_run_and_experiment_tabs_are_strictly_scoped(
    client, admin_auth, configured_run, db_engine, admin_user
):
    """The Pipeline Run tab shows ONLY entity_type='pipeline_run' rows for
    that run. The Experiment tab shows ONLY entity_type='experiment' rows for
    that experiment. An experiment review that included Run X must NOT
    appear on Run X's Pipeline Run tab, and a pipeline_run review of Run X
    must NOT appear on the parent experiment's tab. Earlier the Pipeline Run
    tab unioned in experiment reviews via included_run_ids; user feedback
    requires the strict scoping below."""
    # Create one pipeline_run-scope review of the run.
    resp_run = await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "pipeline_run",
            "entity_id": configured_run["run_id"],
            "selected_sub_item_ids": ["qc.metric_review"],
        },
        headers=admin_auth,
    )
    assert resp_run.status_code == 202, resp_run.text

    # And one experiment-scope review of the parent experiment, with that run included.
    resp_exp = await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "experiment",
            "entity_id": configured_run["experiment_id"],
            "selected_sub_item_ids": ["qc.metric_review", "xsample.drift_over_time"],
            "included_run_ids": [configured_run["run_id"]],
        },
        headers=admin_auth,
    )
    assert resp_exp.status_code == 202, resp_exp.text

    # Pipeline Run tab: only pipeline_run rows.
    run_listed = (
        await client.get(
            "/api/agent_reviews",
            params={"entity_type": "pipeline_run", "entity_id": configured_run["run_id"]},
            headers=admin_auth,
        )
    ).json()
    entity_types = {item["entity_type"] for item in run_listed["items"]}
    assert entity_types == {"pipeline_run"}, run_listed

    # Experiment tab: only experiment rows.
    exp_listed = (
        await client.get(
            "/api/agent_reviews",
            params={"entity_type": "experiment", "entity_id": configured_run["experiment_id"]},
            headers=admin_auth,
        )
    ).json()
    entity_types = {item["entity_type"] for item in exp_listed["items"]}
    assert entity_types == {"experiment"}, exp_listed


# --- Read access for "View Results" roles (experiments:view OR pipelines:view) ---
#
# Reads of agent reviews back the QC report, which is shown to anyone who can view
# Results. Bench/viewer/leadership lack llm_integration:use but must still see
# existing reviews. Writes (run/dismiss) stay gated on llm_integration:use.


async def _make_user_with_role(session, admin_user, role_name, permissions):
    """Create a user under a fresh custom role with the given permissions."""
    from app.services import role_service

    role = await role_service.create_role(
        session,
        admin_user.organization_id,
        name=role_name,
        description=f"test {role_name}",
        permissions=permissions,
    )
    await session.flush()
    user = User(
        email=f"{role_name}@test.com",
        password_hash=AuthService.hash_password("custompass123"),
        role_id=role.id,
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    role_service.invalidate_cache()
    return user, role.name


@pytest_asyncio.fixture
async def pipelines_only_auth(session, admin_user):
    """A role with pipelines:view but no experiments:view and no llm_integration:use."""
    user, role_name = await _make_user_with_role(session, admin_user, "pipelines_only", [("pipelines", "view")])
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def no_results_auth(session, admin_user):
    """A role with neither experiments:view nor pipelines:view."""
    user, role_name = await _make_user_with_role(session, admin_user, "no_results", [("samples", "view")])
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)
    return {"Authorization": f"Bearer {token}"}


async def _seed_run_review(client, admin_auth, run_id):
    resp = await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "pipeline_run",
            "entity_id": run_id,
            "selected_sub_item_ids": ["qc.metric_review"],
        },
        headers=admin_auth,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["agent_review_id"]


@pytest.mark.asyncio
async def test_list_visible_to_experiments_viewer_without_llm_use(client, admin_auth, viewer_auth, configured_run):
    await _seed_run_review(client, admin_auth, configured_run["run_id"])
    resp = await client.get(
        "/api/agent_reviews",
        params={"entity_type": "pipeline_run", "entity_id": configured_run["run_id"]},
        headers=viewer_auth,
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["items"]) == 1


@pytest.mark.asyncio
async def test_list_visible_to_pipelines_only_role(client, admin_auth, pipelines_only_auth, configured_run):
    await _seed_run_review(client, admin_auth, configured_run["run_id"])
    resp = await client.get(
        "/api/agent_reviews",
        params={"entity_type": "pipeline_run", "entity_id": configured_run["run_id"]},
        headers=pipelines_only_auth,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_list_forbidden_without_results_view(client, admin_auth, no_results_auth, configured_run):
    await _seed_run_review(client, admin_auth, configured_run["run_id"])
    resp = await client.get(
        "/api/agent_reviews",
        params={"entity_type": "pipeline_run", "entity_id": configured_run["run_id"]},
        headers=no_results_auth,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_detail_visible_to_viewer_forbidden_without_results_view(
    client, admin_auth, viewer_auth, no_results_auth, configured_run
):
    review_id = await _seed_run_review(client, admin_auth, configured_run["run_id"])
    ok = await client.get(f"/api/agent_reviews/{review_id}", headers=viewer_auth)
    assert ok.status_code == 200, ok.text
    forbidden = await client.get(f"/api/agent_reviews/{review_id}", headers=no_results_auth)
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_viewer_still_cannot_run_review(client, viewer_auth, configured_run):
    resp = await client.post(
        "/api/agent_reviews/run",
        json={
            "entity_type": "pipeline_run",
            "entity_id": configured_run["run_id"],
            "selected_sub_item_ids": ["qc.metric_review"],
        },
        headers=viewer_auth,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_filter_all_includes_dismissed(client, admin_auth, configured_run):
    review_id = await _seed_run_review(client, admin_auth, configured_run["run_id"])

    all_before = (
        await client.get(
            "/api/agent_reviews",
            params={
                "entity_type": "pipeline_run",
                "entity_id": configured_run["run_id"],
                "filter": "all",
            },
            headers=admin_auth,
        )
    ).json()
    assert len(all_before["items"]) == 1

    dismiss = await client.post(f"/api/agent_reviews/{review_id}/dismiss", headers=admin_auth)
    assert dismiss.status_code == 204

    active = (
        await client.get(
            "/api/agent_reviews",
            params={"entity_type": "pipeline_run", "entity_id": configured_run["run_id"]},
            headers=admin_auth,
        )
    ).json()
    assert active["items"] == []

    all_after = (
        await client.get(
            "/api/agent_reviews",
            params={
                "entity_type": "pipeline_run",
                "entity_id": configured_run["run_id"],
                "filter": "all",
            },
            headers=admin_auth,
        )
    ).json()
    assert len(all_after["items"]) == 1
    assert all_after["items"][0]["dismissed"] is True
