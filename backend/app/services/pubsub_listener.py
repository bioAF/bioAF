"""Pub/Sub listener for auto-ingest from real GCS bucket notifications.

Pulls OBJECT_FINALIZE messages from the ingest bucket's Pub/Sub
subscription, extracts GCS object metadata, and feeds each event
into the existing ingest pipeline handler.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.pubsub_listener")

# Retry backoff base (seconds) - overridable in tests
RETRY_BASE_SECONDS: float = 10.0
RETRY_MAX_SECONDS: float = 120.0


def _base64_md5_to_hex(b64_md5: str) -> str:
    """Convert a base64-encoded MD5 hash (from GCS) to lowercase hex."""
    try:
        return base64.b64decode(b64_md5).hex()
    except (binascii.Error, ValueError):
        # Not valid base64, return as-is (may already be hex)
        return b64_md5


class PubSubListener:
    """Background Pub/Sub pull listener for ingest bucket events."""

    def __init__(self) -> None:
        self._running = False
        self._stop_event = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._running

    def stop(self) -> None:
        """Signal the listener to stop pulling."""
        self._stop_event.set()

    async def start(self, session: AsyncSession) -> None:
        """Start pulling messages from the Pub/Sub subscription.

        Checks platform_config for auto_ingest_enabled and
        pubsub_subscription_name before entering the pull loop.
        """
        config = await self._read_config(session)

        if config.get("auto_ingest_enabled", "false") != "true":
            logger.info("Auto-ingest is disabled, skipping Pub/Sub listener")
            return

        subscription_name = config.get("pubsub_subscription_name", "null")
        if not subscription_name or subscription_name == "null":
            logger.warning("No Pub/Sub subscription configured, skipping listener")
            return

        project_id = config.get("gcp_project_id", "")

        # Use stored GCP credentials if available (same as GCS storage)
        from app.services.gcs_storage import GcsStorageService

        credentials = await GcsStorageService.get_credentials(session)
        subscriber = self._create_subscriber(credentials=credentials)
        subscription_path = f"projects/{project_id}/subscriptions/{subscription_name}"

        self._running = True
        self._stop_event.clear()
        retry_delay = RETRY_BASE_SECONDS
        logger.info("Pub/Sub listener started on %s", subscription_path)

        try:
            while not self._stop_event.is_set():
                try:
                    response = await asyncio.to_thread(
                        subscriber.pull,
                        subscription=subscription_path,
                        max_messages=10,
                        timeout=30,
                    )
                    retry_delay = RETRY_BASE_SECONDS  # reset on success

                    for received in response.received_messages:
                        try:
                            msg_data = json.loads(received.message.data)
                            await self._handle_message(msg_data, session)
                            await asyncio.to_thread(
                                subscriber.acknowledge,
                                subscription=subscription_path,
                                ack_ids=[received.ack_id],
                            )
                        except Exception:
                            logger.exception(
                                "Failed to process message %s, nacking",
                                received.ack_id,
                            )
                            await asyncio.to_thread(
                                subscriber.modify_ack_deadline,
                                subscription=subscription_path,
                                ack_ids=[received.ack_id],
                                ack_deadline_seconds=0,
                            )

                except (ConnectionError, OSError) as exc:
                    logger.error("Pub/Sub connection error: %s, retrying in %.0fs", exc, retry_delay)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, RETRY_MAX_SECONDS)

        finally:
            self._running = False
            logger.info("Pub/Sub listener stopped")

    async def _handle_message(self, msg_data: dict, session: AsyncSession) -> None:
        """Process a single Pub/Sub message by calling the ingest pipeline.

        If the file is a manifest (matches the configured manifest filename),
        route it to the manifest ingest service. Otherwise, route to the
        standard file ingest pipeline.
        """
        from app.services.gcs_storage import GcsStorageService
        from app.services.ingest_service import process_ingest_event
        from app.services.manifest_ingest_service import (
            is_manifest_filename,
            process_manifest_ingest,
            read_manifest_config,
        )

        bucket = msg_data["bucket"]
        object_name = msg_data["name"]
        size = int(msg_data.get("size", 0))

        # GCS Pub/Sub sends MD5 as base64; convert to hex for manifest comparison
        raw_md5 = msg_data.get("md5Hash")
        md5_hash = _base64_md5_to_hex(raw_md5) if raw_md5 else None

        # Read org_id from platform_config (single-tenant assumption)
        row = await session.execute(text("SELECT value FROM platform_config WHERE key = 'default_org_id'"))
        org_id_row = row.fetchone()
        org_id = int(org_id_row[0]) if org_id_row else 1

        # Fetch stored GCP credentials for all downstream GCS operations
        credentials = await GcsStorageService.get_credentials(session)

        filename = object_name.split("/")[-1]

        # Check if this is a manifest file
        manifest_config = await read_manifest_config(session)
        if is_manifest_filename(filename, manifest_config["manifest_filename"]):
            logger.info("Detected manifest file: %s", filename)
            content = await GcsStorageService.read_object_text(bucket, object_name, credentials=credentials)
            await process_manifest_ingest(
                manifest_content=content,
                manifest_format=manifest_config["manifest_format"],
                org_id=org_id,
                source_bucket=bucket,
                db=session,
            )
            await session.commit()
            return

        await process_ingest_event(
            filename=filename,
            source_bucket=bucket,
            source_path=object_name,
            org_id=org_id,
            db=session,
            user_id=None,
            file_size_bytes=size,
            content_md5=md5_hash,
            ingest_source="auto_ingest",
            credentials=credentials,
        )
        await session.commit()

    def _create_subscriber(self, credentials=None):  # type: ignore[no-untyped-def]
        """Create a Pub/Sub SubscriberClient. Overridden in tests."""
        from google.cloud import pubsub_v1

        if credentials:
            return pubsub_v1.SubscriberClient(credentials=credentials)
        return pubsub_v1.SubscriberClient()

    @staticmethod
    async def _read_config(session: AsyncSession) -> dict[str, str]:
        """Read auto-ingest config keys from platform_config."""
        keys = [
            "auto_ingest_enabled",
            "pubsub_subscription_name",
            "pubsub_topic_name",
            "ingest_cleanup_policy",
            "gcp_project_id",
        ]
        rows = (
            await session.execute(
                text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys)
            )
        ).fetchall()
        return {r[0]: r[1] for r in rows}


# Module-level instance for the background task
_listener: PubSubListener | None = None


def get_listener() -> PubSubListener | None:
    """Return the current listener instance, if any."""
    return _listener


async def start_pubsub_listener_task(session: AsyncSession) -> PubSubListener:
    """Create and start the Pub/Sub listener. Used by the lifespan handler."""
    global _listener
    _listener = PubSubListener()
    await _listener.start(session)
    return _listener


async def restart_listener_if_needed() -> None:
    """Start or restart the listener after config changes.

    Called from the auto-ingest settings endpoint when the user enables
    auto-ingest. If the listener is already running, this is a no-op.
    """
    global _listener
    if _listener and _listener.running:
        return

    from app.database import async_session_factory

    _listener = PubSubListener()

    async def _run() -> None:
        async with async_session_factory() as session:
            assert _listener is not None
            await _listener.start(session)

    asyncio.create_task(_run())
