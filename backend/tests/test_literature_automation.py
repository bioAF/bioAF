"""Automated AI Lit Review cadence + per-user notification (spec-automation).

Covers the due-set computation, the per-tick cap and roll-over, tick gating,
the run trigger field, the no-provider and per-experiment-failure isolation
paths, the auto-review notification event + router fan-out, and the settings
endpoint.

The full Lit Review Run pipeline (LLM + sources) is monkey-patched to
deterministic fakes, mirroring test_literature_lit_review_run.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models.experiment import Experiment  # noqa: F401  (registers FK target)
from app.models.literature import (
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    LiteratureReviewRun,
)
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.services.literature import lit_review_auto_service, lit_review_run_service
from app.services.literature.sources import PaperRecord
from app.services.platform_config_service import PlatformConfigService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_experiment(session, admin_user, name="Exp"):
    result = await session.execute(
        text(
            """
            INSERT INTO experiments (name, status, organization_id, owner_user_id, project_id)
            VALUES (:n, 'registered', :org, :uid, NULL)
            RETURNING id
            """
        ).bindparams(n=name, org=admin_user.organization_id, uid=admin_user.id)
    )
    eid = result.scalar_one()
    await session.commit()
    return eid


async def _add_sample(session, eid, *, created_at):
    session.add(Sample(experiment_id=eid, status="registered", created_at=created_at))
    await session.commit()


async def _add_run(session, admin_user, eid, *, created_at, completed_at=None):
    session.add(
        PipelineRun(
            organization_id=admin_user.organization_id,
            experiment_id=eid,
            pipeline_name="nf-core/rnaseq",
            status="completed",
            created_at=created_at,
            completed_at=completed_at,
        )
    )
    await session.commit()


async def _add_scheduled_run(session, admin_user, eid, *, created_at):
    session.add(
        LiteratureReviewRun(
            organization_id=admin_user.organization_id,
            experiment_id=eid,
            triggered_by_user_id=admin_user.id,
            trigger=TRIGGER_SCHEDULED,
            status="complete",
            llm_provider="anthropic",
            llm_model="claude-test",
            created_at=created_at,
        )
    )
    await session.commit()


async def _set_auto_config(session, org_id, *, enabled=True, cadence="weekly", cap=5):
    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
    org.lit_review_auto_enabled = enabled
    org.lit_review_auto_cadence = cadence
    org.lit_review_max_runs_per_tick = cap
    await session.commit()


async def _seed_llm_provider(session, admin_user):
    from app.services import llm_provider_config_service

    await llm_provider_config_service.upsert(
        session,
        org_id=admin_user.organization_id,
        provider="anthropic",
        api_key="sk-test-fake-LAST5",
        model="claude-test",
        actor_user_id=admin_user.id,
    )
    await llm_provider_config_service.set_active(
        session,
        org_id=admin_user.organization_id,
        provider="anthropic",
        actor_user_id=admin_user.id,
    )
    await session.commit()


def _patch_sources(monkeypatch, records: list[PaperRecord]):
    async def fake_search(query, max_results, api_key):
        return list(records)

    async def empty_search(query, max_results, api_key):
        return []

    from app.services.literature.sources import biorxiv, europepmc, pubmed, semanticscholar

    monkeypatch.setattr(pubmed, "search", fake_search)
    monkeypatch.setattr(biorxiv, "search", empty_search)
    monkeypatch.setattr(europepmc, "search", empty_search)
    monkeypatch.setattr(semanticscholar, "search", empty_search)


class _FakeLlmClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        return self._responses.pop(0) if self._responses else ""


# ---------------------------------------------------------------------------
# Due-set computation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_experiments_detects_new_activity_and_orders(session, admin_user):
    org_id = admin_user.organization_id
    now = datetime.now(UTC)
    t_old = now - timedelta(days=10)
    t_mid = now - timedelta(days=5)
    t_new = now - timedelta(days=1)

    a = await _make_experiment(session, admin_user, "A-new-sample")
    await _add_sample(session, a, created_at=t_new)

    # B was reviewed after its only activity -> not due.
    b = await _make_experiment(session, admin_user, "B-reviewed-recent")
    await _add_sample(session, b, created_at=t_old)
    await _add_scheduled_run(session, admin_user, b, created_at=t_mid)

    # C was reviewed, then got a new sample -> due.
    c = await _make_experiment(session, admin_user, "C-new-after-review")
    await _add_scheduled_run(session, admin_user, c, created_at=t_mid)
    await _add_sample(session, c, created_at=t_new)

    # D has no activity -> not due.
    d = await _make_experiment(session, admin_user, "D-empty")

    # E has an old completed run, never reviewed -> due.
    e = await _make_experiment(session, admin_user, "E-run-old")
    await _add_run(session, admin_user, e, created_at=t_old, completed_at=t_old)

    due = await lit_review_auto_service.due_experiments(session, org_id)

    assert a in due and c in due and e in due
    assert b not in due and d not in due
    # Oldest activity first: E (10 days) precedes A and C (1 day).
    assert due.index(e) < due.index(a)
    assert due.index(e) < due.index(c)


@pytest.mark.asyncio
async def test_due_experiments_empty_when_no_experiments(session, admin_user):
    assert await lit_review_auto_service.due_experiments(session, admin_user.organization_id) == []


# ---------------------------------------------------------------------------
# Sweep: cap + roll-over + trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_due_sweep_caps_and_rolls_over(session, admin_user, monkeypatch):
    org_id = admin_user.organization_id
    await _seed_llm_provider(session, admin_user)
    await _set_auto_config(session, org_id, enabled=True, cap=2)

    executed: list[int] = []

    async def fake_exec(run_id):
        executed.append(run_id)

    monkeypatch.setattr(lit_review_run_service, "_execute_run", fake_exec)

    now = datetime.now(UTC)
    e1 = await _make_experiment(session, admin_user, "e1")
    await _add_sample(session, e1, created_at=now - timedelta(days=3))
    e2 = await _make_experiment(session, admin_user, "e2")
    await _add_sample(session, e2, created_at=now - timedelta(days=2))
    e3 = await _make_experiment(session, admin_user, "e3")
    await _add_sample(session, e3, created_at=now - timedelta(days=1))

    result = await lit_review_auto_service.run_due_sweep(session, org_id)
    assert len(result["due"]) == 3
    assert result["ran"] == [e1, e2]  # oldest-activity-first, capped at 2

    runs = (
        (await session.execute(select(LiteratureReviewRun).where(LiteratureReviewRun.trigger == TRIGGER_SCHEDULED)))
        .scalars()
        .all()
    )
    assert len(runs) == 2

    # Second sweep: e1/e2 now have a recent scheduled run, only e3 remains due.
    result2 = await lit_review_auto_service.run_due_sweep(session, org_id)
    assert result2["ran"] == [e3]


@pytest.mark.asyncio
async def test_create_run_trigger_defaults_manual_and_accepts_scheduled(session, admin_user):
    await _seed_llm_provider(session, admin_user)
    eid = await _make_experiment(session, admin_user)

    manual = await lit_review_run_service.create_run(
        session, org_id=admin_user.organization_id, experiment_id=eid, triggered_by_user_id=admin_user.id
    )
    assert manual.trigger == TRIGGER_MANUAL

    scheduled = await lit_review_run_service.create_run(
        session,
        org_id=admin_user.organization_id,
        experiment_id=eid,
        triggered_by_user_id=admin_user.id,
        trigger=TRIGGER_SCHEDULED,
    )
    assert scheduled.trigger == TRIGGER_SCHEDULED


@pytest.mark.asyncio
async def test_run_due_sweep_skips_without_active_llm_provider(session, admin_user):
    org_id = admin_user.organization_id
    await _set_auto_config(session, org_id, enabled=True)
    e1 = await _make_experiment(session, admin_user)
    await _add_sample(session, e1, created_at=datetime.now(UTC) - timedelta(days=1))

    result = await lit_review_auto_service.run_due_sweep(session, org_id)
    assert result["skipped_reason"] == "no_active_llm_provider"
    assert result["ran"] == []


@pytest.mark.asyncio
async def test_run_due_sweep_isolates_a_failing_experiment(session, admin_user, monkeypatch):
    org_id = admin_user.organization_id
    await _seed_llm_provider(session, admin_user)
    await _set_auto_config(session, org_id, enabled=True, cap=5)

    state = {"failed": False}

    async def fake_exec(run_id):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("boom")

    monkeypatch.setattr(lit_review_run_service, "_execute_run", fake_exec)

    now = datetime.now(UTC)
    e1 = await _make_experiment(session, admin_user, "e1")
    await _add_sample(session, e1, created_at=now - timedelta(days=2))
    e2 = await _make_experiment(session, admin_user, "e2")
    await _add_sample(session, e2, created_at=now - timedelta(days=1))

    result = await lit_review_auto_service.run_due_sweep(session, org_id)
    # e1's execution raised; e2 still ran.
    assert e1 not in result["ran"]
    assert e2 in result["ran"]


# ---------------------------------------------------------------------------
# Tick gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_gating_and_advance(session, admin_user):
    org_id = admin_user.organization_id

    # Disabled -> never due.
    assert await lit_review_auto_service.is_tick_due(session, org_id) is False

    await _set_auto_config(session, org_id, enabled=True, cadence="daily")

    # Enabled but no next_run yet -> not due.
    assert await lit_review_auto_service.is_tick_due(session, org_id) is False

    await lit_review_auto_service.ensure_next_run_seeded(session, org_id)
    await session.commit()
    # Seeded one cadence out -> not due now.
    assert await lit_review_auto_service.is_tick_due(session, org_id) is False
    # ... but due once that time passes.
    future = datetime.now(UTC) + timedelta(hours=25)
    assert await lit_review_auto_service.is_tick_due(session, org_id, now=future) is True

    await lit_review_auto_service.advance_next_run(session, org_id, now=future)
    await session.commit()
    next_run = await lit_review_auto_service._get_next_run(session)
    assert next_run is not None and next_run > future


# ---------------------------------------------------------------------------
# Notification: event emission + router fan-out
# ---------------------------------------------------------------------------


async def _run_scheduled(session, admin_user, monkeypatch, *, score: float):
    """Run one scheduled Lit Review Run to completion with a single candidate
    scored `score`, returning the list of (event_type, payload) emits."""
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await _seed_llm_provider(session, admin_user)
    eid = await _make_experiment(session, admin_user)

    candidate = PaperRecord(
        source="pubmed",
        title="A candidate paper",
        authors=[{"family": "Doe", "given": "Jane"}],
        doi="10.9999/auto",
        journal="Nature",
        publication_date=None,
        abstract="abstract",
    )
    _patch_sources(monkeypatch, [candidate])
    fake_client = _FakeLlmClient(["q1\nq2", json.dumps([{"index": 0, "score": score, "reasoning": "ok"}])])
    monkeypatch.setattr(lit_review_run_service, "get_client", lambda provider: fake_client)

    emitted: list[tuple[str, dict]] = []

    async def fake_emit(event_type, payload):
        emitted.append((event_type, payload))

    monkeypatch.setattr(lit_review_run_service.event_bus, "emit", fake_emit)

    run = await lit_review_run_service.create_run(
        session,
        org_id=admin_user.organization_id,
        experiment_id=eid,
        triggered_by_user_id=admin_user.id,
        trigger=TRIGGER_SCHEDULED,
        score_threshold=0.33,
    )
    await session.commit()
    await lit_review_run_service._execute_run(run.id)
    return emitted


@pytest.mark.asyncio
async def test_scheduled_run_emits_auto_review_event_when_papers_added(session, admin_user, monkeypatch):
    from app.services.event_types import LITERATURE_AUTO_REVIEW_RECOMMENDATIONS

    emitted = await _run_scheduled(session, admin_user, monkeypatch, score=0.88)
    auto = [p for (et, p) in emitted if et == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS]
    assert len(auto) == 1
    assert auto[0]["org_id"] == admin_user.organization_id
    assert auto[0]["metadata"]["recommendation_count"] == 1
    assert auto[0]["event_type"] == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS


@pytest.mark.asyncio
async def test_scheduled_run_silent_when_no_papers_added(session, admin_user, monkeypatch):
    from app.services.event_types import LITERATURE_AUTO_REVIEW_RECOMMENDATIONS

    # Score below the default 0.33 run threshold -> zero recommendations.
    emitted = await _run_scheduled(session, admin_user, monkeypatch, score=0.05)
    auto = [p for (et, p) in emitted if et == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS]
    assert auto == []


@pytest.mark.asyncio
async def test_notification_router_delivers_in_app_to_all_active_users(session, admin_user, viewer_user):
    import app.database as _database
    from app.models.notification import Notification, NotificationRule
    from app.services.event_types import LITERATURE_AUTO_REVIEW_RECOMMENDATIONS
    from app.services.notification_router import NotificationRouter

    org_id = admin_user.organization_id
    # Seed the same in-app rule (NULL role_filter) the migration installs.
    session.add(
        NotificationRule(
            organization_id=org_id,
            event_type=LITERATURE_AUTO_REVIEW_RECOMMENDATIONS,
            channel="in_app",
            role_filter=None,
            mandatory=False,
            enabled=True,
        )
    )
    await session.commit()

    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": LITERATURE_AUTO_REVIEW_RECOMMENDATIONS,
            "org_id": org_id,
            "title": "AI Lit Review added 2 papers to Exp",
            "message": "msg",
            "metadata": {"experiment_id": 1, "run_id": 1},
        }
    )

    rows = (
        (
            await session.execute(
                select(Notification).where(Notification.event_type == LITERATURE_AUTO_REVIEW_RECOMMENDATIONS)
            )
        )
        .scalars()
        .all()
    )
    user_ids = {r.user_id for r in rows}
    assert admin_user.id in user_ids
    assert viewer_user.id in user_ids


# ---------------------------------------------------------------------------
# Settings endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lit_review_auto_settings_default_update_and_validation(client, admin_token, viewer_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    r = await client.get("/api/literature/settings/lit-review", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["auto_enabled"] is False
    assert body["auto_cadence"] == "weekly"
    assert body["max_runs_per_tick"] == 5

    r2 = await client.put(
        "/api/literature/settings/lit-review",
        json={"auto_enabled": True, "auto_cadence": "daily", "max_runs_per_tick": 3},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["auto_enabled"] is True
    assert r2.json()["auto_cadence"] == "daily"
    assert r2.json()["max_runs_per_tick"] == 3

    # GET reflects the update.
    r3 = await client.get("/api/literature/settings/lit-review", headers=headers)
    assert r3.json()["auto_cadence"] == "daily"

    # Validation.
    bad_cadence = await client.put(
        "/api/literature/settings/lit-review", json={"auto_cadence": "hourly"}, headers=headers
    )
    assert bad_cadence.status_code == 400
    bad_cap = await client.put("/api/literature/settings/lit-review", json={"max_runs_per_tick": 0}, headers=headers)
    assert bad_cap.status_code == 400

    # Viewer cannot configure.
    v = await client.put(
        "/api/literature/settings/lit-review",
        json={"auto_enabled": True},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert v.status_code == 403

    # Disabling round-trips.
    r4 = await client.put("/api/literature/settings/lit-review", json={"auto_enabled": False}, headers=headers)
    assert r4.json()["auto_enabled"] is False


@pytest.mark.asyncio
async def test_auto_schedule_seeded_on_enable_and_cleared_on_disable(session, admin_user):
    """The scheduling primitives the settings endpoint uses: enabling seeds a
    next_run one cadence out; disabling clears it. Service-level (no client) so
    it does not mix the session fixture with HTTP requests."""
    org_id = admin_user.organization_id
    await _set_auto_config(session, org_id, enabled=True, cadence="daily")

    await lit_review_auto_service.schedule_from_now(session, org_id)
    await session.commit()
    assert await PlatformConfigService.get(session, lit_review_auto_service.NEXT_RUN_KEY) is not None

    await lit_review_auto_service.clear_schedule(session)
    await session.commit()
    assert await PlatformConfigService.get(session, lit_review_auto_service.NEXT_RUN_KEY) is None
