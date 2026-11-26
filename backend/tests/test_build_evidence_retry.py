"""Plan 03.5B: a build's evidence is read until it is readable, not lost to one transient failure.

"Could not read the build's result" (a transient API or credential failure) is not "the build returned
no evidence". The built-in image is not published, and a notebook EnvironmentVersion stays building,
until the result is read or ``MAX_EVIDENCE_ATTEMPTS`` reads have failed; only then do they go ahead by
tag, unverified.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.adapters.image_build.base import BuildResult
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.services import notebook_image_service
from app.services.environment_build_service import EnvironmentBuildService
from app.services.environment_service import DEFAULT_NOTEBOOK_CONDA_YML
from app.services.notebook_image_service import MAX_EVIDENCE_ATTEMPTS, scientific_image

TAG = "us-central1-docker.pkg.dev/test/repo/bioaf-scrna:latest"
DIGEST = "sha256:" + "e" * 64
UNREADABLE = BuildResult("UNKNOWN", None, None)
EVIDENCE = BuildResult("SUCCESS", DIGEST, {"contract": 1, "jupyter": "verified", "rstudio": "verified"})


async def _set(session, **values):
    for key, value in values.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()


# -- The built-in scientific image --------------------------------------------------------------


async def _poll_image(session, result):
    reads = AsyncMock(return_value=result)
    with (
        patch.object(notebook_image_service, "check_build_status", AsyncMock(return_value="SUCCESS")),
        patch.object(notebook_image_service, "check_build_result", reads),
        patch.object(notebook_image_service, "get_image_uri", return_value=TAG),
        patch("app.services.infrastructure_operations.settle_external_build", AsyncMock()),
        patch("app.services.component_queue.process_queued_components", AsyncMock()),
        patch.object(notebook_image_service, "_ensure_default_environment", AsyncMock()),
    ):
        status = await notebook_image_service.poll_image_build(session)
    await session.commit()
    return status, reads.await_count


@pytest.mark.asyncio
async def test_an_unreadable_result_is_read_again_and_the_image_then_pins_to_its_digest(session):
    await _set(
        session,
        gcp_project_id="test",
        gcp_region="us-central1",
        notebook_image_build_id="b-1",
        notebook_image_build_status="WORKING",
    )
    status, reads = await _poll_image(session, UNREADABLE)
    assert (status, reads) == ("SUCCESS", 1)
    assert await scientific_image(session) is None  # not published on a guess
    status, reads = await _poll_image(session, EVIDENCE)
    assert (status, reads) == ("SUCCESS", 1)
    assert await scientific_image(session) == "us-central1-docker.pkg.dev/test/repo/bioaf-scrna@" + DIGEST
    # Once read, it is not read again.
    _, reads = await _poll_image(session, EVIDENCE)
    assert reads == 0


@pytest.mark.asyncio
async def test_after_the_last_attempt_the_image_is_published_by_its_tag(session):
    await _set(
        session,
        gcp_project_id="test",
        gcp_region="us-central1",
        notebook_image_build_id="b-1",
        notebook_image_build_status="WORKING",
    )
    for _ in range(MAX_EVIDENCE_ATTEMPTS):
        await _poll_image(session, UNREADABLE)
    assert await scientific_image(session) == TAG


@pytest.mark.asyncio
async def test_a_new_build_starts_its_evidence_attempts_afresh(session):
    await _set(
        session,
        gcp_project_id="test",
        gcp_region="us-central1",
        notebook_image_build_id="b-1",
        notebook_image_build_status="WORKING",
    )
    await _poll_image(session, UNREADABLE)
    await notebook_image_service._reset_scientific_image(session)
    await session.commit()
    value = (
        await session.execute(
            text("SELECT value FROM platform_config WHERE key = :k"),
            {"k": notebook_image_service.SCIENTIFIC_IMAGE_EVIDENCE_ATTEMPTS_KEY},
        )
    ).scalar()
    assert value in (None, "null")


# -- Notebook EnvironmentVersions ---------------------------------------------------------------


async def _version(session, user):
    env = Environment(
        name="Env",
        organization_id=user.organization_id,
        created_by_user_id=user.id,
        environment_type="notebook",
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="building",
        build_id="build-1",
        image_uri="us-docker.pkg.dev/p/r/env:v1.1",
        definition_format="conda",
        definition_content=DEFAULT_NOTEBOOK_CONDA_YML,
        created_by_user_id=user.id,
    )
    session.add(version)
    await session.commit()
    await _set(session, gcp_project_id="test-project", gcp_region="us-central1")
    return version.id


async def _poll_versions(session, result):
    with (
        patch("app.services.notebook_image_service.check_build_status", return_value="SUCCESS"),
        patch("app.services.notebook_image_service.check_build_result", return_value=result),
    ):
        await EnvironmentBuildService.poll_in_progress_builds(session)
        await session.commit()
    session.expire_all()


@pytest.mark.asyncio
async def test_a_version_whose_result_is_unreadable_stays_building_until_it_is_read(session, admin_user):
    vid = await _version(session, admin_user)
    await _poll_versions(session, UNREADABLE)
    assert (await session.get(EnvironmentVersion, vid)).status == "building"
    await _poll_versions(session, EVIDENCE)
    row = await session.get(EnvironmentVersion, vid)
    assert row.status == "ready"
    assert row.notebook_capability["jupyter"] == "verified"
    assert row.notebook_capability["image_digest"] == DIGEST


@pytest.mark.asyncio
async def test_after_the_last_attempt_a_version_is_ready_and_unverified(session, admin_user):
    vid = await _version(session, admin_user)
    for _ in range(MAX_EVIDENCE_ATTEMPTS):
        await _poll_versions(session, UNREADABLE)
    row = await session.get(EnvironmentVersion, vid)
    assert row.status == "ready"
    assert (row.notebook_capability["jupyter"], row.notebook_capability["rstudio"]) == ("unverified", "unverified")
    assert row.notebook_capability["image_digest"] is None


@pytest.mark.asyncio
async def test_the_production_job_keeps_each_retry(session, admin_user):
    # Through the job the scheduler runs, which commits only when the poll reports a change.
    from app.database import async_session_factory
    from app.tasks.jobs import _environment_build_poll

    vid = await _version(session, admin_user)
    seen = []
    for _ in range(2):
        with (
            patch("app.services.notebook_image_service.check_build_status", return_value="SUCCESS"),
            patch("app.services.notebook_image_service.check_build_result", return_value=UNREADABLE),
        ):
            async with async_session_factory() as job_session:
                await _environment_build_poll(job_session)
        async with async_session_factory() as reader:
            row = await reader.get(EnvironmentVersion, vid)
            seen.append((row.notebook_capability or {}).get("evidence_attempts"))
    assert seen == [1, 2]
