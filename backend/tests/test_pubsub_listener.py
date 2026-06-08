"""Tests for the Pub/Sub listener service (Phase 21; messaging seam Phase 9E).

Tests 1-7: Listener lifecycle, message processing, ack/nack, retry. The listener
pulls/acks through the BAL MessagingProvider (Phase 9E), so these mock the
provider boundary (create_messaging_provider) rather than a raw Pub/Sub client.
"""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.adapters.messaging import ReceivedMessage


async def _seed_config(session, overrides=None):
    """Insert platform_config keys needed for tests."""
    defaults = {
        "auto_ingest_enabled": "false",
        "pubsub_subscription_name": "null",
        "pubsub_topic_name": "null",
        "ingest_cleanup_policy": "delete_after_copy",
        "storage_deployed": "true",
        "ingest_bucket_name": "bioaf-ingest-testorg",
        "raw_bucket_name": "bioaf-raw-testorg",
    }
    if overrides:
        defaults.update(overrides)
    for key, value in defaults.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


def _make_received_message(
    bucket="bioaf-ingest-testorg",
    name="PROJ1_EXP1_S001.fastq.gz",
    size="1048576",
    md5_hash=None,
    ack_id="ack-id-1",
):
    """Build a normalized ReceivedMessage as the provider would return."""
    if md5_hash is None:
        md5_hash = base64.b64encode(b"\x00" * 16).decode()
    data = json.dumps(
        {
            "bucket": bucket,
            "name": name,
            "size": size,
            "md5Hash": md5_hash,
            "timeCreated": "2026-03-12T10:00:00Z",
            "contentType": "application/gzip",
            "metageneration": "1",
        }
    ).encode()
    return ReceivedMessage(ack_id=ack_id, data=data)


def _mock_provider():
    provider = MagicMock()
    provider.pull = AsyncMock(return_value=[])
    provider.acknowledge = AsyncMock()
    provider.nack = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_listener_starts_when_enabled(session):
    """Listener enters pull loop when auto_ingest_enabled=true and subscription exists."""
    await _seed_config(
        session,
        {"auto_ingest_enabled": "true", "pubsub_subscription_name": "bioaf-ingest-worker-testorg"},
    )

    from app.services.pubsub_listener import PubSubListener

    listener = PubSubListener()

    # Stop after the first pull. The real pull blocks up to 30s waiting for
    # messages; an instant-returning mock would otherwise spin the loop with no
    # delay (and AsyncMock records every call -> unbounded memory).
    async def pull_side_effect(*args, **kwargs):
        listener.stop()
        return []

    provider = _mock_provider()
    provider.pull = AsyncMock(side_effect=pull_side_effect)

    with patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider):
        await asyncio.wait_for(listener.start(session), timeout=2.0)

    assert provider.pull.called


@pytest.mark.asyncio
async def test_listener_skips_when_disabled(session):
    """Listener returns immediately when auto_ingest_enabled=false."""
    await _seed_config(session, {"auto_ingest_enabled": "false"})

    from app.services.pubsub_listener import PubSubListener

    listener = PubSubListener()
    await listener.start(session)
    assert not listener.running


@pytest.mark.asyncio
async def test_listener_skips_when_no_subscription(session):
    """Listener returns when pubsub_subscription_name is null."""
    await _seed_config(
        session,
        {"auto_ingest_enabled": "true", "pubsub_subscription_name": "null"},
    )

    from app.services.pubsub_listener import PubSubListener

    listener = PubSubListener()
    await listener.start(session)
    assert not listener.running


@pytest.mark.asyncio
async def test_listener_processes_message(session):
    """Listener extracts GCS object metadata and calls ingest pipeline."""
    await _seed_config(
        session,
        {"auto_ingest_enabled": "true", "pubsub_subscription_name": "bioaf-ingest-worker-testorg"},
    )

    from app.services.pubsub_listener import PubSubListener

    listener = PubSubListener()
    msg = _make_received_message()
    call_count = 0

    async def pull_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [msg]
        listener.stop()
        return []

    provider = _mock_provider()
    provider.pull = AsyncMock(side_effect=pull_side_effect)
    mock_handler = AsyncMock()

    with (
        patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider),
        patch.object(listener, "_handle_message", mock_handler),
    ):
        await listener.start(session)

    mock_handler.assert_called_once()
    assert mock_handler.call_args[0][0]["name"] == "PROJ1_EXP1_S001.fastq.gz"


@pytest.mark.asyncio
async def test_listener_acks_on_success(session):
    """Listener acknowledges message after successful processing."""
    await _seed_config(
        session,
        {"auto_ingest_enabled": "true", "pubsub_subscription_name": "bioaf-ingest-worker-testorg"},
    )

    from app.services.pubsub_listener import PubSubListener

    listener = PubSubListener()
    msg = _make_received_message()
    call_count = 0

    async def pull_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [msg]
        listener.stop()
        return []

    provider = _mock_provider()
    provider.pull = AsyncMock(side_effect=pull_side_effect)

    with (
        patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider),
        patch.object(listener, "_handle_message", AsyncMock()),
    ):
        await listener.start(session)

    provider.acknowledge.assert_awaited_once()
    assert "ack-id-1" in provider.acknowledge.call_args.args[1]
    provider.nack.assert_not_awaited()


@pytest.mark.asyncio
async def test_listener_nacks_on_failure(session):
    """Listener nacks message when processing raises an exception."""
    await _seed_config(
        session,
        {"auto_ingest_enabled": "true", "pubsub_subscription_name": "bioaf-ingest-worker-testorg"},
    )

    from app.services.pubsub_listener import PubSubListener

    listener = PubSubListener()
    msg = _make_received_message()
    call_count = 0

    async def pull_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [msg]
        listener.stop()
        return []

    provider = _mock_provider()
    provider.pull = AsyncMock(side_effect=pull_side_effect)

    with (
        patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider),
        patch.object(listener, "_handle_message", AsyncMock(side_effect=Exception("processing failed"))),
    ):
        await listener.start(session)

    provider.nack.assert_awaited_once()
    assert "ack-id-1" in provider.nack.call_args.args[1]


@pytest.mark.asyncio
async def test_listener_retries_on_connection_error(session):
    """Listener retries with backoff when the provider raises a connection error."""
    await _seed_config(
        session,
        {"auto_ingest_enabled": "true", "pubsub_subscription_name": "bioaf-ingest-worker-testorg"},
    )

    from app.services.pubsub_listener import PubSubListener

    listener = PubSubListener()
    call_count = 0

    async def pull_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ConnectionError("Pub/Sub unavailable")
        listener.stop()
        return []

    provider = _mock_provider()
    provider.pull = AsyncMock(side_effect=pull_side_effect)

    with (
        patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider),
        patch("app.services.pubsub_listener.RETRY_BASE_SECONDS", 0.01),
    ):
        await listener.start(session)

    assert provider.pull.call_count >= 3
