"""Plan 03.5B: a notebook EnvironmentVersion records what its build proved.

A successful notebook build records the runtime contract's result for its definition, bound to the
built image and build. Anything else (pipelines, failed builds, versions built before the contract,
a rebuild in progress) carries no claim, and API responses say "unverified" rather than nothing.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.adapters.image_build.base import BuildResult
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.services.environment_build_service import EnvironmentBuildService
from app.services.environment_service import DEFAULT_NOTEBOOK_CONDA_YML

PY_ONLY = "name: py\nchannels: [conda-forge]\ndependencies: [python=3.11, jupyterlab]\n"


async def _version(
    session, user, *, environment_type="notebook", definition=DEFAULT_NOTEBOOK_CONDA_YML, status="building"
):
    env = Environment(
        name=f"Env {environment_type}",
        organization_id=user.organization_id,
        created_by_user_id=user.id,
        environment_type=environment_type,
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status=status,
        build_id="build-1",
        image_uri="us-docker.pkg.dev/p/r/env:v1.1",
        definition_format="conda",
        definition_content=definition,
        created_by_user_id=user.id,
    )
    session.add(version)
    for k, v in (("gcp_project_id", "test-project"), ("gcp_region", "us-central1")):
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()
    return version


DIGEST = "sha256:" + "c" * 64


def _evidence(rstudio="verified"):
    return BuildResult("SUCCESS", DIGEST, {"contract": 1, "jupyter": "verified", "rstudio": rstudio})


async def _poll(session, outcome, result=None):
    with (
        patch("app.services.notebook_image_service.check_build_status", return_value=outcome),
        patch(
            "app.services.notebook_image_service.check_build_result",
            return_value=result or BuildResult(outcome, None, None),
        ),
    ):
        await EnvironmentBuildService.poll_in_progress_builds(session)
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("definition, rstudio", [(DEFAULT_NOTEBOOK_CONDA_YML, "verified"), (PY_ONLY, "unsupported")])
async def test_a_successful_notebook_build_records_its_proven_capabilities(session, admin_user, definition, rstudio):
    version = await _version(session, admin_user, definition=definition)
    vid = version.id
    await _poll(session, "SUCCESS", _evidence(rstudio))
    session.expire_all()
    row = await session.get(EnvironmentVersion, vid)
    assert row.notebook_capability["contract"] == 1
    assert (row.notebook_capability["jupyter"], row.notebook_capability["rstudio"]) == ("verified", rstudio)
    assert row.notebook_capability["image_uri"] == "us-docker.pkg.dev/p/r/env:v1.1"
    assert row.notebook_capability["image_digest"] == DIGEST
    assert row.notebook_capability["build_id"] == "build-1"


@pytest.mark.asyncio
async def test_a_build_that_returned_no_evidence_stays_unverified(session, admin_user):
    # For example, a build submitted before this release that finishes after the upgrade.
    version = await _version(session, admin_user)
    vid = version.id
    await _poll(session, "SUCCESS")
    session.expire_all()
    row = await session.get(EnvironmentVersion, vid)
    assert (row.notebook_capability["jupyter"], row.notebook_capability["rstudio"]) == ("unverified", "unverified")


@pytest.mark.asyncio
async def test_a_failed_build_or_a_pipeline_records_no_claim(session, admin_user):
    failed = await _version(session, admin_user)
    fid = failed.id
    await _poll(session, "FAILURE")
    pipeline = await _version(session, admin_user, environment_type="pipeline", definition=PY_ONLY)
    pid = pipeline.id
    await _poll(session, "SUCCESS")
    session.expire_all()
    assert (await session.get(EnvironmentVersion, fid)).notebook_capability is None
    assert (await session.get(EnvironmentVersion, pid)).notebook_capability is None


@pytest.mark.asyncio
async def test_api_says_unverified_for_a_version_built_before_the_contract(client, session, admin_user, admin_token):
    version = await _version(session, admin_user, status="ready")
    env_id = version.environment_id
    response = await client.get(f"/api/v1/environments/{env_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    summary = response.json()["versions"][0]
    assert summary["notebook_capability"] == {"contract": None, "jupyter": "unverified", "rstudio": "unverified"}


@pytest.mark.asyncio
async def test_the_default_scientific_environment_records_its_proven_capabilities(session, admin_user):
    from unittest.mock import MagicMock

    from app.services.notebook_image_service import _ensure_default_environment

    adapter = MagicMock()
    adapter.image_storage_pip_packages.return_value = "google-cloud-storage"
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await _ensure_default_environment(session, "us-docker.pkg.dev/p/r/bioaf-scrna:latest", _evidence(), "b-9")
    await session.commit()
    row = (await session.execute(text("SELECT notebook_capability FROM environment_versions"))).scalar_one()
    assert (row["jupyter"], row["rstudio"]) == ("verified", "verified")
    assert row["image_digest"] == DIGEST and row["build_id"] == "b-9"
