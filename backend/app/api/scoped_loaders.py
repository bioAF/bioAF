"""Loaders that answer 404 for a record owned by another Organization.

A foreign record and a missing one must look the same to the caller, so routes
load through these rather than by bare primary key.
"""

from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.role import Role
from app.models.sample import Sample
from app.models.user import User


async def owned_user_or_404(session: AsyncSession, user_id: int, org_id: int) -> User:
    result = await session.execute(select(User).where(User.id == user_id, User.organization_id == org_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def owned_role_or_404(session: AsyncSession, role_id: int, org_id: int) -> Role:
    result = await session.execute(select(Role).where(Role.id == role_id, Role.organization_id == org_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def require_owned_experiment(session: AsyncSession, experiment_id: int, org_id: int) -> None:
    result = await session.execute(
        select(Experiment.id).where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(404, "Experiment not found")


async def owned_sample_ids(session: AsyncSession, sample_ids: Iterable[int], org_id: int) -> set[int]:
    """The subset of these IDs that exist in this Organization. Samples carry no
    organization of their own; they belong through their experiment."""
    result = await session.execute(
        select(Sample.id)
        .join(Experiment, Experiment.id == Sample.experiment_id)
        .where(Sample.id.in_(set(sample_ids)), Experiment.organization_id == org_id)
    )
    return set(result.scalars().all())


async def require_owned_sample(session: AsyncSession, sample_id: int, org_id: int) -> None:
    if not await owned_sample_ids(session, [sample_id], org_id):
        raise HTTPException(404, "Sample not found")
