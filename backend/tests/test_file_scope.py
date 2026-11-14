"""File membership shared by the listing and data exports (app/services/file_scope.py)."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.models.file import File
from app.models.organization import Organization
from app.services import file_scope
from app.services.file_service import FileService


async def _org(session, name: str) -> int:
    org = Organization(name=name, setup_complete=True)
    session.add(org)
    await session.flush()
    return org.id


async def _seed(session, *, home: int, foreign: int) -> None:
    """Experiment 1 is the home org's; experiment 2 (project 2) is the foreign org's.

    File 10 (home) is linked to a sample of the foreign experiment and to nothing else.
    """
    await session.execute(
        text(
            "INSERT INTO projects (id, organization_id, name, status) VALUES "
            "(1, :home, 'Home', 'active'), (2, :foreign, 'Foreign', 'active')"
        ),
        {"home": home, "foreign": foreign},
    )
    await session.execute(
        text(
            "INSERT INTO experiments (id, organization_id, project_id, name, status) VALUES "
            "(1, :home, 1, 'Home exp', 'registered'), (2, :foreign, 2, 'Foreign exp', 'registered')"
        ),
        {"home": home, "foreign": foreign},
    )
    await session.execute(
        text("INSERT INTO samples (id, experiment_id, external_id, status) VALUES (20, 2, 'F1', 'registered')")
    )
    await session.execute(
        text(
            "INSERT INTO files (id, organization_id, filename, gcs_uri, storage_uri, file_type, source_type) "
            "VALUES (10, :home, 'stray.txt', 'gs://b/stray.txt', 'gs://b/stray.txt', 'txt', 'upload')"
        ),
        {"home": home},
    )
    await session.execute(text("INSERT INTO sample_files (sample_id, file_id) VALUES (20, 10)"))
    await session.commit()


@pytest.mark.asyncio
async def test_a_foreign_experiments_samples_contribute_no_members(session):
    home = await _org(session, "Home")
    foreign = await _org(session, "Foreign")
    await _seed(session, home=home, foreign=foreign)

    by_experiment = await session.execute(
        select(File.id).where(file_scope.live_files(home), file_scope.experiment_files(2, home))
    )
    by_project = await session.execute(
        select(File.id).where(file_scope.live_files(home), file_scope.project_files(2, home))
    )

    assert by_experiment.scalars().all() == []
    assert by_project.scalars().all() == []


@pytest.mark.asyncio
async def test_listing_by_a_foreign_experiment_finds_nothing(session):
    home = await _org(session, "Home")
    foreign = await _org(session, "Foreign")
    await _seed(session, home=home, foreign=foreign)

    files, total = await FileService.list_files(session, home, experiment_id=2)

    assert (files, total) == ([], 0)
