"""Tests for POST /api/components/select-batch.

The endpoint is what the wizard calls after the user checks a set of
components on the new Select Components step. It transactionally writes
one queued row per selected key.
"""

import pytest
from sqlalchemy import text


async def _set_compute_stack(session, stack: str) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('compute_stack', :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(v=stack)
    )


async def _rows(session) -> dict[str, tuple[bool, str]]:
    result = (await session.execute(text("SELECT component_key, enabled, status FROM component_states"))).fetchall()
    return {r[0]: (r[1], r[2]) for r in result}


@pytest.mark.asyncio
async def test_writes_one_queued_row_per_selected_key(client, admin_token, session):
    """T8: each selected key becomes enabled=true, status='queued_for_infra'."""
    await _set_compute_stack(session, "kubernetes")
    await session.commit()

    response = await client.post(
        "/api/components/select-batch",
        json={"keys": ["nextflow", "jupyterhub"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert sorted(payload["queued"]) == ["jupyterhub", "nextflow"]

    rows = await _rows(session)
    assert rows["nextflow"] == (True, "queued_for_infra")
    assert rows["jupyterhub"] == (True, "queued_for_infra")


@pytest.mark.asyncio
async def test_empty_keys_is_a_noop(client, admin_token, session):
    """An empty selection is accepted (the user explicitly chose nothing)."""
    await _set_compute_stack(session, "kubernetes")
    await session.commit()

    response = await client.post(
        "/api/components/select-batch",
        json={"keys": []},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["queued"] == []


@pytest.mark.asyncio
async def test_unknown_key_returns_400_and_writes_nothing(client, admin_token, session):
    """T9: validation is all-or-nothing. One bad key fails the whole batch."""
    await _set_compute_stack(session, "kubernetes")
    await session.commit()

    response = await client.post(
        "/api/components/select-batch",
        json={"keys": ["nextflow", "not_a_real_key"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert "not_a_real_key" in response.text

    rows = await _rows(session)
    # nextflow must not have been written: validation must precede any write.
    assert "nextflow" not in rows or rows["nextflow"][1] != "queued_for_infra"


@pytest.mark.asyncio
async def test_stack_mismatched_key_returns_400(client, admin_token, session):
    """T10: trying to queue a SLURM-only component on a Kubernetes stack is
    rejected. The wizard only shows stack-matched components, but a stale
    or hand-crafted request must be defended against.
    """
    await _set_compute_stack(session, "kubernetes")
    await session.commit()

    response = await client.post(
        "/api/components/select-batch",
        json={"keys": ["slurm"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert "slurm" in response.text

    rows = await _rows(session)
    assert rows.get("slurm", (False, "disabled"))[1] != "queued_for_infra"


@pytest.mark.asyncio
async def test_duplicate_submit_is_idempotent(client, admin_token, session):
    """The user might click Continue twice. The second submit must not raise
    on the unique constraint and must leave the row in the queued state.
    """
    await _set_compute_stack(session, "kubernetes")
    await session.commit()

    first = await client.post(
        "/api/components/select-batch",
        json={"keys": ["nextflow"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        "/api/components/select-batch",
        json={"keys": ["nextflow"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert second.status_code == 200, second.text

    rows = await _rows(session)
    assert rows["nextflow"] == (True, "queued_for_infra")


@pytest.mark.asyncio
async def test_requires_admin(client, viewer_token, session):
    """Component selection mutates infra state; viewer role must be 403."""
    await _set_compute_stack(session, "kubernetes")
    await session.commit()

    response = await client.post(
        "/api/components/select-batch",
        json={"keys": ["nextflow"]},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )

    assert response.status_code == 403
