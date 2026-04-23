from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from app.models.file import File

from app.models.notebook_session import NotebookSession
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import SESSION_IDLE
from app.services.quota_service import QuotaService
from app.adapters.registry import get_notebook_adapter

logger = logging.getLogger("bioaf.notebooks")

RESOURCE_PROFILES = {
    "small": (2, 4),
    "medium": (4, 8),
    "large": (8, 16),
}


class NotebookService:
    @staticmethod
    def get_resource_profile(profile_name: str) -> tuple[int, int]:
        if profile_name not in RESOURCE_PROFILES:
            raise ValueError(f"Invalid resource profile: {profile_name}")
        return RESOURCE_PROFILES[profile_name]

    @staticmethod
    async def launch_session(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        session_type: str,
        resource_profile: str,
        experiment_id: int | None = None,
        project_id: int | None = None,
        image: str | None = None,
        input_file_ids: list[int] | None = None,
        environment_version_id: int | None = None,
    ) -> NotebookSession:
        # Check quota
        allowed, message = await QuotaService.check_quota(session, user_id, estimated_hours=1.0)
        if not allowed:
            raise ValueError(f"Quota exceeded: {message}")

        cpu_cores, memory_gb = NotebookService.get_resource_profile(resource_profile)

        notebook_session = NotebookSession(
            user_id=user_id,
            organization_id=org_id,
            session_type=session_type,
            experiment_id=experiment_id,
            project_id=project_id,
            environment_version_id=environment_version_id,
            resource_profile=resource_profile,
            cpu_cores=cpu_cores,
            memory_gb=memory_gb,
            status="pending",
            started_at=datetime.now(timezone.utc),
        )
        session.add(notebook_session)
        await session.flush()

        # Launch via the notebook adapter (BAL)
        try:
            notebook_adapter = get_notebook_adapter()
            spec: dict = {
                "session_type": session_type,
                "resource_profile": resource_profile,
                "cpu_cores": cpu_cores,
                "memory_gb": memory_gb,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "user_id": user_id,
                "session_id": notebook_session.id,
            }
            if image:
                spec["image"] = image

            # Pass working bucket name so the adapter uses the correct GCS path
            from sqlalchemy import text as sa_text

            config_rows = await session.execute(
                sa_text(
                    "SELECT key, value FROM platform_config "
                    "WHERE key IN ('working_bucket_name', 'notebook_runner_sa_email')"
                )
            )
            config_map = {row[0]: row[1] for row in config_rows.all()}

            bucket_name = (config_map.get("working_bucket_name") or "").strip()
            if bucket_name and bucket_name != "null":
                spec["working_bucket"] = bucket_name

            sa_email = (config_map.get("notebook_runner_sa_email") or "").strip()
            if sa_email and sa_email != "null":
                spec["notebook_runner_sa_email"] = sa_email

            # RStudio requires session credentials for PAM auth
            if session_type == "rstudio":
                from app.services.session_credential_service import SessionCredentialService

                cred = await SessionCredentialService.get_by_user_id(session, user_id)
                if not cred:
                    raise ValueError(
                        "Session credentials are required for RStudio sessions. "
                        "Please set up your session credentials in your profile settings."
                    )
                spec["session_credentials"] = {
                    "username": cred.username,
                    "password_hash": cred.password_hash,
                }

                # Include SSH key if the user has one configured
                if cred.ssh_private_key:
                    spec["ssh_private_key"] = cred.ssh_private_key

            # Validate and build input file list with hierarchical paths
            input_files_spec: list[dict] = []
            if input_file_ids:
                from app.models.file import File

                file_results = await session.execute(select(File).where(File.id.in_(input_file_ids)))
                found_files = {f.id: f for f in file_results.scalars().all()}

                # Resolve names for hierarchy: project, experiment, sample, pipeline
                name_cache = await _resolve_input_file_context(session, found_files)

                for fid in input_file_ids:
                    f = found_files.get(fid)
                    if not f or f.organization_id != org_id:
                        raise ValueError(f"File {fid} not found or not accessible")
                    rel_path = _build_relative_path(f, name_cache)
                    input_files_spec.append(
                        {
                            "file_id": f.id,
                            "gcs_uri": f.gcs_uri,
                            "relative_path": rel_path,
                        }
                    )

                spec["input_files"] = input_files_spec

            result = await notebook_adapter.launch_session(spec)

            notebook_session.slurm_job_id = str(result.get("session_id", ""))
            notebook_session.proxy_url = result.get("url")
            notebook_session.k8s_pod_name = result.get("pod_name")
            notebook_session.k8s_namespace = result.get("namespace")
            notebook_session.access_url = result.get("access_url")
            notebook_session.gcs_home_prefix = result.get("gcs_home_prefix")
            adapter_status = result.get("status", "starting")
            if adapter_status == "error":
                notebook_session.status = "failed"
            elif adapter_status == "running":
                notebook_session.status = "running"
            else:
                notebook_session.status = "starting"

            # Create NotebookSessionFile input rows
            if input_file_ids:
                from app.models.notebook_session_file import NotebookSessionFile

                for fid in input_file_ids:
                    session.add(
                        NotebookSessionFile(
                            session_id=notebook_session.id,
                            file_id=fid,
                            access_type="input",
                        )
                    )

        except ValueError:
            notebook_session.status = "failed"
            raise
        except Exception as e:
            notebook_session.status = "failed"
            logger.error("Failed to launch notebook session %d: %s", notebook_session.id, e)
            if "not found or not accessible" in str(e):
                raise

        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="notebook_session",
            entity_id=notebook_session.id,
            action="launch",
            details={
                "session_type": session_type,
                "resource_profile": resource_profile,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "status": notebook_session.status,
            },
        )
        return notebook_session

    @staticmethod
    async def stop_session(session: AsyncSession, session_id: int, user_id: int) -> NotebookSession:
        notebook_session = await NotebookService.get_session(session, session_id)
        if not notebook_session:
            raise ValueError("Session not found")

        old_status = notebook_session.status

        # Look up working bucket for output sync
        from sqlalchemy import text as sa_text

        bucket_row = await session.execute(
            sa_text("SELECT value FROM platform_config WHERE key = 'working_bucket_name'")
        )
        working_bucket = ""
        row = bucket_row.first()
        if row:
            val = (row[0] or "").strip()
            if val and val != "null":
                working_bucket = val

        # Terminate via the notebook adapter
        terminate_result: dict = {}
        if notebook_session.slurm_job_id or notebook_session.k8s_pod_name:
            try:
                notebook_adapter = get_notebook_adapter()
                terminate_result = await notebook_adapter.terminate_session(
                    notebook_session.slurm_job_id or "",
                    pod_name=notebook_session.k8s_pod_name or "",
                    namespace=notebook_session.k8s_namespace or "bioaf-notebooks",
                    gcs_home_prefix=notebook_session.gcs_home_prefix or "",
                    working_bucket=working_bucket,
                    session_type=notebook_session.session_type,
                )
            except Exception as e:
                logger.warning("Failed to terminate session %s: %s", notebook_session.slurm_job_id, e)

        # Register output files (ADR-040)
        output_files = terminate_result.get("output_files", [])
        if output_files:
            try:
                from app.services.session_output_service import SessionOutputService

                await SessionOutputService.register_outputs(
                    session,
                    session_id=notebook_session.id,
                    organization_id=notebook_session.organization_id,
                    project_id=notebook_session.project_id,
                    experiment_id=notebook_session.experiment_id,
                    user_id=notebook_session.user_id,
                    gcs_files=output_files,
                )
            except Exception as e:
                logger.warning("Output registration failed for session %d: %s", session_id, e)

        gcs_output_prefix = terminate_result.get("gcs_output_prefix", "")
        if gcs_output_prefix:
            notebook_session.gcs_output_prefix = gcs_output_prefix

        # Move outputs from working to results bucket (ADR-040: two-phase)
        if working_bucket and output_files:
            try:
                results_row = await session.execute(
                    sa_text("SELECT value FROM platform_config WHERE key = 'results_bucket_name'")
                )
                r_row = results_row.first()
                results_bucket = (r_row[0] or "").strip() if r_row else ""
                if results_bucket and results_bucket != "null":
                    from app.services.session_output_service import SessionOutputService

                    final_prefix = await SessionOutputService.move_outputs_to_results_bucket(
                        session,
                        session_id=notebook_session.id,
                        working_bucket=working_bucket,
                        results_bucket=results_bucket,
                    )
                    notebook_session.gcs_output_prefix = final_prefix
            except Exception as e:
                logger.warning("Failed to move outputs to results bucket for session %d: %s", session_id, e)

        notebook_session.status = "stopped"
        notebook_session.stopped_at = datetime.now(timezone.utc)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="notebook_session",
            entity_id=notebook_session.id,
            action="stop",
            details={"session_type": notebook_session.session_type},
            previous_value={"status": old_status},
        )
        return notebook_session

    @staticmethod
    async def list_sessions(
        session: AsyncSession,
        org_id: int,
        user_id: int | None = None,
        session_type: str | None = None,
        status: str | None = None,
    ) -> tuple[list[NotebookSession], int]:
        query = (
            select(NotebookSession)
            .options(selectinload(NotebookSession.user), selectinload(NotebookSession.experiment))
            .where(
                NotebookSession.organization_id == org_id,
                NotebookSession.session_type != "ssh",
            )
        )
        count_query = select(func.count(NotebookSession.id)).where(
            NotebookSession.organization_id == org_id,
            NotebookSession.session_type != "ssh",
        )

        if user_id:
            query = query.where(NotebookSession.user_id == user_id)
            count_query = count_query.where(NotebookSession.user_id == user_id)
        if session_type:
            query = query.where(NotebookSession.session_type == session_type)
            count_query = count_query.where(NotebookSession.session_type == session_type)
        if status:
            query = query.where(NotebookSession.status == status)
            count_query = count_query.where(NotebookSession.status == status)

        query = query.order_by(NotebookSession.created_at.desc())

        result = await session.execute(query)
        sessions = list(result.scalars().all())

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return sessions, total

    @staticmethod
    async def get_session(session: AsyncSession, session_id: int) -> NotebookSession | None:
        result = await session.execute(
            select(NotebookSession)
            .options(selectinload(NotebookSession.user), selectinload(NotebookSession.experiment))
            .where(NotebookSession.id == session_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def check_idle_sessions(session: AsyncSession, idle_timeout_hours: int = 4) -> None:
        """Background task: check for idle sessions and auto-stop them."""
        try:
            result = await session.execute(
                select(NotebookSession).where(NotebookSession.status.in_(["running", "idle"]))
            )
            active_sessions = list(result.scalars().all())

            now = datetime.now(timezone.utc)

            for ns in active_sessions:
                if ns.status == "idle" and ns.idle_since:
                    idle_duration = (now - ns.idle_since).total_seconds() / 3600
                    if idle_duration >= idle_timeout_hours:
                        logger.info("Auto-stopping idle session %d (idle %.1fh)", ns.id, idle_duration)
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
                                    "title": f"Idle session auto-stopped after {idle_duration:.1f}h",
                                    "message": f"Your {ns.session_type} session was stopped due to inactivity",
                                    "summary": f"Notebook session {ns.id} auto-stopped (idle {idle_duration:.1f}h)",
                                },
                            )
                        )
                        ns.status = "stopped"
                        ns.stopped_at = now
                        if ns.slurm_job_id or ns.k8s_pod_name:
                            try:
                                notebook_adapter = get_notebook_adapter()
                                await notebook_adapter.terminate_session(
                                    ns.slurm_job_id or "",
                                    pod_name=ns.k8s_pod_name or "",
                                    namespace=ns.k8s_namespace or "bioaf-notebooks",
                                    gcs_home_prefix=ns.gcs_home_prefix or "",
                                )
                            except Exception as e:
                                logger.warning("Failed to terminate idle session: %s", e)

            await session.flush()
            await session.commit()
            logger.info("Idle session check completed, checked %d sessions", len(active_sessions))

        except Exception as e:
            logger.error("Idle session check failed: %s", e)


def _slugify(name: str) -> str:
    """Convert a display name to a filesystem-safe slug."""
    import re

    slug = re.sub(r"[^\w\s-]", "", name.lower())
    return re.sub(r"[\s_]+", "-", slug).strip("-") or "unknown"


async def _resolve_input_file_context(
    session: "AsyncSession",
    files: dict[int, "File"],
) -> dict:
    """Resolve project, experiment, sample, and pipeline names for input files."""
    from sqlalchemy import text as sa_text

    project_ids = {f.project_id for f in files.values() if f.project_id}
    experiment_ids = {f.experiment_id for f in files.values() if f.experiment_id}
    pipeline_run_ids = {f.source_pipeline_run_id for f in files.values() if f.source_pipeline_run_id}
    file_ids = list(files.keys())

    cache: dict = {"projects": {}, "experiments": {}, "pipelines": {}, "file_samples": {}}

    if project_ids:
        rows = await session.execute(
            sa_text("SELECT id, name FROM projects WHERE id = ANY(:ids)"),
            {"ids": list(project_ids)},
        )
        cache["projects"] = {r[0]: r[1] for r in rows.fetchall()}

    if experiment_ids:
        rows = await session.execute(
            sa_text("SELECT id, name FROM experiments WHERE id = ANY(:ids)"),
            {"ids": list(experiment_ids)},
        )
        cache["experiments"] = {r[0]: r[1] for r in rows.fetchall()}

    if pipeline_run_ids:
        rows = await session.execute(
            sa_text("SELECT id, pipeline_name FROM pipeline_runs WHERE id = ANY(:ids)"),
            {"ids": list(pipeline_run_ids)},
        )
        cache["pipelines"] = {r[0]: r[1] for r in rows.fetchall()}

    # Resolve sample identifiers for files via sample_files junction
    if file_ids:
        rows = await session.execute(
            sa_text(
                "SELECT sf.file_id, COALESCE(s.sample_id_external, CAST(s.id AS TEXT)) "
                "FROM sample_files sf "
                "JOIN samples s ON s.id = sf.sample_id "
                "WHERE sf.file_id = ANY(:ids)"
            ),
            {"ids": file_ids},
        )
        for r in rows.fetchall():
            cache["file_samples"][r[0]] = r[1]

    return cache


def _build_relative_path(f: "File", cache: dict) -> str:
    """Build a hierarchical relative path for a file based on its associations.

    Structure: {project}/{experiment}/{sample}/{tool}/filename
    Falls back gracefully when associations are missing.
    """
    parts: list[str] = []

    project_name = cache["projects"].get(f.project_id) if f.project_id else None
    if project_name:
        parts.append(_slugify(project_name))

    experiment_name = cache["experiments"].get(f.experiment_id) if f.experiment_id else None
    if experiment_name:
        parts.append(_slugify(experiment_name))

    sample_name = cache["file_samples"].get(f.id)
    if sample_name:
        parts.append(_slugify(sample_name))

    pipeline_name = cache["pipelines"].get(f.source_pipeline_run_id) if f.source_pipeline_run_id else None
    if pipeline_name:
        parts.append(_slugify(pipeline_name))
    elif f.source_type == "upload":
        parts.append("uploads")

    parts.append(f.filename)
    return "/".join(parts)
