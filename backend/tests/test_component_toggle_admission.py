"""A component change waits for no running infrastructure action (review finding #8).

The toggle endpoint is refused, with the running action named, while another deploy, teardown
or build holds the shared operation lock, so a stale page cannot change components underneath it.
"""

import pytest
from sqlalchemy import text

from app.services.component_service import ComponentService


async def _component(session, key, enabled=False, status="disabled"):
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES (:key, :enabled, :status, '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = EXCLUDED.enabled, status = EXCLUDED.status"
        ).bindparams(key=key, enabled=enabled, status=status)
    )
    await session.commit()


async def _state(session, key):
    row = (
        await session.execute(
            text("SELECT enabled, status FROM component_states WHERE component_key = :k").bindparams(k=key)
        )
    ).one()
    return tuple(row)


@pytest.mark.asyncio
async def test_a_toggle_is_refused_while_another_infrastructure_action_runs(session, admin_user):
    """A stale page cannot change components under a running deploy, teardown or build."""
    from app.services import infrastructure_operations as ops

    await _component(session, "nextflow", enabled=True, status="enabled")
    await ops.accept(action="compute_teardown", principal=ops.Principal(admin_user.id))

    with pytest.raises(ops.InfrastructureBusyError) as exc:
        await ComponentService.toggle_stack_component(session, "nextflow", user_id=admin_user.id)

    assert "Compute teardown" in str(exc.value)
    assert "Your Component change was not started." in str(exc.value)
    assert await _state(session, "nextflow") == (True, "enabled")


@pytest.mark.asyncio
async def test_the_toggle_route_answers_409_while_another_action_runs(client, session, admin_user, admin_token):
    from app.services import infrastructure_operations as ops

    await _component(session, "nextflow", enabled=True, status="enabled")
    await ops.accept(action="compute_teardown", principal=ops.Principal(admin_user.id))

    response = await client.post(
        "/api/v1/infrastructure/stack/components/nextflow/toggle",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 409
    assert await _state(session, "nextflow") == (True, "enabled")


@pytest.mark.asyncio
async def test_no_other_action_can_start_while_a_component_change_runs(client, session, admin_user, admin_token):
    """The change holds the operation lock from before it reads state until it commits."""
    from unittest.mock import patch

    from app.services import infrastructure_operations as ops

    await _component(session, "nextflow", enabled=True, status="enabled")
    refused = []
    toggle = ComponentService.toggle_stack_component

    async def toggle_while_another_admin_starts_teardown(*args, **kwargs):
        try:
            await ops.accept(action="compute_teardown", principal=ops.Principal(admin_user.id))
        except ops.InfrastructureBusyError:
            refused.append(True)
        return await toggle(*args, **kwargs)

    with patch.object(
        ComponentService, "toggle_stack_component", side_effect=toggle_while_another_admin_starts_teardown
    ):
        response = await client.post(
            "/api/v1/infrastructure/stack/components/nextflow/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    assert refused == [True]
    assert await ops.active_operation(session) is None
