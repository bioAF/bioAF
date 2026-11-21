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
"""

from __future__ import annotations

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
        return existing

    earlier = await _last_storage_apply_since_destroy(session)
    if earlier is not None:
        recorded = identifier_value(str((earlier.tfvars_json or {}).get("stack_uid") or ""))
        if not recorded:
            logger.warning("Refusing storage deploy: run %s applied storage without a recorded generation", earlier.id)
            raise StorageGenerationUnresolved(UNRESOLVED_PARTIAL_MESSAGE)
        logger.info("Storage deploy resumes generation %s recorded by run %s", recorded, earlier.id)
        return await _store_generation(session, recorded)

    if await _recorded_bucket_names(session):
        logger.warning("Refusing storage deploy: bucket names are recorded without a storage generation")
        raise StorageGenerationUnresolved(UNRESOLVED_PARTIAL_MESSAGE)

    return await _store_generation(session, secrets.token_hex(3))


async def retire_storage_generation(session: AsyncSession) -> None:
    """Forget the generation and its bucket identities after a confirmed storage destroy.

    The caller commits.
    """
    from app.services.storage_identity import retire_identities

    await PlatformConfigService.set(session, STORAGE_GENERATION_KEY, None)
    await retire_identities(session)
