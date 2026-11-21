"""Upload failures never disclose storage internals (storage lifecycle stage 11B).

An SDK error can carry a full storage URL, a signed URL, or a credential. The
caller gets a plain message and a diagnostic reference; the server log gets the
same reference with the stage and error type, never the raw error text.
"""

from __future__ import annotations

import io
import logging
import re
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import select

LEAKY = (
    "503 POST https://storage.googleapis.com/storage/v1/b/bioaf-ingest-acme-4bd459/o/"
    "uploads%2F7f3a/rewriteTo/b/bioaf-raw-acme-4bd459?X-Goog-Signature=SECRETSIG&access_token=SECRETTOKEN"
)
SENSITIVE = ("googleapis", "bioaf-ingest-acme", "bioaf-raw-acme", "SECRETSIG", "SECRETTOKEN", "rewriteTo")
REFERENCE = re.compile(r"UPL-[0-9A-F]{6}")


def _adapter(**failures):
    adapter = AsyncMock()
    adapter.generate_signed_url.return_value = "https://storage.example/signed"
    adapter.move.side_effect = lambda src, dst: dst
    adapter.resolve_uri.side_effect = lambda store, key: f"gs://bioaf-{store.value}-test/{key}"
    adapter.build_uri = MagicMock(side_effect=lambda bucket, key: f"gs://{bucket}/{key.lstrip('/')}")
    adapter.parse_uri = MagicMock(side_effect=lambda uri: (urlparse(uri).netloc, urlparse(uri).path.lstrip("/")))
    for name, exc in failures.items():
        getattr(adapter, name).side_effect = exc
    return adapter


@pytest_asyncio.fixture
async def storage_config(session, admin_user):
    from app.models.component import PlatformConfig

    session.add(PlatformConfig(key="ingest_bucket_name", value="bioaf-ingest-test-abc123"))
    session.add(PlatformConfig(key="raw_bucket_name", value="bioaf-raw-test-abc123"))
    await session.flush()
    await session.commit()


async def _experiment(session):
    from app.models.experiment import Experiment
    from app.models.user import User

    user = (await session.execute(select(User).limit(1))).scalar_one()
    exp = Experiment(organization_id=user.organization_id, name="Leak", owner_user_id=user.id, status="registered")
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


def _assert_safe(resp, caplog, *, expected_detail: str, status: int = 500, code: str = "upload_failed") -> str:
    assert resp.status_code == status
    body = resp.json()
    text = resp.text
    for marker in SENSITIVE:
        assert marker not in text
    assert body["code"] == code
    reference = body["details"]["reference"]
    assert REFERENCE.fullmatch(reference)
    assert body["detail"] == expected_detail.format(ref=reference)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for marker in SENSITIVE:
        assert marker not in logged
    assert reference in logged
    return reference


TRANSFER_FAILED = "Upload failed because storage did not respond. Nothing was saved. Try again. Reference {ref}."
# A filing failure keeps the received bytes and retries them (storage lifecycle 11F, mockup M12).
FILING_PENDING = (
    "Received, not yet filed. Your file is stored safely but could not be added to Leak yet. "
    "bioAF will try again automatically. Reference {ref}."
)


@pytest.mark.asyncio
async def test_simple_upload_transfer_failure_is_sanitized(client, admin_token, storage_config, caplog):
    caplog.set_level(logging.DEBUG)
    adapter = _adapter(upload_file=RuntimeError(LEAKY))
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/files/upload/simple",
            files={"file": ("a.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    _assert_safe(resp, caplog, expected_detail=TRANSFER_FAILED)


@pytest.mark.asyncio
async def test_simple_upload_move_failure_is_sanitized(client, admin_token, storage_config, session, caplog):
    caplog.set_level(logging.DEBUG)
    exp = await _experiment(session)
    adapter = _adapter(move=RuntimeError(LEAKY))
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            f"/api/files/upload/simple?experiment_id={exp.id}",
            files={"file": ("b.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    _assert_safe(resp, caplog, expected_detail=FILING_PENDING, status=202, code="upload_received_not_filed")


@pytest.mark.asyncio
async def test_signed_upload_completion_failure_is_sanitized(client, admin_token, storage_config, session, caplog):
    caplog.set_level(logging.DEBUG)
    exp = await _experiment(session)
    adapter = _adapter(move=RuntimeError(LEAKY))
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        init = await client.post(
            "/api/files/upload/initiate",
            json={"filename": "c.fastq.gz", "expected_size_bytes": 4, "experiment_id": exp.id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert init.status_code == 200
        resp = await client.post(
            "/api/files/upload/complete",
            json={"upload_id": init.json()["upload_id"], "actual_md5": "0" * 32},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    _assert_safe(resp, caplog, expected_detail=FILING_PENDING, status=202, code="upload_received_not_filed")


@pytest.mark.asyncio
async def test_known_validation_failures_keep_their_responses(client, admin_token, storage_config):
    """A refused association target is not an upload failure and keeps its 404."""
    adapter = _adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        resp = await client.post(
            "/api/files/upload/simple?experiment_id=999999",
            files={"file": ("d.fastq.gz", io.BytesIO(b"data"), "application/gzip")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code in (400, 404)
    assert "code" not in resp.json() or resp.json()["code"] != "upload_failed"
    adapter.upload_file.assert_not_called()
