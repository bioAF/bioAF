"""Auto-ingest neutralization gate during the Naming Profile redesign.

See local/Naming Profiles/spec-auto-ingest-neutralize.md for the contract:
the two ingest entry points short-circuit at the top with a clear error so
production does not silently misbehave while the new naming profile schema
and parser are being rolled out.
"""

import pytest

from app.services.auto_ingest_gate import (
    AUTO_INGEST_DISABLED,
    AutoIngestDisabledError,
)


@pytest.mark.asyncio
async def test_process_ingest_event_raises_disabled_exception(session):
    from app.services.ingest_service import process_ingest_event

    assert AUTO_INGEST_DISABLED is True, (
        "Gate must be active until the auto-ingest rework lands."
    )
    with pytest.raises(AutoIngestDisabledError):
        await process_ingest_event(
            filename="any.fastq",
            source_bucket="bucket",
            source_path="path",
            org_id=1,
            db=session,
        )


@pytest.mark.asyncio
async def test_process_manifest_ingest_raises_disabled_exception(session):
    from app.services.manifest_ingest_service import process_manifest_ingest

    assert AUTO_INGEST_DISABLED is True
    with pytest.raises(AutoIngestDisabledError):
        await process_manifest_ingest(
            manifest_content="",
            manifest_format="md5",
            org_id=1,
            source_bucket="bucket",
            db=session,
        )


@pytest.mark.asyncio
async def test_disabled_error_carries_documented_code_and_message():
    err = AutoIngestDisabledError()
    assert err.code == "auto_ingest_temporarily_disabled"
    assert "Naming Profile" in err.message


@pytest.mark.asyncio
async def test_constant_flip_re_enables_processing(monkeypatch, session):
    """Flipping AUTO_INGEST_DISABLED stops the gate from firing.

    Confirms the gate has a single source of truth, so the follow-up rework
    only needs to flip one constant.
    """
    import app.services.auto_ingest_gate as gate
    from app.services import ingest_service

    monkeypatch.setattr(gate, "AUTO_INGEST_DISABLED", False)

    # Past the gate, downstream code may fail for other reasons (no platform
    # config, no profile, etc.), and that is fine. We only assert the gate
    # itself does not fire.
    try:
        await ingest_service.process_ingest_event(
            filename="x.fastq",
            source_bucket="bucket",
            source_path="path",
            org_id=999999,
            db=session,
        )
    except AutoIngestDisabledError:
        pytest.fail("Gate should not fire when AUTO_INGEST_DISABLED is False")
    except Exception:
        # Downstream failures are out of scope for this test.
        pass


@pytest.mark.asyncio
async def test_post_ingest_simulate_returns_503_with_documented_body(
    client, admin_user, admin_token
):
    resp = await client.post(
        "/api/ingest/simulate",
        json={"filename": "anything.fastq"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "auto_ingest_temporarily_disabled"
    assert "Naming Profile" in body["message"]


@pytest.mark.asyncio
async def test_get_ingest_events_still_works(client, admin_user, admin_token):
    """GET endpoints are unaffected by the gate; only the processing entry points are."""
    resp = await client.get(
        "/api/ingest/events",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
