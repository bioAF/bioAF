"""The application does no normal work until its database upgrade is done (plan 9, section 3).

When app.schema_upgrade.compatibility says the database is not at this code's completed head (an
upgrade is running, failed, or never ran), the backend starts in maintenance: no startup writes, no
background loops, every API request except the health probes answers 503 with the reason, readiness
fails while liveness stays up, and once a controlled retry completes the process asks to be restarted
so it comes up normally. A probe that only says "unhealthy" while the app keeps writing is not enough.
"""

import asyncio

import pytest
from fastapi import FastAPI

import app.main as main_module
from app import schema_upgrade

FAILED = {
    "compatible": False,
    "reason": "upgrade_failed",
    "revision": "131",
    "target": "132",
    "upgrade": {"status": "failed", "step": "backup", "error": "BackupFailed"},
}


@pytest.fixture
def real_secrets(monkeypatch):
    """The lifespan refuses the default JWT secret, as production must."""
    from app.config import settings

    monkeypatch.setattr(settings, "jwt_secret_key", "startup-gate-test-secret-0123456789abcdef")


@pytest.fixture
def maintenance(monkeypatch):
    """The real application object, in the state its lifespan leaves it when the gate is closed."""
    monkeypatch.setattr(main_module.app.state, "maintenance", FAILED, raising=False)
    yield
    main_module.app.state.maintenance = None


@pytest.mark.asyncio
async def test_maintenance_answers_every_api_request_with_the_reason(client, admin_token, maintenance):
    for path in ("/api/projects", "/api/v1/integrations/samples", "/api/auth/me"):
        resp = await client.get(path, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 503, path
        body = resp.json()
        assert body["status"] == "maintenance" and body["reason"] == "upgrade_failed"
        assert body["upgrade"] == FAILED["upgrade"]
        assert resp.headers["Retry-After"] == "30"
    write = await client.post("/api/projects", json={"name": "x"}, headers={"Authorization": f"Bearer {admin_token}"})
    assert write.status_code == 503


@pytest.mark.asyncio
async def test_maintenance_fails_readiness_and_keeps_liveness(client, maintenance):
    assert (await client.get("/api/health/live")).status_code == 200
    ready = await client.get("/api/health/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "maintenance" and ready.json()["reason"] == "upgrade_failed"


def test_the_maintenance_text_follows_the_reason():
    from app.middleware.maintenance import maintenance_body

    failed = maintenance_body(FAILED)
    assert failed["detail"] == (
        "bioAF\u2019s last upgrade failed. An administrator needs to review the failure and retry."
    )
    assert (failed["reason"], failed["upgrade"]) == ("upgrade_failed", FAILED["upgrade"])
    running = maintenance_body({**FAILED, "reason": "upgrade_in_progress"})
    assert running["detail"] == "bioAF is finishing an upgrade and is not accepting requests yet."


@pytest.mark.asyncio
async def test_readiness_fails_when_the_database_does_not_answer(client):
    from app.database import get_session

    class _Down:
        async def execute(self, *_a, **_k):
            raise ConnectionError("database unavailable")

    async def _down_session():
        yield _Down()

    main_module.app.dependency_overrides[get_session] = _down_session
    resp = await client.get("/api/health/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "database": "unavailable"}


@pytest.mark.asyncio
async def test_a_ready_backend_still_answers_ok(client):
    resp = await client.get("/api/health/ready")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_a_closed_gate_skips_startup_writes_and_background_work(db_engine, monkeypatch, real_secrets):
    async def _closed(_engine):
        return FAILED

    def _must_not_run(*_a, **_k):
        raise AssertionError("normal startup work ran while the gate was closed")

    monkeypatch.setattr(schema_upgrade, "compatibility", _closed)
    monkeypatch.setattr(main_module, "engine", db_engine)
    monkeypatch.setattr("app.services.bootstrap_metadata.persist_cloud_provider", _must_not_run)
    monkeypatch.setattr("app.adapters.registry.initialize_adapters", _must_not_run)
    monkeypatch.setattr("app.services.upgrade_service.UpgradeService.resolve_pending_upgrades", _must_not_run)
    monkeypatch.setattr(main_module, "_pipeline_monitor_loop", _must_not_run)
    monkeypatch.setattr(main_module, "_postgres_backup_loop", _must_not_run)

    application = FastAPI()
    before = set(asyncio.all_tasks())
    async with main_module.lifespan(application):
        assert application.state.maintenance == FAILED
        started = set(asyncio.all_tasks()) - before
        assert [t.get_name() for t in started] == ["bioaf-maintenance-watch"]
    assert all(t.done() for t in started)


@pytest.mark.asyncio
async def test_maintenance_asks_for_a_restart_once_a_retry_completes(db_engine, monkeypatch, real_secrets):
    verdicts = iter([FAILED, FAILED, {**FAILED, "compatible": True, "reason": None}])

    async def _changing(_engine):
        return next(verdicts)

    restarts = []
    monkeypatch.setattr(schema_upgrade, "compatibility", _changing)
    monkeypatch.setattr(main_module, "engine", db_engine)
    monkeypatch.setattr(main_module, "MAINTENANCE_RECHECK_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "_request_restart", lambda: restarts.append(True))

    application = FastAPI()
    async with main_module.lifespan(application):
        for _ in range(100):
            if restarts:
                break
            await asyncio.sleep(0.01)
    assert restarts == [True]
