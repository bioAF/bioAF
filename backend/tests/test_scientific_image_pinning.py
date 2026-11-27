"""Plan 03.5B: sessions on the built-in scientific image run the build that was recorded, by digest.

The notebook image build records its pushed digest beside the image's tag. Every launch without an
EnvironmentVersion (interactive sessions and scheduled notebook runs) resolves the image through
``scientific_image``, pinned to that digest, so a moved tag never changes what runs. An administrator's
own image, a failed build or a rebuild clears the digest.
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.adapters.image_build.base import BuildResult
from app.services import notebook_image_service
from app.services.notebook_image_service import scientific_image

TAG = "us-central1-docker.pkg.dev/test/repo/bioaf-scrna:latest"
DIGEST = "sha256:" + "d" * 64
PINNED = "us-central1-docker.pkg.dev/test/repo/bioaf-scrna@" + DIGEST


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


async def _get(session, key):
    return (await session.execute(text("SELECT value FROM platform_config WHERE key = :k"), {"k": key})).scalar()


@pytest_asyncio.fixture
async def building(session):
    await _set(
        session,
        gcp_project_id="test",
        gcp_region="us-central1",
        notebook_image_build_id="b-1",
        notebook_image_build_status="WORKING",
    )


async def _poll(session, status, result):
    with (
        patch.object(notebook_image_service, "check_build_status", AsyncMock(return_value=status)),
        patch.object(notebook_image_service, "check_build_result", AsyncMock(return_value=result)),
        patch.object(notebook_image_service, "get_image_uri", return_value=TAG),
        patch("app.services.infrastructure_operations.settle_external_build", AsyncMock()),
        patch("app.services.component_queue.process_queued_components", AsyncMock()),
        patch.object(notebook_image_service, "_ensure_default_environment", AsyncMock()),
    ):
        await notebook_image_service.poll_image_build(session)
    await session.commit()


@pytest.mark.asyncio
async def test_a_successful_build_records_its_digest_and_launches_pin_to_it(session, building):
    await _poll(session, "SUCCESS", BuildResult("SUCCESS", DIGEST, None))
    assert await _get(session, "bioaf_scrna_image") == TAG
    assert await scientific_image(session) == PINNED


@pytest.mark.asyncio
async def test_a_build_without_a_digest_launches_by_its_tag(session, building):
    await _poll(session, "SUCCESS", BuildResult("SUCCESS", None, None))
    assert await scientific_image(session) == TAG


@pytest.mark.asyncio
async def test_a_failed_build_clears_the_digest(session, building):
    await _set(session, bioaf_scrna_image=TAG, bioaf_scrna_image_digest=DIGEST)
    await _poll(session, "FAILURE", BuildResult("FAILURE", None, None))
    assert await scientific_image(session) is None


@pytest.mark.asyncio
async def test_no_image_means_none(session):
    assert await scientific_image(session) is None
    await _set(session, bioaf_scrna_image="null", bioaf_scrna_image_digest=DIGEST)
    assert await scientific_image(session) is None


@pytest.mark.asyncio
async def test_an_administrators_own_image_is_used_as_given(client, session, admin_token):
    await _set(session, bioaf_scrna_image=TAG, bioaf_scrna_image_digest=DIGEST)
    response = await client.put(
        "/api/v1/settings/container-registry",
        json={"bioaf_scrna_image": "registry.example.org/lab/notebook:2026"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert await scientific_image(session) == "registry.example.org/lab/notebook:2026"


@pytest.mark.asyncio
async def test_a_session_without_an_environment_launches_the_pinned_image(client, session, comp_bio_token):
    await _set(session, compute_deployed="true", bioaf_scrna_image=TAG, bioaf_scrna_image_digest=DIGEST)
    from app.services.notebook_service import NotebookService

    real = NotebookService.launch_session
    seen = {}

    async def spy(*args, **kwargs):
        seen["image"] = kwargs.get("image")
        return await real(*args, **kwargs)

    with patch.object(NotebookService, "launch_session", side_effect=spy):
        response = await client.post(
            "/api/v1/notebooks/sessions",
            json={"session_type": "jupyter", "resource_profile": "small"},
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert response.status_code == 200, response.text
    assert seen["image"] == PINNED


@pytest.mark.asyncio
async def test_unreadable_evidence_defers_publishing_and_does_not_break_the_poll(session, building):
    # Unreadable is not "no evidence": the image waits for a later read (tests/test_build_evidence_retry.py).
    with (
        patch.object(notebook_image_service, "check_build_status", AsyncMock(return_value="SUCCESS")),
        patch.object(notebook_image_service, "resolve_image_credentials", AsyncMock(side_effect=RuntimeError("x"))),
        patch.object(notebook_image_service, "get_image_uri", return_value=TAG),
        patch("app.services.infrastructure_operations.settle_external_build", AsyncMock()),
        patch("app.services.component_queue.process_queued_components", AsyncMock()),
        patch.object(notebook_image_service, "_ensure_default_environment", AsyncMock()),
    ):
        assert await notebook_image_service.poll_image_build(session) == "SUCCESS"
    await session.commit()
    assert await scientific_image(session) is None
