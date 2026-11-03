"""Component-toggle decisions at the service boundary.

The service decides and records; the route owns permissions, HTTP translation,
and the commit. Nothing here commits, so a rollback must leave no trace.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from app.exceptions import NotFoundError, ValidationError
from app.models.audit_log import AuditLog
from app.services.component_service import KUBERNETES_COMPONENTS, ComponentService

NOTEBOOK_BUILD = "app.services.component_service.build_notebook_image"
CELLXGENE_BUILD = "app.services.cellxgene_image_service.build_cellxgene_image"


async def _component(session, key, enabled=False, status="disabled"):
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES (:key, :enabled, :status, '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = EXCLUDED.enabled, status = EXCLUDED.status"
        ).bindparams(key=key, enabled=enabled, status=status)
    )
    await session.commit()


async def _config(session, **values):
    for key, value in values.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
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
async def test_cluster_cannot_be_toggled(session, admin_user):
    with pytest.raises(ValidationError) as exc:
        await ComponentService.toggle_stack_component(session, "kubernetes_cluster", user_id=admin_user.id)

    assert str(exc.value) == "Cannot toggle kubernetes_cluster directly. Use stack deploy/teardown."


@pytest.mark.asyncio
async def test_unknown_component(session, admin_user):
    with pytest.raises(NotFoundError) as exc:
        await ComponentService.toggle_stack_component(session, "nope", user_id=admin_user.id)

    assert str(exc.value) == "Component 'nope' not found"


@pytest.mark.asyncio
async def test_catalog_key_outside_the_stack_view_is_unknown(session, admin_user):
    """COMPONENT_CATALOG has its own dependency rules; the toggle does not use them."""
    with pytest.raises(NotFoundError):
        await ComponentService.toggle_stack_component(session, "slurm", user_id=admin_user.id)


@pytest.mark.asyncio
async def test_missing_dependencies_are_all_named(session, admin_user):
    await _component(session, "nextflow", enabled=False)
    await _component(session, "qc_dashboard", enabled=False)

    with pytest.raises(ValidationError) as exc:
        await ComponentService.toggle_stack_component(session, "qc_dashboard", user_id=admin_user.id)

    assert str(exc.value) == "Dependency not met: nextflow must be enabled first"
    assert await _state(session, "qc_dashboard") == (False, "disabled")


@pytest.mark.asyncio
async def test_enable_then_disable_with_audit_left_to_the_caller_to_commit(session, admin_user):
    user_id = admin_user.id
    await _component(session, "meilisearch")

    result = await ComponentService.toggle_stack_component(session, "meilisearch", user_id=user_id)

    assert (result.component_key, result.enabled, result.status) == ("meilisearch", True, "enabled")
    await session.rollback()
    assert await _state(session, "meilisearch") == (False, "disabled")
    assert (await session.execute(select(AuditLog))).scalars().all() == []

    await ComponentService.toggle_stack_component(session, "meilisearch", user_id=user_id)
    await session.commit()
    result = await ComponentService.toggle_stack_component(session, "meilisearch", user_id=user_id)
    await session.commit()

    assert (result.enabled, result.status) == (False, "disabled")
    assert await _state(session, "meilisearch") == (False, "disabled")
    actions = (await session.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars().all()
    assert actions == ["enable", "disable"]


@pytest.mark.asyncio
async def test_disabling_ignores_dependencies_and_images(session, admin_user):
    await _component(session, "rstudio", enabled=True, status="enabled")

    with patch(NOTEBOOK_BUILD, new_callable=AsyncMock) as build:
        result = await ComponentService.toggle_stack_component(
            session, "rstudio", user_id=admin_user.id, force_rebuild=True
        )

    assert (result.enabled, result.status) == (False, "disabled")
    build.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "force_rebuild", "builds", "status"),
    [
        ({"bioaf_scrna_image": "img", "notebook_image_build_status": "SUCCESS"}, False, 0, "enabled"),
        ({"bioaf_scrna_image": "img", "notebook_image_build_status": "SUCCESS"}, True, 1, "provisioning"),
        ({"bioaf_scrna_image": "img", "notebook_image_build_status": "FAILURE"}, False, 1, "provisioning"),
        ({"bioaf_scrna_image": "null", "notebook_image_build_status": "SUCCESS"}, False, 1, "provisioning"),
        ({}, False, 1, "provisioning"),
    ],
    ids=["existing", "rebuild", "last-build-failed", "null-image", "no-image"],
)
async def test_notebook_image_decision(session, admin_user, config, force_rebuild, builds, status):
    await _component(session, "kubernetes_cluster", enabled=True, status="running")
    await _component(session, "jupyterhub")
    await _config(session, **config)

    with patch(NOTEBOOK_BUILD, new_callable=AsyncMock) as build:
        result = await ComponentService.toggle_stack_component(
            session, "jupyterhub", user_id=admin_user.id, force_rebuild=force_rebuild
        )

    assert build.await_count == builds
    assert result.status == status


@pytest.mark.asyncio
async def test_build_failure_enables_with_build_failed(session, admin_user):
    await _component(session, "kubernetes_cluster", enabled=True, status="running")
    await _component(session, "rstudio")

    with patch(NOTEBOOK_BUILD, new_callable=AsyncMock, side_effect=RuntimeError("no project")):
        result = await ComponentService.toggle_stack_component(session, "rstudio", user_id=admin_user.id)

    assert (result.enabled, result.status) == (True, "build_failed")
    assert await _state(session, "rstudio") == (True, "build_failed")


@pytest.mark.asyncio
async def test_cellxgene_uses_its_own_image(session, admin_user):
    await _component(session, "cellxgene")
    await _config(session, cellxgene_image="cxg", cellxgene_image_build_status="SUCCESS")

    with (
        patch(NOTEBOOK_BUILD, new_callable=AsyncMock) as notebook,
        patch(CELLXGENE_BUILD, new_callable=AsyncMock) as cellxgene,
    ):
        kept = await ComponentService.toggle_stack_component(session, "cellxgene", user_id=admin_user.id)
        await ComponentService.toggle_stack_component(session, "cellxgene", user_id=admin_user.id)
        rebuilt = await ComponentService.toggle_stack_component(
            session, "cellxgene", user_id=admin_user.id, force_rebuild=True
        )

    assert kept.status == "enabled"
    assert rebuilt.status == "provisioning"
    assert cellxgene.await_count == 1
    notebook.assert_not_called()


def test_stack_catalog_lives_with_the_service():
    keys = {component["key"] for component in KUBERNETES_COMPONENTS}

    assert {"nextflow", "jupyterhub", "rstudio", "cellxgene", "qc_dashboard", "meilisearch", "snakemake"} == keys
