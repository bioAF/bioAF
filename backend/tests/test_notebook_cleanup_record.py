"""One write for a session's cleanup record, and it never loses an owed save.

An owed save (``save_outputs``) is a fact separate from the latest cleanup outcome. Every writer
(Stop, idle stops, launch completion, the periodic pass) records cleanup through one conditional
database update that keeps an owed save unless the new outcome explicitly confirms the save
(``save_outputs: False``). Because the rule is in the update itself, concurrent writers cannot
drop it.
"""

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.notebook_session import NotebookSession
from tests.test_notebook_lifecycle_state import _Adapter, _launch, _row, _seed


@pytest.fixture
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


def _cleanup(row):
    return ((row.provider_metadata or {}).get("lifecycle") or {}).get("cleanup") or {}


@pytest.mark.asyncio
async def test_a_late_launch_response_keeps_a_save_owed_by_a_stop_that_landed_first(
    session, admin_user, factory, monkeypatch
):
    """Reviewer's race: observer marked it running, Stop failed to save, then the launch response arrived."""

    class RaceAdapter(_Adapter):
        async def launch_session(self, spec):
            result = await super().launch_session(spec)
            async with self.factory() as db:
                row = (
                    await db.execute(select(NotebookSession).where(NotebookSession.id == spec["session_id"]))
                ).scalar_one()
                lifecycle = {
                    **row.provider_metadata["lifecycle"],
                    "cleanup": {"state": "pending", "save_outputs": True},
                }
                await db.execute(
                    update(NotebookSession)
                    .where(NotebookSession.id == row.id)
                    .values(status="stopped", provider_metadata={**row.provider_metadata, "lifecycle": lifecycle})
                )
                await db.commit()
            return result

    ns = await _launch(session, admin_user, RaceAdapter(factory), monkeypatch)

    row = await _row(factory, ns.id)
    assert row.status == "stopped"
    cleanup = _cleanup(row)
    assert cleanup["state"] == "pending" and cleanup["save_outputs"] is True


@pytest.mark.asyncio
async def test_the_one_write_keeps_an_owed_save_unless_the_outcome_confirms_it(session, admin_user, factory):
    from app.services.notebook_lifecycle import record_cleanup

    ns = await _seed(session, admin_user, status="stopped")
    await record_cleanup(session, ns, {"state": "pending", "save_outputs": True})
    await session.commit()

    for outcome in ({"state": "blocked", "detail": "x"}, {"state": "pending"}, {"state": "complete"}):
        await record_cleanup(session, ns, outcome)
        await session.commit()
        assert _cleanup(await _row(factory, ns.id))["save_outputs"] is True, outcome

    await record_cleanup(session, ns, {"state": "complete", "save_outputs": False})
    await session.commit()
    assert not _cleanup(await _row(factory, ns.id)).get("save_outputs")
    assert ns.provider_metadata["lifecycle"]["cleanup"]["state"] == "complete"  # in-memory row reloaded


@pytest.mark.asyncio
async def test_other_metadata_survives_a_cleanup_write(session, admin_user, factory):
    from app.services.notebook_lifecycle import record_cleanup

    ns = await _seed(session, admin_user, status="stopped")
    await record_cleanup(session, ns, {"state": "pending"})
    await session.commit()

    row = await _row(factory, ns.id)
    assert row.provider_metadata["namespace"] == "bioaf-notebooks"
    assert row.provider_metadata["lifecycle"]["reserved_at"]


def test_there_is_one_cleanup_writer_and_no_unused_status_wrapper():
    from app.adapters import session_status
    from app.services import notebook_lifecycle, notebook_service

    assert not hasattr(session_status, "transition_compute_session")
    assert not hasattr(notebook_service, "_set_cleanup")
    assert not hasattr(notebook_lifecycle, "_record_cleanup")


def test_reservation_launch_and_termination_build_the_workload_one_way(mode_adapter_factory):
    from app.adapters.notebooks import kubernetes as k8s
    from app.settings_contract import ComputeMode

    adapter = mode_adapter_factory(k8s.KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    reserved = adapter.reserve_workload(42, 7, "jupyter", organization_id=3)
    launched = k8s.launch_workload(
        {"session_id": 42, "user_id": 7, "session_type": "jupyter", "organization_id": 3}, "bioaf-notebooks"
    )
    assert reserved == launched == k8s.notebook_workload(42, 7, "jupyter", organization_id=3)
