"""Idle session monitor background task.

Polls running notebook sessions, checks idle duration via last_activity_at
(or falls back to started_at), sends warnings before shutdown, and terminates
sessions that exceed the idle timeout.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notebook_session import NotebookSession
from app.services.event_bus import event_bus
from app.services.event_types import SESSION_IDLE

logger = logging.getLogger("bioaf.session_monitor")


class SessionMonitorService:
    @staticmethod
    async def poll_notebook_sessions(
        session: AsyncSession,
        idle_timeout_hours: int = 4,
        warning_minutes: int = 15,
    ) -> None:
        """Check running notebook sessions for idle timeout."""
        try:
            result = await session.execute(select(NotebookSession).where(NotebookSession.status == "running"))
            running_sessions = list(result.scalars().all())

            now = datetime.now(timezone.utc)

            for ns in running_sessions:
                # Use last_activity_at if available, fall back to started_at
                last_active = ns.last_activity_at or ns.started_at
                if not last_active:
                    continue

                idle_hours = (now - last_active).total_seconds() / 3600
                warning_threshold_hours = idle_timeout_hours - (warning_minutes / 60)

                if idle_hours >= idle_timeout_hours:
                    await SessionMonitorService._terminate_idle_session(session, ns)
                elif idle_hours >= warning_threshold_hours:
                    await SessionMonitorService._send_idle_warning(session, ns, idle_timeout_hours)

            await session.flush()
            await session.commit()

            if running_sessions:
                logger.info(
                    "Session monitor checked %d running sessions",
                    len(running_sessions),
                )

        except Exception as e:
            logger.error("Session monitor poll failed: %s", e)

    @staticmethod
    async def _terminate_idle_session(session: AsyncSession, ns: NotebookSession) -> None:
        """Terminate an idle session and notify the owner."""
        from app.adapters.registry import get_notebook_adapter

        logger.info("Terminating idle session %d (pod %s)", ns.id, ns.compute_job_ref)

        try:
            notebook_adapter = get_notebook_adapter()
            await notebook_adapter.terminate_session(
                session_id=ns.id,
                pod_name=ns.compute_job_ref or "",
                namespace=ns.provider_namespace or "bioaf-notebooks",
                gcs_home_prefix=ns.gcs_home_prefix or "",
            )
        except Exception as e:
            logger.warning("Failed to terminate idle session %d: %s", ns.id, e)

        now = datetime.now(timezone.utc)
        ns.status = "stopped"
        ns.stopped_at = now

        try:
            import asyncio

            asyncio.create_task(
                event_bus.emit(
                    SESSION_IDLE,
                    {
                        "event_type": SESSION_IDLE,
                        "org_id": ns.organization_id,
                        "user_id": ns.user_id,
                        "target_user_id": ns.user_id,
                        "entity_type": "notebook_session",
                        "entity_id": ns.id,
                        "title": "Notebook session auto-stopped due to inactivity",
                        "message": (f"Your {ns.session_type} session was stopped after exceeding the idle timeout"),
                        "summary": f"Notebook session {ns.id} auto-stopped",
                    },
                )
            )
        except Exception as e:
            logger.warning("Failed to emit idle session event: %s", e)

    @staticmethod
    async def _send_idle_warning(
        session: AsyncSession,
        ns: NotebookSession,
        idle_timeout_hours: int,
    ) -> None:
        """Send a warning notification that the session will be terminated soon."""
        try:
            import asyncio

            asyncio.create_task(
                event_bus.emit(
                    SESSION_IDLE,
                    {
                        "event_type": SESSION_IDLE,
                        "org_id": ns.organization_id,
                        "user_id": ns.user_id,
                        "target_user_id": ns.user_id,
                        "entity_type": "notebook_session",
                        "entity_id": ns.id,
                        "title": "Notebook session will be stopped soon",
                        "message": (
                            f"Your {ns.session_type} session will be stopped "
                            f"due to inactivity. Use the session or save your "
                            f"work to prevent data loss."
                        ),
                        "summary": f"Idle warning for session {ns.id}",
                    },
                )
            )
            logger.info("Sent idle warning for session %d", ns.id)
        except Exception as e:
            logger.warning("Failed to send idle warning for session %d: %s", ns.id, e)
