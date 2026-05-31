"""Component queue drain orchestrator.

The wizard lets a user select components before the underlying
infrastructure is fully deployed. Selected components land in
`component_states` as `enabled=true, status='queued_for_infra'`. As
prereqs flip ready (storage_deployed, compute_deployed, image builds
succeed), this module's `process_queued_components` is invoked from
each lifecycle hook to drain the queue: image-build-only components
get their build kicked off as soon as storage is ready; cluster-only
components flip to enabled as soon as compute is ready; image-needing
components flip to enabled once both their image and compute are up.

The orchestrator is idempotent. Calling it repeatedly with the same
ready state is safe: rows already in the right status are not
re-touched, and image builds are not re-submitted while a build is
already in flight (status='provisioning' on any image-bound row of the
same image family is the signal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cellxgene_image_service import build_cellxgene_image
from app.services.notebook_image_service import build_notebook_image

logger = logging.getLogger("bioaf.component_queue")

# Components that need the notebook (bioaf-scrna) image.
_NOTEBOOK_IMAGE_COMPONENTS = {"rstudio", "jupyterhub"}
# Components that need the cellxgene image.
_CELLXGENE_IMAGE_COMPONENTS = {"cellxgene"}
# Components whose only prereq is the cluster.
_CLUSTER_ONLY_COMPONENTS = {"nextflow", "snakemake", "qc_dashboard", "meilisearch"}


@dataclass
class QueueProcessResult:
    """Outcome of one queue drain pass."""

    enabled: list[str] = field(default_factory=list)
    image_builds_started: list[str] = field(default_factory=list)
    still_waiting: list[str] = field(default_factory=list)


async def _read_config(session: AsyncSession, key: str) -> str | None:
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = :k").bindparams(k=key))).first()
    if not row:
        return None
    value = row[0]
    if value == "null":
        return None
    return value


async def _flag_true(session: AsyncSession, key: str) -> bool:
    return (await _read_config(session, key)) == "true"


async def _set_status(session: AsyncSession, component_key: str, status: str) -> None:
    await session.execute(
        text("UPDATE component_states SET status = :s WHERE component_key = :k").bindparams(s=status, k=component_key)
    )


async def _actionable_rows(session: AsyncSession) -> list[tuple[str, str]]:
    """Rows the orchestrator can act on: queued_for_infra or provisioning.

    Provisioning rows are included so that when compute flips ready while a
    notebook image build is still in flight, the orchestrator can flip the
    row to enabled on the next pass. Disabled, build_failed, destroying, and
    enabled rows are intentionally skipped.
    """
    rows = (
        await session.execute(
            text(
                "SELECT component_key, status FROM component_states "
                "WHERE enabled = true AND status IN ('queued_for_infra', 'provisioning') "
                "ORDER BY component_key"
            )
        )
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


async def _provisioning_image_components(session: AsyncSession, image_family: set[str]) -> bool:
    """True if any image-family component is already in provisioning."""
    placeholders = ", ".join(f":k{i}" for i in range(len(image_family)))
    sql = (
        "SELECT 1 FROM component_states "
        f"WHERE enabled = true AND status = 'provisioning' AND component_key IN ({placeholders}) "
        "LIMIT 1"
    )
    params = {f"k{i}": k for i, k in enumerate(sorted(image_family))}
    row = (await session.execute(text(sql).bindparams(**params))).first()
    return row is not None


async def process_queued_components(session: AsyncSession) -> QueueProcessResult:
    """Drain whatever's actionable in the queue, given the current ready state.

    This is called from three lifecycle hooks: after storage_deployed flips
    true, after compute_deployed flips true, and after each image build poll
    that returns SUCCESS. Safe to call at any other time too.
    """
    result = QueueProcessResult()

    actionable = await _actionable_rows(session)
    if not actionable:
        return result

    storage_ready = await _flag_true(session, "storage_deployed")
    compute_ready = await _flag_true(session, "compute_deployed")
    notebook_image_ready = bool(await _read_config(session, "bioaf_scrna_image"))
    cellxgene_image_ready = bool(await _read_config(session, "cellxgene_image"))

    notebook_build_in_flight = await _provisioning_image_components(session, _NOTEBOOK_IMAGE_COMPONENTS)
    cellxgene_build_in_flight = await _provisioning_image_components(session, _CELLXGENE_IMAGE_COMPONENTS)

    notebook_build_kicked_this_pass = False
    cellxgene_build_kicked_this_pass = False

    for component_key, current_status in actionable:
        if component_key in _CLUSTER_ONLY_COMPONENTS:
            if compute_ready:
                await _set_status(session, component_key, "enabled")
                result.enabled.append(component_key)
            else:
                result.still_waiting.append(component_key)

        elif component_key in _NOTEBOOK_IMAGE_COMPONENTS:
            if notebook_image_ready and compute_ready:
                await _set_status(session, component_key, "enabled")
                result.enabled.append(component_key)
            elif current_status == "provisioning":
                # Build already in flight (or completed but compute not up yet);
                # nothing to do this pass.
                result.still_waiting.append(component_key)
            elif storage_ready and not notebook_image_ready:
                if not notebook_build_in_flight and not notebook_build_kicked_this_pass:
                    try:
                        await build_notebook_image(session)
                        notebook_build_kicked_this_pass = True
                        logger.info("Kicked off notebook image build for queued component %s", component_key)
                    except Exception as exc:
                        logger.warning("Failed to kick off notebook image build for %s: %s", component_key, exc)
                        result.still_waiting.append(component_key)
                        continue
                await _set_status(session, component_key, "provisioning")
                result.image_builds_started.append(component_key)
            else:
                result.still_waiting.append(component_key)

        elif component_key in _CELLXGENE_IMAGE_COMPONENTS:
            if cellxgene_image_ready and compute_ready:
                await _set_status(session, component_key, "enabled")
                result.enabled.append(component_key)
            elif current_status == "provisioning":
                result.still_waiting.append(component_key)
            elif storage_ready and not cellxgene_image_ready:
                if not cellxgene_build_in_flight and not cellxgene_build_kicked_this_pass:
                    try:
                        await build_cellxgene_image(session)
                        cellxgene_build_kicked_this_pass = True
                        logger.info("Kicked off cellxgene image build for queued component %s", component_key)
                    except Exception as exc:
                        logger.warning("Failed to kick off cellxgene image build for %s: %s", component_key, exc)
                        result.still_waiting.append(component_key)
                        continue
                await _set_status(session, component_key, "provisioning")
                result.image_builds_started.append(component_key)
            else:
                result.still_waiting.append(component_key)

        else:
            logger.warning("Queued component %s has no known prereq mapping; leaving in queue", component_key)
            result.still_waiting.append(component_key)

    await session.flush()
    return result
