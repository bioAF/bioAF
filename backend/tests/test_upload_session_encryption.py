"""Upload session handles are encrypted at rest (ADR-047).

A GCS resumable session URL is a bearer credential: anyone holding a live one can send data into that
upload. It is stored with the existing EncryptedString type, migration 143 encrypts handles stored
before this, and key rotation re-encrypts them.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models.upload_checkpoint import UploadCheckpoint
from app.platform import encryption_service
from app.platform.encryption_service import looks_like_ciphertext

CREDENTIAL = "https://storage.googleapis.com/upload/storage/v1/b/bkt/o?upload_id=FAKE_TEST_CREDENTIAL"
_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "143_encrypt_upload_session_handles.py"


def _checkpoint(admin_user, handle=CREDENTIAL) -> UploadCheckpoint:
    upload_id = str(uuid.uuid4())
    return UploadCheckpoint(
        id=upload_id,
        organization_id=admin_user.organization_id,
        user_id=admin_user.id,
        kind="session",
        stage="issued",
        filename="a.txt",
        source_uri=f"gs://bkt/uploads/{upload_id}/a.txt",
        reference="UPL-TEST01",
        session_protocol="gcs_resumable",
        session_handle=handle,
    )


async def _raw(session, upload_id):
    return await session.scalar(
        text("SELECT session_handle FROM upload_checkpoints WHERE id=:id").bindparams(id=upload_id)
    )


@pytest.mark.asyncio
async def test_a_session_handle_is_ciphertext_in_the_database_and_plain_to_the_app(session, admin_user):
    cp = _checkpoint(admin_user)
    upload_id = cp.id
    session.add(cp)
    await session.commit()
    raw = await _raw(session, upload_id)
    assert looks_like_ciphertext(raw) and CREDENTIAL not in raw
    session.expire_all()
    restored = await session.get(UploadCheckpoint, upload_id)
    assert restored is not None and restored.session_handle == CREDENTIAL


def _upgrade(sync_conn):
    spec = importlib.util.spec_from_file_location("migration_143", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(sync_conn)):
        module.upgrade()


@pytest.mark.asyncio
async def test_migration_143_encrypts_handles_stored_in_plain_text_once(session, admin_user):
    plain = _checkpoint(admin_user)
    already = _checkpoint(admin_user, handle="https://storage.googleapis.com/upload?upload_id=ALREADY")
    empty = _checkpoint(admin_user, handle=None)
    plain_id, already_id, empty_id = plain.id, already.id, empty.id
    session.add_all([plain, already, empty])
    await session.commit()
    # A handle stored before this release: plain text in the column.
    await session.execute(
        text("UPDATE upload_checkpoints SET session_handle=:v WHERE id=:id").bindparams(v=CREDENTIAL, id=plain_id)
    )
    await session.commit()
    already_cipher = await _raw(session, already_id)

    conn = await session.connection()
    await conn.run_sync(_upgrade)
    await conn.run_sync(_upgrade)  # safe to run twice
    await session.commit()

    raw = await _raw(session, plain_id)
    assert looks_like_ciphertext(raw)
    assert encryption_service.decrypt(raw) == CREDENTIAL
    assert await _raw(session, already_id) == already_cipher
    assert await _raw(session, empty_id) is None
    session.expire_all()
    restored = await session.get(UploadCheckpoint, plain_id)
    assert restored is not None and restored.session_handle == CREDENTIAL


def test_key_rotation_re_encrypts_session_handles():
    from app.cli import rotate_encryption_keys

    assert ("upload_checkpoints", "session_handle") in rotate_encryption_keys._ENCRYPTED_COLUMNS
