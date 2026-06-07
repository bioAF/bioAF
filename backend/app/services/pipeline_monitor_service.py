from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.pipeline_process import PipelineProcess
from app.models.pipeline_run import PipelineRun
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import PIPELINE_COMPLETED, PIPELINE_FAILED, PIPELINE_OOM
from app.adapters.models import ProcessInfo, StorageObjectNotFound, StoredObject
from app.adapters.registry import get_compute_adapter, get_storage_adapter
from app.pipeline import nextflow_trace

if TYPE_CHECKING:
    from app.adapters.base import ComputeProvider

logger = logging.getLogger("bioaf.pipeline_monitor")


# Matches `<link rel="icon" ...>` / `<link rel='shortcut icon' ...>` regardless
# of attribute order. The Nextflow report's favicon points at nextflow.io,
# which our CSP `img-src` blocks; favicons aren't shown in srcdoc iframes
# anyway, so we strip the tag rather than weaken the CSP.
_FAVICON_LINK_RE = re.compile(
    r"<link\b[^>]*\brel\s*=\s*['\"](?:shortcut\s+)?icon['\"][^>]*/?>",
    re.IGNORECASE,
)


def _strip_external_favicon_link(html: str) -> str:
    """Remove `<link rel="icon">` tags from an HTML document."""
    return _FAVICON_LINK_RE.sub("", html)


# A srcdoc iframe inherits its base URL from the parent document, so plain
# in-page anchors like `<a href="#tasks">` resolve to `<parent_url>#tasks`
# and clicking them triggers a cross-document navigation that the parent's
# `frame-ancestors 'none'` blocks. This shim intercepts hash-only clicks in
# capture phase and does an in-iframe scrollIntoView instead. Bootstrap's
# tab anchors (data-toggle present) are left untouched so the Resources
# sub-tabs keep working.
_IFRAME_HASH_NAV_SHIM = (
    "<script>(function(){function h(e){var n=e.target;"
    "while(n&&n!==document){if(n.tagName==='A'){"
    "if(n.hasAttribute('data-toggle'))return;"
    "var u=n.getAttribute('href');"
    "if(u&&u.charAt(0)==='#'){e.preventDefault();"
    "if(u.length>1){var t=document.getElementById(u.substring(1));"
    "if(t)t.scrollIntoView();}}return;}"
    "n=n.parentNode;}}"
    "document.addEventListener('click',h,true);})();</script>"
)


def _inject_iframe_hash_nav_shim(html: str) -> str:
    """Insert the hash-nav click shim before `</body>` (or append if absent)."""
    if "</body>" in html:
        return html.replace("</body>", _IFRAME_HASH_NAV_SHIM + "</body>", 1)
    return html + _IFRAME_HASH_NAV_SHIM


def _prepare_report_for_iframe(html: str) -> str:
    """Apply all transforms needed before serving the report inside a
    srcdoc iframe: strip external favicons (CSP) and inject the hash-nav
    click shim (base URL inheritance workaround)."""
    if not html:
        return html
    return _inject_iframe_hash_nav_shim(_strip_external_favicon_link(html))


class PipelineMonitorService:
    @staticmethod
    async def sync_run_statuses(session: AsyncSession) -> None:
        """Background task: sync pipeline run statuses by reading Nextflow trace files."""
        try:
            result = await session.execute(
                select(PipelineRun)
                .options(selectinload(PipelineRun.processes))
                .where(PipelineRun.status.in_(["running", "pending"]))
            )
            active_runs = list(result.scalars().all())

            for run in active_runs:
                try:
                    await PipelineMonitorService._sync_single_run(session, run)
                except Exception as e:
                    logger.warning("Failed to sync run %d: %s", run.id, e)

            await session.flush()
            await session.commit()
            if active_runs:
                logger.info("Pipeline monitor synced %d active runs", len(active_runs))

        except Exception as e:
            logger.error("Pipeline monitor sync failed: %s", e)

    @staticmethod
    async def _sync_single_run(session: AsyncSession, run: PipelineRun) -> None:
        """Sync a single run's status from the compute adapter.

        For K8s jobs (k8s_job_name set), uses direct K8s Job status polling.
        For Nextflow runs, falls back to trace file parsing.
        """
        job_id = run.k8s_job_name or run.slurm_job_id or str(run.id)

        # K8s direct status polling
        if run.k8s_job_name:
            await PipelineMonitorService._sync_k8s_run(session, run, job_id)
            return

        # Nextflow trace-based polling (legacy path)
        try:
            compute_adapter = get_compute_adapter()
            await compute_adapter.get_job_status(job_id)
            trace_content = await compute_adapter.get_job_logs(job_id)
        except Exception:
            return

        if not trace_content.strip():
            return

        # Parse TSV
        processes = PipelineMonitorService.parse_trace_tsv(trace_content)

        # Upsert pipeline_processes
        existing_by_task_id = {p.task_id: p for p in run.processes if p.task_id}

        for proc_data in processes:
            task_id = proc_data.get("task_id", "")
            if task_id in existing_by_task_id:
                proc = existing_by_task_id[task_id]
            else:
                proc = PipelineProcess(
                    pipeline_run_id=run.id, process_name=proc_data.get("process", ""), task_id=task_id
                )
                session.add(proc)

            proc.status = PipelineMonitorService._map_nf_status(proc_data.get("status", ""))
            proc.exit_code = _safe_int(proc_data.get("exit"))
            proc.cpu_usage = _safe_float(proc_data.get("%cpu"))
            proc.memory_peak_gb = _parse_memory_gb(proc_data.get("peak_rss"))
            proc.duration_seconds = _parse_duration(proc_data.get("realtime"))
            proc.slurm_job_id = proc_data.get("native_id")

        # Compute aggregate progress
        total = len(processes)
        completed = sum(1 for p in processes if p.get("status") == "COMPLETED")
        running = sum(1 for p in processes if p.get("status") == "RUNNING")
        failed = sum(1 for p in processes if p.get("status") == "FAILED")
        cached = sum(1 for p in processes if p.get("status") == "CACHED")

        run.progress_json = {
            "total_processes": total,
            "completed": completed,
            "running": running,
            "failed": failed,
            "cached": cached,
            "percent_complete": round((completed + cached) / total * 100, 1) if total > 0 else 0,
        }

        # Detect completion
        if total > 0 and running == 0 and (completed + cached + failed) == total:
            if failed > 0:
                run.status = "failed"
                run.error_message = f"{failed} process(es) failed"
            else:
                run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)

            await PipelineMonitorService._handle_completion(session, run)

    @staticmethod
    async def _sync_k8s_run(session: AsyncSession, run: PipelineRun, job_id: str) -> None:
        """Sync a K8s Job run by querying the compute adapter for job status.

        Progress data (trace file) is only available after the pipeline
        container exits, so we fetch it on completion/failure transitions
        rather than on every sync cycle.
        """
        try:
            compute_adapter = get_compute_adapter()
            status_result = await compute_adapter.get_job_status(job_id)
        except Exception as e:
            logger.warning("Failed to get K8s job status for run %d: %s", run.id, e)
            return

        k8s_status = status_result.status
        pod_name = status_result.provider_details.get("pod_name")

        # Update pod name if available
        if pod_name:
            run.k8s_pod_name = pod_name
            # Mirror into the neutral provider_metadata (BAL Phase 4).
            run.provider_metadata = {**(run.provider_metadata or {}), "pod_name": pod_name}

        is_custom = run.custom_pipeline_version_id is not None

        if k8s_status == "completed" and run.status != "completed":
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)

            if is_custom:
                # Custom pipelines have no Nextflow trace file
                run.progress_json = {
                    "total_processes": 1,
                    "completed": 1,
                    "running": 0,
                    "failed": 0,
                    "cached": 0,
                    "percent_complete": 100.0,
                }
            else:
                await PipelineMonitorService._populate_progress(session, run, compute_adapter, job_id)
                if not run.progress_json:
                    run.progress_json = {
                        "total_processes": 1,
                        "completed": 1,
                        "running": 0,
                        "failed": 0,
                        "cached": 0,
                        "percent_complete": 100.0,
                    }
            await PipelineMonitorService._handle_completion(session, run)

        elif k8s_status == "failed" and run.status != "failed":
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)

            if is_custom:
                run.progress_json = {
                    "total_processes": 1,
                    "completed": 0,
                    "running": 0,
                    "failed": 1,
                    "cached": 0,
                    "percent_complete": 0.0,
                }
            else:
                await PipelineMonitorService._populate_progress(session, run, compute_adapter, job_id)

            # Try to get error info from logs
            try:
                log_content = await compute_adapter.get_job_logs(job_id)
                if log_content:
                    run.error_message = log_content[-500:] if len(log_content) > 500 else log_content
                else:
                    run.error_message = "Job failed (no logs available)"
            except Exception:
                run.error_message = "Job failed (could not retrieve logs)"

            # Classify failure reason from K8s termination info and trace data
            await PipelineMonitorService._classify_failure(session, run, status_result)

            await PipelineMonitorService._handle_completion(session, run)

    @staticmethod
    async def _classify_failure(session: AsyncSession, run: PipelineRun, status_result: dict) -> None:
        """Set failure_reason based on K8s termination info and trace data.

        Priority:
        1. OOMKilled in container termination reasons -> 'oom'
        2. Preemption exit codes (143/137/247) in failed processes -> 'preemption_exhausted'
        3. Otherwise -> 'task_error'
        """
        PREEMPTION_EXIT_CODES = {143, 137, 247}

        termination_reasons = status_result.termination_reasons
        oom_detected = any(r.reason == "OOMKilled" for r in termination_reasons)

        if oom_detected:
            machine_type = await PipelineMonitorService._get_pipeline_machine_type(session)
            run.failure_reason = "oom"
            run.error_message = (
                f"Pipeline failed: out of memory. The pipeline's memory requirements "
                f"exceeded the capacity of the current node size ({machine_type}). "
                f"Go to Infrastructure > Components and select a larger pipeline machine size, "
                f"then re-run the pipeline."
            )

            # Emit OOM event for notifications
            import asyncio

            experiment_name = ""
            if run.experiment_id:
                from app.models.experiment import Experiment

                exp_result = await session.execute(select(Experiment.name).where(Experiment.id == run.experiment_id))
                experiment_name = exp_result.scalar_one_or_none() or ""

            asyncio.create_task(
                event_bus.emit(
                    PIPELINE_OOM,
                    {
                        "event_type": PIPELINE_OOM,
                        "org_id": run.organization_id,
                        "user_id": run.submitted_by_user_id,
                        "target_user_id": run.submitted_by_user_id,
                        "entity_type": "pipeline_run",
                        "entity_id": run.id,
                        "run_id": run.id,
                        "pipeline_name": run.pipeline_name,
                        "experiment_name": experiment_name,
                        "machine_type": machine_type,
                        "title": "Pipeline failed: out of memory",
                        "message": (
                            f"{run.pipeline_name} on experiment {experiment_name} failed because "
                            f"a process exceeded the memory capacity of the current node size "
                            f"({machine_type})."
                        ),
                        "severity": "critical",
                        "summary": (f"Pipeline '{run.pipeline_name}' run {run.id} failed: out of memory"),
                    },
                )
            )
            return

        # Check process records for preemption exit codes.
        # Query the session directly since _populate_progress may have added
        # new PipelineProcess records that aren't in the relationship yet.
        proc_result = await session.execute(
            select(PipelineProcess).where(
                PipelineProcess.pipeline_run_id == run.id,
                PipelineProcess.status == "failed",
            )
        )
        failed_processes = list(proc_result.scalars().all())
        preemption_detected = any(p.exit_code in PREEMPTION_EXIT_CODES for p in failed_processes)

        if preemption_detected:
            run.failure_reason = "preemption_exhausted"
            run.error_message = (
                "Pipeline failed after repeated interruptions from Spot instance "
                "reclamation. This can happen during periods of high cloud demand. "
                "Re-run the pipeline to try again, or disable Spot instances in "
                "Infrastructure > Components for guaranteed availability."
            )
            return

        run.failure_reason = "task_error"

    @staticmethod
    async def _get_pipeline_machine_type(session: AsyncSession) -> str:
        """Read the pipeline machine type from platform_config."""
        result = await session.execute(
            text("SELECT value FROM platform_config WHERE key = 'k8s_pipeline_machine_type'")
        )
        row = result.first()
        return row[0] if row else "unknown"

    @staticmethod
    async def _populate_progress(
        session: AsyncSession,
        run: PipelineRun,
        compute_adapter: ComputeProvider,
        job_id: str,
    ) -> None:
        """Fetch progress from the adapter and update run + PipelineProcess records."""
        try:
            progress = await compute_adapter.get_job_progress(job_id)
        except Exception as e:
            logger.warning("Failed to get job progress for run %d: %s", run.id, e)
            return

        adapter_processes = progress.processes
        if not adapter_processes:
            return

        existing_by_task = {p.task_id: p for p in run.processes if p.task_id}
        existing_by_name = {p.process_name: p for p in run.processes if not p.task_id}

        # The Nextflow trace has one row per task attempt. Group rows by
        # `task_id` so retries of the same task collapse, while legitimate
        # parallel runs of the same process (different task_ids, same name,
        # e.g. nf-core/scrnaseq's MTX_TO_H5AD running once per matrix
        # variant) stay as separate entries. Within a task_id, the highest
        # `attempt` is the authoritative final status.
        # task_id may be missing on older adapter outputs; fall back to
        # name so we don't crash, but log it so it can be diagnosed.
        attempts_by_task: dict[str, list[ProcessInfo]] = {}
        for proc_data in adapter_processes:
            key = proc_data.task_id or proc_data.name or ""
            attempts_by_task.setdefault(key, []).append(proc_data)

        completed = 0
        running = 0
        failed = 0
        cached = 0
        retries: list[dict] = []

        for key, attempts in attempts_by_task.items():
            final = max(attempts, key=lambda a: a.attempt or 1)
            final_status = final.status or ""
            name = final.name or key

            if final_status == "completed":
                completed += 1
            elif final_status == "running":
                running += 1
            elif final_status == "failed":
                failed += 1
            elif final_status == "cached":
                cached += 1

            max_attempt = max((a.attempt or 1 for a in attempts), default=1)
            if max_attempt > 1 or len(attempts) > 1:
                retries.append({"name": name, "attempts": max(max_attempt, len(attempts))})

            task_id = final.task_id or ""
            if task_id and task_id in existing_by_task:
                proc = existing_by_task[task_id]
            elif not task_id and name in existing_by_name:
                proc = existing_by_name[name]
            else:
                proc = PipelineProcess(
                    pipeline_run_id=run.id,
                    process_name=name,
                    task_id=task_id or None,
                )
                session.add(proc)

            # Persist the final attempt's status + metrics.
            proc.status = final_status
            if final.exit_code is not None:
                proc.exit_code = final.exit_code
            if final.cpu is not None:
                proc.cpu_usage = final.cpu
            if final.memory_gb is not None:
                proc.memory_peak_gb = final.memory_gb
            if final.duration_s is not None:
                proc.duration_seconds = final.duration_s

        total = len(attempts_by_task)
        pct = round((completed + cached) / total * 100, 1) if total > 0 else 0.0
        progress_payload: dict = {
            "total_processes": total,
            "completed": completed,
            "running": running,
            "failed": failed,
            "cached": cached,
            "percent_complete": pct,
        }
        if retries:
            progress_payload["retries"] = retries
        run.progress_json = progress_payload

    @staticmethod
    async def _handle_completion(session: AsyncSession, run: PipelineRun) -> None:
        """Handle pipeline completion: update experiment status, index outputs."""
        # Check if any other active runs for this experiment
        if run.experiment_id:
            other_active = await session.execute(
                select(PipelineRun.id).where(
                    PipelineRun.experiment_id == run.experiment_id,
                    PipelineRun.id != run.id,
                    PipelineRun.status.in_(["running", "pending"]),
                )
            )
            if not other_active.first():
                # No other active runs — advance experiment to "pipeline_complete"
                # (review step now precedes "analysis" per ADR-019)
                try:
                    from app.services.experiment_service import ExperimentService

                    await ExperimentService.update_status(
                        session,
                        run.experiment_id,
                        run.organization_id,
                        run.submitted_by_user_id,
                        "pipeline_complete",
                    )
                except Exception as e:
                    logger.warning("Could not advance experiment status: %s", e)

        # Persist pipeline logs to GCS while the pod is still alive
        k8s_job_name = run.k8s_job_name
        if k8s_job_name:
            try:
                compute_adapter = get_compute_adapter()
                await compute_adapter.persist_job_logs(k8s_job_name)
            except Exception as e:
                logger.warning("Failed to persist logs for run %d: %s", run.id, e)

        is_custom = run.custom_pipeline_version_id is not None

        # Collect output files via storage adapter
        try:
            storage_adapter = get_storage_adapter()
            outdir = (run.parameters_json or {}).get("outdir", "")
            if not outdir:
                # Fall back: read results_bucket_name from platform_config
                bucket_row = (
                    await session.execute(text("SELECT value FROM platform_config WHERE key = 'results_bucket_name'"))
                ).first()
                if bucket_row:
                    outdir = f"gs://{bucket_row[0]}/experiments/{run.experiment_id}/pipeline-runs/{run.id}"
                else:
                    outdir = f"/data/results/experiments/{run.experiment_id}/pipeline-runs/{run.id}"
            collected = await storage_adapter.collect_outputs(
                outdir,
                {"id": run.id, "experiment_id": run.experiment_id},
            )
            if collected:
                output_meta: dict = {"files": [f.filename for f in collected]}

                if is_custom:
                    report_uri, report_format = _find_custom_report(collected)
                    if report_uri:
                        output_meta["report_path"] = report_uri
                        output_meta["report_format"] = report_format

                    version = run.custom_pipeline_version
                    log_path_setting = version.log_file_path if version else None
                    if log_path_setting:
                        log_uri = _find_custom_log(collected, log_path_setting)
                        if log_uri:
                            output_meta["custom_log_path"] = log_uri

                run.output_files_json = output_meta
                try:
                    from app.services.pipeline_output_service import PipelineOutputService

                    await PipelineOutputService.register_outputs(
                        session,
                        run,
                        [
                            {
                                "filename": f.filename,
                                "gcs_uri": f.storage_uri,
                                "size_bytes": f.size_bytes,
                                "md5_hash": f.md5_hash,
                            }
                            for f in collected
                        ],
                    )
                    logger.info("Registered %d output files for run %d", len(collected), run.id)
                except Exception as reg_err:
                    logger.warning("Failed to register output files for run %d: %s", run.id, reg_err)
        except Exception as e:
            logger.warning("Failed to collect output files for run %d: %s", run.id, e)

        # Register Nextflow report and trace from the RAW store (Nextflow only).
        # register_nextflow_metadata resolves the URIs via the storage adapter.
        if run.k8s_job_name and not is_custom:
            try:
                from app.services.pipeline_output_service import PipelineOutputService

                await PipelineOutputService.register_nextflow_metadata(session, run)
            except Exception as e:
                logger.warning("Failed to register NF metadata for run %d: %s", run.id, e)

        # Audit log
        await log_action(
            session,
            user_id=run.submitted_by_user_id,
            entity_type="pipeline_run",
            entity_id=run.id,
            action="complete",
            details={"status": run.status, "progress": run.progress_json},
        )

        # Emit event for activity feed / notifications
        import asyncio

        if run.status == "completed":
            asyncio.create_task(
                event_bus.emit(
                    PIPELINE_COMPLETED,
                    {
                        "event_type": PIPELINE_COMPLETED,
                        "org_id": run.organization_id,
                        "user_id": run.submitted_by_user_id,
                        "target_user_id": run.submitted_by_user_id,
                        "entity_type": "pipeline_run",
                        "entity_id": run.id,
                        "title": f"Pipeline '{run.pipeline_name}' completed",
                        "message": f"Run {run.id} finished successfully",
                        "summary": f"Pipeline '{run.pipeline_name}' run {run.id} completed",
                    },
                )
            )
        else:
            asyncio.create_task(
                event_bus.emit(
                    PIPELINE_FAILED,
                    {
                        "event_type": PIPELINE_FAILED,
                        "org_id": run.organization_id,
                        "user_id": run.submitted_by_user_id,
                        "target_user_id": run.submitted_by_user_id,
                        "entity_type": "pipeline_run",
                        "entity_id": run.id,
                        "title": f"Pipeline '{run.pipeline_name}' failed",
                        "message": run.error_message or "Run failed",
                        "severity": "critical",
                        "summary": f"Pipeline '{run.pipeline_name}' run {run.id} failed",
                    },
                )
            )

        # Auto-generate QC dashboard if component is enabled and run succeeded
        if run.status == "completed":
            try:
                from app.services.component_service import ComponentService

                if await ComponentService.is_enabled(session, "qc_dashboard"):
                    from app.services.qc_dashboard_service import QCDashboardService

                    await QCDashboardService.generate_qc_dashboard(session, run.organization_id, run.id)
                    logger.info("QC dashboard generated for run %d", run.id)
            except Exception as e:
                logger.warning("Failed to generate QC dashboard for run %d: %s", run.id, e)

    @staticmethod
    def parse_trace_tsv(content: str) -> list[dict]:
        """Parse a Nextflow trace.tsv file into a list of raw row dicts.

        Delegates to the shared ``app.pipeline.nextflow_trace`` parser.
        """
        return nextflow_trace.parse_trace_rows(content)

    @staticmethod
    def _map_nf_status(nf_status: str) -> str:
        return nextflow_trace.map_nf_status(nf_status)

    @staticmethod
    async def get_run_logs(
        session: AsyncSession,
        run_id: int,
        process_name: str,
        force_pod_logs: bool = False,
    ) -> dict:
        """Get stdout/stderr for a specific process or K8s job."""
        run_result = await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))
        run = run_result.scalar_one_or_none()

        if run is not None and run.k8s_job_name:
            if run.custom_pipeline_version_id is not None:
                return await PipelineMonitorService._get_custom_run_logs(run, force_pod_logs=force_pod_logs)

            stdout = ""
            try:
                compute_adapter = get_compute_adapter()
                stdout = await compute_adapter.get_job_logs(run.k8s_job_name)
            except Exception as e:
                logger.warning("Failed to read K8s logs for run %d: %s", run_id, e)
            return {"stdout": stdout, "stderr": ""}

        # Nextflow process-based log retrieval
        result = await session.execute(
            select(PipelineProcess).where(
                PipelineProcess.pipeline_run_id == run_id,
                PipelineProcess.process_name == process_name,
            )
        )
        process = result.scalar_one_or_none()
        if not process:
            return {"stdout": "", "stderr": ""}

        stdout = ""
        stderr = ""
        try:
            compute_adapter = get_compute_adapter()
            logs = await compute_adapter.get_job_logs(str(process.pipeline_run_id))
            stdout = logs
        except Exception as e:
            logger.warning("Failed to read logs for process %s: %s", process_name, e)

        return {"stdout": stdout, "stderr": stderr}

    @staticmethod
    async def _get_custom_run_logs(run: PipelineRun, force_pod_logs: bool = False) -> dict:
        """Log retrieval for custom pipeline runs.

        Returns pod logs when no log_file_path is configured. Otherwise returns
        pod logs with a `custom_log_pending` flag while running, and the custom
        log file (with `pod_logs_available` flag) once the run has completed.
        `force_pod_logs=True` overrides the custom file selection so callers can
        explicitly request the system pod logs.
        """
        version = run.custom_pipeline_version
        log_file_path = version.log_file_path if version else None

        if not log_file_path:
            return {"stdout": await _safe_pod_logs(run.k8s_job_name, run.id), "stderr": ""}

        if force_pod_logs:
            return {
                "stdout": await _safe_pod_logs(run.k8s_job_name, run.id),
                "stderr": "",
                "log_source": "pod",
            }

        if run.status in ("running", "pending"):
            return {
                "stdout": await _safe_pod_logs(run.k8s_job_name, run.id),
                "stderr": "",
                "log_source": "pod",
                "custom_log_pending": True,
            }

        custom_log_uri = (run.output_files_json or {}).get("custom_log_path")
        if custom_log_uri:
            content = await _read_gcs_text(custom_log_uri)
            if content is not None:
                return {
                    "stdout": content,
                    "stderr": "",
                    "log_source": "custom_file",
                    "pod_logs_available": True,
                }

        return {
            "stdout": await _safe_pod_logs(run.k8s_job_name, run.id),
            "stderr": "",
            "log_source": "pod",
            "custom_log_missing": True,
        }

    @staticmethod
    async def get_run_report(session: AsyncSession, run_id: int) -> str:
        """Read the report from GCS.

        For Nextflow runs this returns the HTML report from the compute
        adapter. For custom pipeline runs this returns the report artifact
        (HTML or markdown) registered during output collection.
        """
        run_result = await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))
        run = run_result.scalar_one_or_none()

        if run is None or not run.k8s_job_name:
            return ""

        if run.custom_pipeline_version_id is not None:
            report_uri = (run.output_files_json or {}).get("report_path")
            if not report_uri:
                return ""
            content = await _read_gcs_text(report_uri)
            return _prepare_report_for_iframe(content or "")

        try:
            compute_adapter = get_compute_adapter()
            report = await compute_adapter.get_job_report(run.k8s_job_name)
            return _prepare_report_for_iframe(report)
        except Exception as e:
            logger.warning("Failed to read report for run %d: %s", run_id, e)
            return ""


def _find_custom_report(collected: list[StoredObject]) -> tuple[str | None, str | None]:
    """Detect a `report/report.html` or `report/report.md` artifact in collected outputs.

    HTML is preferred when both are present.
    """
    html_uri: str | None = None
    md_uri: str | None = None
    for f in collected:
        uri = f.storage_uri or ""
        if uri.endswith("/report/report.html"):
            html_uri = uri
        elif uri.endswith("/report/report.md"):
            md_uri = uri
    if html_uri:
        return html_uri, "html"
    if md_uri:
        return md_uri, "md"
    return None, None


def _find_custom_log(collected: list[StoredObject], log_file_path: str) -> str | None:
    """Find a custom log artifact whose GCS URI ends with the configured log path.

    `log_file_path` is the path inside the pod (e.g. `/outputs/analysis.log`);
    collected GCS URIs end with the same suffix relative to the outputs root.
    """
    relative = log_file_path
    if relative.startswith("/outputs/"):
        relative = relative[len("/outputs/") :]
    relative = relative.lstrip("/")
    if not relative:
        return None
    needle = "/" + relative
    for f in collected:
        uri = f.storage_uri or ""
        if uri.endswith(needle):
            return uri
    return None


async def _read_gcs_text(gcs_uri: str) -> str | None:
    """Download a storage object as text via the storage adapter.

    Returns None if the object is missing or unreadable. Routing through the
    adapter offloads the blocking SDK call to a worker thread (fixing the
    event-loop stall the inline ``download_as_text`` previously caused).
    """
    if not gcs_uri.startswith("gs://"):
        return None
    try:
        return await get_storage_adapter().read_text(gcs_uri)
    except StorageObjectNotFound:
        return None
    except Exception as e:
        logger.warning("Failed to read storage object %s: %s", gcs_uri, e)
        return None


async def _safe_pod_logs(k8s_job_name: str | None, run_id: int) -> str:
    """Fetch pod logs via the compute adapter, returning empty string on error."""
    if not k8s_job_name:
        return ""
    try:
        compute_adapter = get_compute_adapter()
        return await compute_adapter.get_job_logs(k8s_job_name)
    except Exception as e:
        logger.warning("Failed to read K8s logs for run %d: %s", run_id, e)
        return ""


# Trace-field normalizers. These are thin aliases of the shared
# app.pipeline.nextflow_trace helpers (the single source of truth); kept as
# module-level names because the monitor and its tests import them directly.
_safe_int = nextflow_trace.safe_int
_safe_float = nextflow_trace.safe_float
_parse_memory_gb = nextflow_trace.parse_memory_gb
_parse_duration = nextflow_trace.parse_duration_s
