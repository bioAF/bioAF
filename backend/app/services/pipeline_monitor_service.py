from __future__ import annotations

import csv
import io
import logging
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
from app.adapters.registry import get_compute_adapter, get_storage_adapter

if TYPE_CHECKING:
    from app.adapters.base import ComputeProvider

logger = logging.getLogger("bioaf.pipeline_monitor")


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

        k8s_status = status_result.get("status", "")
        pod_name = status_result.get("pod_name")

        # Update pod name if available
        if pod_name:
            run.k8s_pod_name = pod_name

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

        termination_reasons = status_result.get("termination_reasons", [])
        oom_detected = any(r.get("reason") == "OOMKilled" for r in termination_reasons)

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

        adapter_processes = progress.get("processes", [])
        if not adapter_processes:
            return

        existing_by_name = {p.process_name: p for p in run.processes}

        completed = 0
        running = 0
        failed = 0
        cached = 0
        for proc_data in adapter_processes:
            name = proc_data.get("name", "")
            status = proc_data.get("status", "")

            if status == "completed":
                completed += 1
            elif status == "running":
                running += 1
            elif status == "failed":
                failed += 1
            elif status == "cached":
                cached += 1

            if name in existing_by_name:
                proc = existing_by_name[name]
            else:
                proc = PipelineProcess(
                    pipeline_run_id=run.id,
                    process_name=name,
                )
                session.add(proc)

            proc.status = status
            exit_val = proc_data.get("exit_code")
            if exit_val is not None:
                proc.exit_code = exit_val
            cpu_val = proc_data.get("cpu")
            if cpu_val is not None:
                proc.cpu_usage = cpu_val
            mem_val = proc_data.get("memory_gb")
            if mem_val is not None:
                proc.memory_peak_gb = mem_val
            dur_val = proc_data.get("duration_s")
            if dur_val is not None:
                proc.duration_seconds = dur_val

        total = len(adapter_processes)
        run.progress_json = {
            "total_processes": total,
            "completed": completed,
            "running": running,
            "failed": failed,
            "cached": cached,
            "percent_complete": progress.get("percent_complete", 0.0),
        }

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
                output_meta: dict = {"files": [f["filename"] for f in collected]}

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

                    await PipelineOutputService.register_outputs(session, run, collected)
                    logger.info("Registered %d output files for run %d", len(collected), run.id)
                except Exception as reg_err:
                    logger.warning("Failed to register output files for run %d: %s", run.id, reg_err)
        except Exception as e:
            logger.warning("Failed to collect output files for run %d: %s", run.id, e)

        # Register Nextflow report and trace from the raw bucket (Nextflow only)
        if run.k8s_job_name and not is_custom:
            try:
                from app.services.pipeline_output_service import PipelineOutputService

                compute_adapter = get_compute_adapter()
                raw_bucket = compute_adapter.get_raw_bucket_name()
                if raw_bucket:
                    await PipelineOutputService.register_nextflow_metadata(session, run, raw_bucket)
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
        """Parse a Nextflow trace.tsv file into a list of dicts."""
        reader = csv.DictReader(io.StringIO(content), delimiter="\t")
        return [dict(row) for row in reader]

    @staticmethod
    def _map_nf_status(nf_status: str) -> str:
        mapping = {
            "COMPLETED": "completed",
            "RUNNING": "running",
            "FAILED": "failed",
            "CACHED": "cached",
            "SUBMITTED": "pending",
            "PENDING": "pending",
            "ABORTED": "failed",
        }
        return mapping.get(nf_status.upper(), nf_status.lower())

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
            return content or ""

        try:
            compute_adapter = get_compute_adapter()
            return await compute_adapter.get_job_report(run.k8s_job_name)
        except Exception as e:
            logger.warning("Failed to read report for run %d: %s", run_id, e)
            return ""


def _find_custom_report(collected: list[dict]) -> tuple[str | None, str | None]:
    """Detect a `report/report.html` or `report/report.md` artifact in collected outputs.

    HTML is preferred when both are present.
    """
    html_uri: str | None = None
    md_uri: str | None = None
    for f in collected:
        uri = f.get("gcs_uri") or ""
        if uri.endswith("/report/report.html"):
            html_uri = uri
        elif uri.endswith("/report/report.md"):
            md_uri = uri
    if html_uri:
        return html_uri, "html"
    if md_uri:
        return md_uri, "md"
    return None, None


def _find_custom_log(collected: list[dict], log_file_path: str) -> str | None:
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
        uri = f.get("gcs_uri") or ""
        if uri.endswith(needle):
            return uri
    return None


async def _read_gcs_text(gcs_uri: str) -> str | None:
    """Download a GCS object as text. Returns None if the object is missing or unreadable."""
    if not gcs_uri.startswith("gs://"):
        return None
    try:
        from google.cloud import storage as gcs_storage
    except ImportError:
        return None
    try:
        bucket_name, _, blob_path = gcs_uri[len("gs://") :].partition("/")
        if not bucket_name or not blob_path:
            return None
        client = gcs_storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return None
        return blob.download_as_text()
    except Exception as e:
        logger.warning("Failed to read GCS object %s: %s", gcs_uri, e)
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


def _safe_int(val) -> int | None:
    if val is None or val == "" or val == "-":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    if val is None or val == "" or val == "-":
        return None
    try:
        return float(str(val).replace("%", ""))
    except (ValueError, TypeError):
        return None


def _parse_memory_gb(val) -> float | None:
    """Parse memory values like '1.2 GB' or '500 MB'."""
    if not val or val == "-":
        return None
    try:
        val = str(val).strip()
        if "GB" in val.upper():
            return float(val.upper().replace("GB", "").strip())
        if "MB" in val.upper():
            return round(float(val.upper().replace("MB", "").strip()) / 1024, 2)
        if "KB" in val.upper():
            return round(float(val.upper().replace("KB", "").strip()) / (1024 * 1024), 4)
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_duration(val) -> int | None:
    """Parse duration strings like '5m 30s' or '1h 2m 3s' into seconds."""
    if not val or val == "-":
        return None
    try:
        val = str(val).strip()
        # Try direct milliseconds first (Nextflow trace uses ms)
        if val.endswith("ms"):
            return int(float(val[:-2]) / 1000)
        if val.endswith("s") and "m" not in val and "h" not in val:
            return int(float(val[:-1]))
        # Parse h/m/s format
        seconds = 0
        if "h" in val:
            parts = val.split("h")
            seconds += int(parts[0].strip()) * 3600
            val = parts[1].strip()
        if "m" in val:
            parts = val.split("m")
            seconds += int(parts[0].strip()) * 60
            val = parts[1].strip()
        if val.endswith("s"):
            seconds += int(float(val[:-1]))
        return seconds if seconds > 0 else None
    except (ValueError, TypeError):
        return None
