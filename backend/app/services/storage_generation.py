"""The storage generation: the suffix that names one storage deployment.

``storage_stack_uid`` names every bucket and topic of one storage deployment.
Its lifecycle:

- A storage deployment persists its generation before Terraform runs, so a
  retry after a failed or interrupted attempt resumes the same resources.
- Only a confirmed storage destroy retires it; a failed, partial or
  interrupted destroy keeps it.
- A new generation is allocated only when recorded lifecycle shows no storage
  from an earlier attempt can remain. A missing value or a false deployed flag
  alone does not prove that, so an earlier attempt whose identity cannot be
  established is refused rather than given a new name over its resources.

A new generation separates deployments by name. It does not rely on any
provider reserving a deleted bucket name.

Each generation also carries one identity marker per bucket (``storage_bucket_markers``), written
on the bucket as a label (GCS) or tag (S3) by Terraform and recorded with the bucket's identity,
so a bucket deleted and recreated under the same name is told apart. Markers come from a new
storage deployment (origin "deploy": bioAF created the buckets carrying them) or are added to
existing buckets by Check for Infrastructure Updates (origin "update": an administrator still
confirms those buckets).
"""

from __future__ import annotations

import json
import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StateError
from app.models.component import TerraformRun
from app.platform.platform_config_service import PlatformConfigService
from app.services.deployment_outputs import STORAGE_BUCKETS_TO_EMPTY, identifier_value

logger = logging.getLogger("bioaf.storage_generation")

STORAGE_GENERATION_KEY = "storage_stack_uid"
MARKERS_KEY = "storage_bucket_markers"

UNRESOLVED_PARTIAL_MESSAGE = (
    "An earlier storage deployment may have created buckets that bioAF cannot identify, "
    "so nothing was changed. Confirm the existing buckets under Infrastructure > Components "
    "before deploying storage again."
)


class StorageGenerationUnresolved(StateError):
    """Storage from an earlier attempt may exist and its generation is unknown."""


async def _last_storage_apply_since_destroy(session: AsyncSession) -> TerraformRun | None:
    """The newest storage apply after the last confirmed storage destroy, if any.

    A run reaches ``action == "apply"`` only once Terraform starts applying, so a
    plan that failed or was never confirmed is not evidence of resources.
    """
    last_destroy = (
        await session.execute(
            select(TerraformRun.id)
            .where(
                TerraformRun.module_name == "storage",
                TerraformRun.action == "destroy",
                TerraformRun.status == "completed",
            )
            .order_by(TerraformRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    query = select(TerraformRun).where(TerraformRun.module_name == "storage", TerraformRun.action == "apply")
    if last_destroy is not None:
        query = query.where(TerraformRun.id > last_destroy)
    return (await session.execute(query.order_by(TerraformRun.id.desc()).limit(1))).scalar_one_or_none()


async def _recorded_bucket_names(session: AsyncSession) -> bool:
    values = await PlatformConfigService.get_many(session, list(STORAGE_BUCKETS_TO_EMPTY))
    return any(identifier_value(v) for v in values.values())


async def _store_generation(session: AsyncSession, value: str) -> str:
    """Store ``value`` unless a real generation is already stored; return the stored one.

    Locks the existing row, so two concurrent deploys cannot both allocate.
    """
    current = await PlatformConfigService.get_for_update(session, STORAGE_GENERATION_KEY)
    if identifier_value(current):
        return identifier_value(current)
    if current is None:
        stored = await PlatformConfigService.insert_if_absent(session, STORAGE_GENERATION_KEY, value)
    else:
        # The row is locked above, so replacing its blank value cannot race another allocation.
        await PlatformConfigService.set(session, STORAGE_GENERATION_KEY, value)
        stored = value
    return identifier_value(stored)


async def ensure_storage_generation(session: AsyncSession) -> str:
    """Return the generation the next storage deployment must use, persisting it.

    The caller commits before launching Terraform. Raises
    StorageGenerationUnresolved when an earlier attempt may have left resources
    whose generation is not recorded.
    """
    existing = identifier_value(await PlatformConfigService.get(session, STORAGE_GENERATION_KEY))
    if existing:
        await ensure_markers(session, origin="update")
        return existing

    earlier = await _last_storage_apply_since_destroy(session)
    if earlier is not None:
        recorded = identifier_value(str((earlier.tfvars_json or {}).get("stack_uid") or ""))
        if not recorded:
            logger.warning("Refusing storage deploy: run %s applied storage without a recorded generation", earlier.id)
            raise StorageGenerationUnresolved(UNRESOLVED_PARTIAL_MESSAGE)
        logger.info("Storage deploy resumes generation %s recorded by run %s", recorded, earlier.id)
        generation = await _store_generation(session, recorded)
        await ensure_markers(session, origin="update")
        return generation

    if await _recorded_bucket_names(session):
        logger.warning("Refusing storage deploy: bucket names are recorded without a storage generation")
        raise StorageGenerationUnresolved(UNRESOLVED_PARTIAL_MESSAGE)

    generation = await _store_generation(session, secrets.token_hex(3))
    # bioAF is about to create these buckets: the markers they carry identify them as its own.
    await ensure_markers(session, origin="deploy")
    return generation


def _parse_markers(raw: str | None) -> dict:
    if not raw or raw == "null":
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def ensure_markers(session: AsyncSession, *, origin: str) -> dict:
    """The current generation's bucket markers, generated (with ``origin``) when it has none.

    Existing markers of the same generation are kept, whatever their origin. The caller commits.
    """
    from app.services.storage_identity import STORES

    generation = identifier_value(await PlatformConfigService.get(session, STORAGE_GENERATION_KEY))
    if not generation:
        return {}
    current = _parse_markers(await PlatformConfigService.get(session, MARKERS_KEY))
    if current.get("generation") == generation and set(STORES) <= set(current.get("markers") or {}):
        return current
    kept = (current.get("markers") or {}) if current.get("generation") == generation else {}
    value = {
        "generation": generation,
        "origin": current.get("origin", origin) if kept else origin,
        "markers": {store: kept.get(store) or secrets.token_hex(8) for store in STORES},
    }
    await PlatformConfigService.set(session, MARKERS_KEY, json.dumps(value, sort_keys=True))
    logger.info("Recorded storage identity markers for generation %s (%s)", generation, value["origin"])
    return value


def markers_for_tfvars(config: dict, storage_suffix: str | None) -> dict | None:
    """The bucket markers to pass to the storage module, when they belong to its generation."""
    markers = _parse_markers(config.get(MARKERS_KEY))
    if storage_suffix and markers.get("generation") == storage_suffix and markers.get("markers"):
        return dict(markers["markers"])
    return None


async def retire_storage_generation(session: AsyncSession) -> None:
    """Forget the generation and its bucket identities after a confirmed storage destroy.

    The caller commits.
    """
    from app.services.storage_identity import retire_identities

    await PlatformConfigService.set(session, STORAGE_GENERATION_KEY, None)
    await PlatformConfigService.set(session, MARKERS_KEY, None)
    await retire_identities(session)
