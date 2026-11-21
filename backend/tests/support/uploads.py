"""Seed a signed upload the way initiate does: a durable checkpoint, before any byte arrives.

Replaces writing into the old in-memory pending-upload dictionary (storage lifecycle 11F).
"""

from __future__ import annotations

import uuid

from app.services.upload_service import _new_checkpoint


async def seed_signed_upload(
    *,
    org_id: int,
    user_id: int | None,
    filename: str,
    gcs_uri: str | None = None,
    upload_id: str | None = None,
    expected_size: int | None = None,
    expected_md5: str | None = None,
    project_id: int | None = None,
    experiment_id: int | None = None,
    sample_ids: list[int] | None = None,
    is_global: bool = False,
    external_id: str | None = None,
) -> str:
    upload_id = upload_id or str(uuid.uuid4())
    await _new_checkpoint(
        id=upload_id,
        organization_id=org_id,
        user_id=user_id,
        kind="signed",
        stage="issued",
        filename=filename,
        source_uri=gcs_uri or f"gs://bioaf-ingest-test/uploads/{upload_id}/{filename}",
        expected_size=expected_size,
        expected_md5=expected_md5,
        project_id=project_id,
        experiment_id=experiment_id,
        sample_ids=sample_ids or [],
        is_global=is_global,
        external_id=external_id,
    )
    return upload_id
