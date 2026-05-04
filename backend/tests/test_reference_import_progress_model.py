"""TDD: ReferenceImportProgress model — tracks GKE-job-driven import state.

Spec §3: progress writes go to a `reference_import_progress` table.
"""

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_reference_import_progress_row_can_be_created(session, admin_user):
    """Smoke test: schema exists and accepts a row tied to a reference dataset."""
    from app.models.reference_dataset import ReferenceDataset
    from app.models.reference_import_progress import ReferenceImportProgress

    ref = ReferenceDataset(
        organization_id=admin_user.organization_id,
        name="GENCODE Import Test",
        category="annotation",
        scope="internal",
        version="v45",
        gcs_prefix="annotation/gencode-import-test/v45/",
        uploaded_by_user_id=admin_user.id,
        status="uploading",
    )
    session.add(ref)
    await session.flush()

    progress = ReferenceImportProgress(
        reference_id=ref.id,
        status="pending",
        import_job_id="refimport-1-abcd",
        bytes_downloaded=0,
        total_bytes=None,
    )
    session.add(progress)
    await session.flush()

    result = await session.execute(
        select(ReferenceImportProgress).where(ReferenceImportProgress.reference_id == ref.id)
    )
    fetched = result.scalar_one()
    assert fetched.status == "pending"
    assert fetched.import_job_id == "refimport-1-abcd"
    assert fetched.bytes_downloaded == 0
