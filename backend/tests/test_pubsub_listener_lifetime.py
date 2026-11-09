"""Pub/Sub listener lifetime: one listener per manager, transactions recover after a failed message.

- A message whose database write fails is nacked after its transaction is rolled back, so the next
  valid message commits (the failure used to poison the shared session for every later message).
- Two starts before the first listener is running start one listener, not two pull loops.
- Disabling stops and awaits the listener, including one still starting.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text

from app.models.component import PlatformConfig
from tests.test_pubsub_listener import _make_received_message, _seed_config

pytestmark = pytest.mark.asyncio

SUBSCRIPTION = "bioaf-ingest-worker-testorg"


def _provider(batches, stop_after):
    """A provider answering ``batches`` in order, then calling ``stop_after()`` and blocking."""
    provider = MagicMock()
    provider.acknowledge = AsyncMock()
    provider.nack = AsyncMock()
    queue = list(batches)

    async def pull(*args, **kwargs):
        if queue:
            return queue.pop(0)
        stop_after()
        await asyncio.sleep(3600)
        return []

    provider.pull = AsyncMock(side_effect=pull)
    return provider


async def _write_marker(msg_data, session):
    """The handler's database write: a duplicate key fails at flush, a new key commits."""
    session.add(PlatformConfig(key=msg_data["name"], value="written"))
    await session.flush()
    await session.commit()


async def _settled(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def _marker_exists(session, key):
    await session.rollback()
    return (await session.execute(select(PlatformConfig).where(PlatformConfig.key == key))).scalar() is not None


async def test_a_failed_write_is_rolled_back_so_the_next_message_commits(session):
    await _seed_config(session, {"auto_ingest_enabled": "true", "pubsub_subscription_name": SUBSCRIPTION})
    from app.services.pubsub_listener import PubSubListener

    listener = PubSubListener()
    duplicate = _make_received_message(name="ingest_bucket_name", ack_id="dup")
    valid = _make_received_message(name="fresh-marker", ack_id="ok")
    provider = _provider([[duplicate, valid]], listener.stop)

    with (
        patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider),
        patch.object(listener, "_handle_message", side_effect=_write_marker),
    ):
        runner = asyncio.create_task(listener.start(session))
        await _settled(lambda: provider.acknowledge.await_count + provider.nack.await_count == 2)
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)

    assert [c.args[1] for c in provider.nack.await_args_list] == [["dup"]]
    assert [c.args[1] for c in provider.acknowledge.await_args_list] == [["ok"]]
    assert await _marker_exists(session, "fresh-marker")


async def test_the_managed_listener_uses_a_session_per_message(session):
    await _seed_config(session, {"auto_ingest_enabled": "true", "pubsub_subscription_name": SUBSCRIPTION})
    from app.services import pubsub_listener

    duplicate = _make_received_message(name="ingest_bucket_name", ack_id="dup")
    valid = _make_received_message(name="fresh-marker", ack_id="ok")
    provider = _provider([[duplicate, valid]], lambda: None)

    with (
        patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider),
        patch.object(pubsub_listener.PubSubListener, "_handle_message", side_effect=_write_marker, autospec=False),
    ):
        await pubsub_listener.start_listener()
        await _settled(lambda: provider.acknowledge.await_count + provider.nack.await_count == 2)
        await pubsub_listener.stop_listener()

    assert [c.args[1] for c in provider.nack.await_args_list] == [["dup"]]
    assert [c.args[1] for c in provider.acknowledge.await_args_list] == [["ok"]]
    assert await _marker_exists(session, "fresh-marker")


async def test_two_starts_before_running_start_one_listener(session):
    await _seed_config(session, {"auto_ingest_enabled": "true", "pubsub_subscription_name": SUBSCRIPTION})
    from app.services import pubsub_listener

    provider = _provider([], lambda: None)
    with patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider) as create:
        await pubsub_listener.restart_listener_if_needed()
        await pubsub_listener.restart_listener_if_needed()
        await _settled(lambda: provider.pull.await_count >= 1)
        await asyncio.sleep(0.05)
        try:
            assert create.call_count == 1
            assert provider.pull.await_count == 1
            assert pubsub_listener.get_listener().running
        finally:
            await pubsub_listener.stop_listener()

    assert not pubsub_listener.get_listener().running


async def test_stopping_while_the_listener_is_starting_leaves_nothing_running(session):
    await _seed_config(session, {"auto_ingest_enabled": "true", "pubsub_subscription_name": SUBSCRIPTION})
    from app.services import pubsub_listener

    provider = _provider([], lambda: None)
    with patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider):
        await pubsub_listener.start_listener()
        await pubsub_listener.stop_listener()
        await asyncio.sleep(0.05)

    assert provider.pull.await_count == 0
    listener = pubsub_listener.get_listener()
    assert listener is None or not listener.running


async def test_disabling_auto_ingest_stops_the_running_listener(client, admin_token, session):
    await _seed_config(session, {"auto_ingest_enabled": "false", "pubsub_subscription_name": SUBSCRIPTION})
    from app.services import pubsub_listener

    provider = _provider([], lambda: None)
    headers = {"Authorization": f"Bearer {admin_token}"}
    with patch("app.services.pubsub_listener.create_messaging_provider", return_value=provider):
        enabled = await client.post(
            "/api/v1/settings/auto-ingest",
            json={"enabled": True, "cleanup_policy": "delete_after_copy"},
            headers=headers,
        )
        assert enabled.status_code == 200
        await _settled(lambda: provider.pull.await_count >= 1)
        assert pubsub_listener.get_listener().running

        disabled = await client.post(
            "/api/v1/settings/auto-ingest",
            json={"enabled": False, "cleanup_policy": "delete_after_copy"},
            headers=headers,
        )
        assert disabled.status_code == 200

    assert not pubsub_listener.get_listener().running
    await session.execute(text("SELECT 1"))
