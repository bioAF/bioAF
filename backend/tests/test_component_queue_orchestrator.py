"""Tests for the component queue drain orchestrator.

`process_queued_components` is the function that turns user-selected,
queued component_states rows into actual enable actions once their
prereqs flip ready. It is the bridge between the wizard's pre-deploy
selection and the post-deploy reality.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text


async def _set_config(session, key: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(k=key, v=value)
    )


async def _insert_queued(session, component_key: str) -> None:
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES (:k, true, 'queued_for_infra', '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = true, status = 'queued_for_infra'"
        ).bindparams(k=component_key)
    )


async def _status_of(session, component_key: str) -> str | None:
    row = (
        await session.execute(
            text("SELECT status FROM component_states WHERE component_key = :k").bindparams(k=component_key)
        )
    ).first()
    return row[0] if row else None


@pytest.mark.asyncio
async def test_empty_queue_is_a_noop(session):
    """T4: process_queued_components is safe to call when nothing is queued."""
    from app.services.component_queue import process_queued_components

    result = await process_queued_components(session)
    await session.commit()

    assert result.enabled == []
    assert result.image_builds_started == []
    assert result.still_waiting == []


@pytest.mark.asyncio
async def test_no_prereq_component_flips_to_enabled_when_compute_ready(session):
    """T5: nextflow's only prereq is the cluster. With compute_deployed=true,
    a queued nextflow flips straight to enabled.
    """
    from app.services.component_queue import process_queued_components

    await _set_config(session, "compute_deployed", "true")
    await _insert_queued(session, "nextflow")
    await session.commit()

    result = await process_queued_components(session)
    await session.commit()

    assert "nextflow" in result.enabled
    assert await _status_of(session, "nextflow") == "enabled"


@pytest.mark.asyncio
async def test_no_prereq_component_stays_queued_when_compute_not_ready(session):
    """T5b: without compute_deployed, the row stays queued_for_infra."""
    from app.services.component_queue import process_queued_components

    await _set_config(session, "compute_deployed", "false")
    await _insert_queued(session, "nextflow")
    await session.commit()

    result = await process_queued_components(session)
    await session.commit()

    assert result.enabled == []
    assert "nextflow" in result.still_waiting
    assert await _status_of(session, "nextflow") == "queued_for_infra"


@pytest.mark.asyncio
async def test_queued_notebook_component_triggers_image_build_when_storage_ready(session):
    """T6: jupyterhub is queued, storage is deployed but compute is not yet.
    The orchestrator kicks off a notebook image build and flips the row to
    provisioning. This is the parallelism win: image building runs while
    GKE is still being created.
    """
    from app.services.component_queue import process_queued_components

    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "bioaf_scrna_image", "null")
    await _set_config(session, "notebook_image_build_id", "null")
    await _insert_queued(session, "jupyterhub")
    await session.commit()

    with patch(
        "app.services.component_queue.build_notebook_image", new=AsyncMock(return_value="build-123")
    ) as build_mock:
        result = await process_queued_components(session)
        await session.commit()

    assert build_mock.await_count == 1
    assert "jupyterhub" in result.image_builds_started
    assert await _status_of(session, "jupyterhub") == "provisioning"


@pytest.mark.asyncio
async def test_image_build_only_kicked_off_once_for_multiple_notebook_components(session):
    """T6b: rstudio and jupyterhub share the same notebook image. The
    orchestrator must not submit two Cloud Build jobs when both are queued.
    """
    from app.services.component_queue import process_queued_components

    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "bioaf_scrna_image", "null")
    await _set_config(session, "notebook_image_build_id", "null")
    await _insert_queued(session, "rstudio")
    await _insert_queued(session, "jupyterhub")
    await session.commit()

    with patch(
        "app.services.component_queue.build_notebook_image", new=AsyncMock(return_value="build-123")
    ) as build_mock:
        await process_queued_components(session)
        await session.commit()

    assert build_mock.await_count == 1


@pytest.mark.asyncio
async def test_notebook_component_flips_enabled_when_image_and_compute_both_ready(session):
    """T7: jupyterhub is queued, the notebook image build SUCCEEDED earlier
    (bioaf_scrna_image is populated), compute is up. The orchestrator
    should flip jupyterhub to enabled without trying to rebuild the image.
    """
    from app.services.component_queue import process_queued_components

    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "compute_deployed", "true")
    await _set_config(session, "bioaf_scrna_image", "us-central1-docker.pkg.dev/x/y/scrna:abc")
    await _insert_queued(session, "jupyterhub")
    await session.commit()

    with patch("app.services.component_queue.build_notebook_image", new=AsyncMock()) as build_mock:
        result = await process_queued_components(session)
        await session.commit()

    assert build_mock.await_count == 0
    assert "jupyterhub" in result.enabled
    assert await _status_of(session, "jupyterhub") == "enabled"


@pytest.mark.asyncio
async def test_notebook_component_stays_provisioning_when_image_ready_but_compute_not(session):
    """T7b: image is built, compute is not yet. Component sits at
    provisioning (image build path was already followed) and is in
    still_waiting until compute flips.
    """
    from app.services.component_queue import process_queued_components

    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "bioaf_scrna_image", "us-central1-docker.pkg.dev/x/y/scrna:abc")
    # Already in provisioning from a previous orchestrator pass (image was kicked off then)
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES (:k, true, 'provisioning', '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = true, status = 'provisioning'"
        ).bindparams(k="jupyterhub")
    )
    await session.commit()

    result = await process_queued_components(session)
    await session.commit()

    assert result.enabled == []
    assert "jupyterhub" in result.still_waiting
    assert await _status_of(session, "jupyterhub") == "provisioning"


@pytest.mark.asyncio
async def test_queued_cellxgene_triggers_cellxgene_image_build_when_storage_ready(session):
    """T6c: cellxgene needs the cellxgene image, distinct from the
    notebook image. Orchestrator kicks off build_cellxgene_image.
    """
    from app.services.component_queue import process_queued_components

    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "cellxgene_image", "null")
    await _set_config(session, "cellxgene_image_build_id", "null")
    await _insert_queued(session, "cellxgene")
    await session.commit()

    with patch(
        "app.services.component_queue.build_cellxgene_image", new=AsyncMock(return_value="build-555")
    ) as cell_mock:
        result = await process_queued_components(session)
        await session.commit()

    assert cell_mock.await_count == 1
    assert "cellxgene" in result.image_builds_started
    assert await _status_of(session, "cellxgene") == "provisioning"


@pytest.mark.asyncio
async def test_no_image_build_attempted_when_storage_not_ready(session):
    """T6d: storage_deployed=false means image build cannot start (working
    bucket does not exist yet). The queued row stays queued, no build is
    submitted.
    """
    from app.services.component_queue import process_queued_components

    await _set_config(session, "storage_deployed", "false")
    await _set_config(session, "compute_deployed", "false")
    await _insert_queued(session, "jupyterhub")
    await session.commit()

    with patch("app.services.component_queue.build_notebook_image", new=AsyncMock()) as build_mock:
        result = await process_queued_components(session)
        await session.commit()

    assert build_mock.await_count == 0
    assert "jupyterhub" in result.still_waiting
    assert await _status_of(session, "jupyterhub") == "queued_for_infra"


@pytest.mark.asyncio
async def test_disabled_rows_are_left_alone(session):
    """A row with enabled=false should never be touched by the orchestrator,
    regardless of its status field. The queue only acts on enabled=true.
    """
    from app.services.component_queue import process_queued_components

    await _set_config(session, "compute_deployed", "true")
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES ('nextflow', false, 'disabled', '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = false, status = 'disabled'"
        )
    )
    await session.commit()

    result = await process_queued_components(session)
    await session.commit()

    assert result.enabled == []
    assert await _status_of(session, "nextflow") == "disabled"
