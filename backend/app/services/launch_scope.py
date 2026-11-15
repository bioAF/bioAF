"""The project and experiment a notebook or work node is launched in, and its environment version, checked against
the caller's organization before any session row or provider call (issue #103).

A missing id and another organization's id are refused the same way (``NotFoundError``), so a refusal says nothing
about what exists elsewhere. An experiment launched with a project must belong to that project; a standalone
experiment (no project) is launched without one. An experiment launched without a project records the
experiment's own project, so the session and its outputs are associated with it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundError, ValidationError
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.project import Project


@dataclass(frozen=True)
class LaunchScope:
    project_id: int | None
    experiment_id: int | None


async def resolve_launch_scope(
    session: AsyncSession, org_id: int, *, project_id: int | None, experiment_id: int | None
) -> LaunchScope:
    if project_id is not None:
        found = await session.scalar(
            select(Project.id).where(Project.id == project_id, Project.organization_id == org_id)
        )
        if found is None:
            raise NotFoundError("Project not found")
    if experiment_id is None:
        return LaunchScope(project_id, None)
    experiment = (
        await session.execute(
            select(Experiment.id, Experiment.project_id).where(
                Experiment.id == experiment_id, Experiment.organization_id == org_id
            )
        )
    ).first()
    if experiment is None:
        raise NotFoundError("Experiment not found")
    if project_id is None:
        return LaunchScope(experiment.project_id, experiment_id)
    if experiment.project_id != project_id:
        raise ValidationError("The experiment does not belong to the selected project.")
    return LaunchScope(project_id, experiment_id)


async def owned_environment_version(session: AsyncSession, org_id: int, version_id: int) -> EnvironmentVersion:
    """The organization's environment version, with its environment loaded, or ``NotFoundError``."""
    version = await session.scalar(
        select(EnvironmentVersion)
        .join(Environment, Environment.id == EnvironmentVersion.environment_id)
        .options(selectinload(EnvironmentVersion.environment))
        .where(EnvironmentVersion.id == version_id, Environment.organization_id == org_id)
    )
    if version is None:
        raise NotFoundError("Environment version not found")
    return version
