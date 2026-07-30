"""G1: headless notebook execution primitive (lit_validation Level-3, C2).

Runs a curated (platform-owned) template notebook to completion, headless: parameters are
injected into the template's `parameters`-tagged cell, pipeline-output File rows are mounted
as inputs, and the run is tracked on a ComputeSession(session_type="headless"). The pod
executes the notebook and exits; poll_execution advances pending/running -> completed/failed
and registers outputs on success, reusing the interactive notebook output pipeline (ADR-040).

Deliberately general (not lit_validation-internal): any analysis type is a new curated
template. Trust boundary: only builtin templates run headless, since the pod executes as the
notebook_runner service account.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.models import ServiceState
from app.adapters.registry import get_notebook_adapter
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.notebook_session import ComputeSession
from app.platform.platform_config_service import PlatformConfigService
from app.services.audit_service import log_action
from app.services.notebook_service import (
    NotebookService,
    _build_relative_path,
    _resolve_input_file_context,
)
from app.services.quota_service import QuotaService
from app.services.template_notebook_service import TemplateNotebookService

logger = logging.getLogger("bioaf.notebook_execution")

HEADLESS_SESSION_TYPE = "headless"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotebookExecutionService:
    """Launch and finalize a headless run of a curated template notebook."""

    @staticmethod
    async def execute_template(
        session: AsyncSession,
        *,
        org_id: int,
        user_id: int,
        template_id: int,
        parameters: dict | None = None,
        input_file_ids: list[int] | None = None,
        resource_profile: str = "medium",
        experiment_id: int | None = None,
        project_id: int | None = None,
    ) -> ComputeSession:
        template = await TemplateNotebookService.get_template(session, org_id, template_id)
        if not template:
            raise NotFoundError(f"Template {template_id} not found")
        # Trust boundary (C2 grill): only curated, platform-owned templates run headless,
        # because the pod executes as the notebook_runner service account.
        if not template.is_builtin:
            raise ValidationError("Headless execution is restricted to curated platform templates")

        allowed, message = await QuotaService.check_quota(session, user_id, estimated_hours=1.0)
        if not allowed:
            raise ConflictError(f"Quota exceeded: {message}")

        cpu_cores, memory_gb = NotebookService.get_resource_profile(resource_profile)

        cs = ComputeSession(
            user_id=user_id,
            organization_id=org_id,
            session_type=HEADLESS_SESSION_TYPE,
            experiment_id=experiment_id,
            project_id=project_id,
            resource_profile=resource_profile,
            cpu_cores=cpu_cores,
            memory_gb=memory_gb,
            requested_disk_gb=100,
            status="pending",
            started_at=_now(),
        )
        session.add(cs)
        await session.flush()

        # Inject parameters into the template's `parameters`-tagged cell (reuse the
        # interactive clone path's injection so both surfaces behave identically).
        content = await TemplateNotebookService.get_template_content(org_id, template)
        notebook_json = json.loads(content)
        params = dict(template.parameters_json or {})
        params.update(parameters or {})
        notebook_json = TemplateNotebookService._inject_parameters(notebook_json, params)

        spec: dict = {
            "session_type": HEADLESS_SESSION_TYPE,
            "resource_profile": resource_profile,
            "cpu_cores": cpu_cores,
            "memory_gb": memory_gb,
            "experiment_id": experiment_id,
            "project_id": project_id,
            "user_id": user_id,
            "session_id": cs.id,
            "notebook_json": notebook_json,
            "notebook_name": template.notebook_path.split("/")[-1],
            "parameters": params,
        }

        config_map = await PlatformConfigService.get_many(
            session, ["working_bucket_name", "notebook_runner_sa_email", "bioaf_scrna_image"]
        )
        bucket_name = (config_map.get("working_bucket_name") or "").strip()
        if bucket_name and bucket_name != "null":
            spec["working_bucket"] = bucket_name
        sa_email = (config_map.get("notebook_runner_sa_email") or "").strip()
        if sa_email and sa_email != "null":
            spec["notebook_runner_sa_email"] = sa_email
        # Run on the built notebook image (the same default the interactive launch resolves). Without
        # it the adapter falls back to a bare "bioaf-scrna:latest" that the cluster cannot pull.
        image = (config_map.get("bioaf_scrna_image") or "").strip()
        if image and image != "null":
            spec["image"] = image

        if input_file_ids:
            from app.models.file import File

            file_results = await session.execute(select(File).where(File.id.in_(input_file_ids)))
            found_files = {f.id: f for f in file_results.scalars().all()}
            name_cache = await _resolve_input_file_context(session, found_files)
            input_files_spec: list[dict] = []
            for fid in input_file_ids:
                f = found_files.get(fid)
                if not f or f.organization_id != org_id:
                    raise ValidationError(f"File {fid} not found or not accessible")
                input_files_spec.append(
                    {
                        "file_id": f.id,
                        "gcs_uri": f.storage_uri,
                        "relative_path": _build_relative_path(f, name_cache),
                    }
                )
            spec["input_files"] = input_files_spec

        try:
            adapter = get_notebook_adapter()
            result = await adapter.launch_session(spec)
            cs.compute_job_ref = result.provider_details.get("pod_name")
            cs.k8s_pod_name = result.provider_details.get("pod_name")
            cs.k8s_namespace = result.provider_details.get("namespace")
            cs.provider_metadata = {
                k: v
                for k, v in {
                    "pod_name": result.provider_details.get("pod_name"),
                    "namespace": result.provider_details.get("namespace"),
                }.items()
                if v is not None
            }
            cs.gcs_home_prefix = result.provider_details.get("gcs_home_prefix")
            cs.status = "failed" if result.status == ServiceState.ERROR else "running"

            if input_file_ids:
                from app.models.notebook_session_file import NotebookSessionFile

                for fid in input_file_ids:
                    session.add(NotebookSessionFile(session_id=cs.id, file_id=fid, access_type="input"))
        except (ConflictError, NotFoundError, ValidationError):
            raise
        except Exception as e:
            from app.adapters.failure_classification import classify_gce_vm_failure

            cs.status = "failed"
            reason, msg = classify_gce_vm_failure(str(e))
            cs.failure_reason = reason
            cs.failure_message = msg
            logger.error("Headless notebook launch failed for session %s: %s", cs.id, e)

        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="notebook_session",
            entity_id=cs.id,
            action="execute_template",
            details={"template_id": template_id, "status": cs.status},
        )
        return cs

    @staticmethod
    async def poll_execution(session: AsyncSession, cs: ComputeSession) -> ComputeSession:
        """Advance a headless run by reading adapter pod state.

        RUNNING/STARTING -> stays running. ERROR -> failed. Terminal success (the pod
        exited 0, surfaced as STOPPED) -> collect + register outputs, then completed.
        """
        if cs.status in ("completed", "failed", "stopped"):
            return cs

        adapter = get_notebook_adapter()
        status = await adapter.get_session_status(
            cs.compute_job_ref or "",
            pod_name=cs.compute_job_ref or "",
            namespace=cs.provider_namespace or "bioaf-notebooks",
        )
        state = status.status
        if state in (ServiceState.RUNNING, ServiceState.STARTING, ServiceState.UNKNOWN):
            cs.status = "running"
            await session.flush()
            return cs
        if state == ServiceState.ERROR:
            cs.status = "failed"
            cs.failure_message = (status.provider_details or {}).get("message") or "notebook execution failed"
            cs.stopped_at = _now()
            await session.flush()
            return cs

        await NotebookExecutionService._finalize_success(session, cs)
        return cs

    @staticmethod
    async def _finalize_success(session: AsyncSession, cs: ComputeSession) -> None:
        from app.services.session_output_service import SessionOutputService

        working_bucket = ""
        working_bucket_value = await PlatformConfigService.get(session, "working_bucket_name")
        if working_bucket_value is not None:
            val = (working_bucket_value or "").strip()
            if val and val != "null":
                working_bucket = val

        adapter = get_notebook_adapter()
        terminate_result = await adapter.terminate_session(
            cs.compute_job_ref or "",
            pod_name=cs.compute_job_ref or "",
            namespace=cs.provider_namespace or "bioaf-notebooks",
            gcs_home_prefix=cs.gcs_home_prefix or "",
            working_bucket=working_bucket,
            session_type=HEADLESS_SESSION_TYPE,
        )

        output_files = terminate_result.output_files
        if output_files:
            await SessionOutputService.register_outputs(
                session,
                session_id=cs.id,
                organization_id=cs.organization_id,
                project_id=cs.project_id,
                experiment_id=cs.experiment_id,
                user_id=cs.user_id,
                gcs_files=[
                    {"filename": o.filename, "gcs_uri": o.storage_uri, "size_bytes": o.size_bytes} for o in output_files
                ],
            )

        if terminate_result.output_prefix:
            cs.gcs_output_prefix = terminate_result.output_prefix

        if working_bucket and output_files:
            results_bucket_value = await PlatformConfigService.get(session, "results_bucket_name")
            results_bucket = (results_bucket_value or "").strip() if results_bucket_value is not None else ""
            if results_bucket and results_bucket != "null":
                final_prefix = await SessionOutputService.move_outputs_to_results_bucket(
                    session,
                    session_id=cs.id,
                    working_bucket=working_bucket,
                    results_bucket=results_bucket,
                )
                cs.gcs_output_prefix = final_prefix

        cs.status = "completed"
        cs.stopped_at = _now()
        await session.flush()
