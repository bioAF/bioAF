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

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.messaging import create_messaging_provider
from app.platform.platform_config_service import PlatformConfigService

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
        # Event subscription is reached through the BAL MessagingProvider (Phase
        # 9E); no Pub/Sub SDK lives in this service. The provider owns the client
        # and the blocking-call offload and hands back normalized messages. Backend
        # resolves from cloud_provider (gcp -> Pub/Sub) via the startup-loaded cache.
        from app.platform.cloud_provider import backend_for

        provider = create_messaging_provider(credentials=credentials, backend=backend_for("messaging"))
        subscription_path = f"projects/{project_id}/subscriptions/{subscription_name}"

        self._running = True
        self._stop_event.clear()
        retry_delay = RETRY_BASE_SECONDS
        logger.info("Pub/Sub listener started on %s", subscription_path)

        try:
            while not self._stop_event.is_set():
                try:
                    messages = await provider.pull(subscription_path, max_messages=10, timeout=30)
                    retry_delay = RETRY_BASE_SECONDS  # reset on success

                    for received in messages:
                        try:
                            msg_data = json.loads(received.data)
                            await self._handle_message(msg_data, session)
                            await provider.acknowledge(subscription_path, [received.ack_id])
                        except Exception:
                            logger.exception(
                                "Failed to process message %s, nacking",
                                received.ack_id,
                            )
                            await provider.nack(subscription_path, [received.ack_id])

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
        default_org_id = await PlatformConfigService.get(session, "default_org_id")
        org_id = int(default_org_id) if default_org_id is not None else 1

        # Fetch stored GCP credentials for all downstream GCS operations
        credentials = await GcsStorageService.get_credentials(session)

        filename = object_name.split("/")[-1]

        # Check if this is a manifest file
        manifest_config = await read_manifest_config(session)
        if is_manifest_filename(filename, manifest_config["manifest_filename"]):
            logger.info("Detected manifest file: %s", filename)
            from app.adapters.registry import get_storage_adapter

            adapter = get_storage_adapter()
            content = await adapter.read_text(adapter.build_uri(bucket, object_name))
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
        return await PlatformConfigService.get_many(session, keys)


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
