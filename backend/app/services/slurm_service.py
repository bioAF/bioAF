import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundError
from app.models.slurm_job import SlurmJob
from app.services.audit_service import log_action
from app.services.compute_cost_service import ComputeCostService

logger = logging.getLogger("bioaf.slurm")

# Cache for cluster status
_cluster_status_cache: dict | None = None
_cluster_status_cache_time: float = 0
CACHE_TTL = 30  # seconds


class SlurmService:
    @staticmethod
    async def get_ssh_connection():
        """Return an asyncssh connection to the SLURM login node.

        Isolated in its own method for easy mocking in tests.
        """
        import asyncssh

        # Connection details come from platform_config (set after SLURM provisioning)
        # In production, these are read from the DB or environment
        return await asyncssh.connect(
            host="bioaf-slurm-login",
            username="bioaf",
            known_hosts=None,
        )

    @staticmethod
    async def _run_ssh_command(command: str) -> str:
        """Run a command via SSH on the login node. Isolated for mocking."""
        conn = await SlurmService.get_ssh_connection()
        async with conn:
            result = await conn.run(command, check=True)
            return result.stdout

    @staticmethod
    async def get_cluster_status() -> dict:
        """Get cluster status via sinfo. Uses a short-lived cache."""
        global _cluster_status_cache, _cluster_status_cache_time

        now = time.time()
        if _cluster_status_cache and (now - _cluster_status_cache_time) < CACHE_TTL:
            return _cluster_status_cache

        try:
            output = await SlurmService._run_ssh_command("sinfo --json")
            data = json.loads(output)

            partitions = []
            total_nodes = 0
            active_nodes = 0
            queue_depth = 0

            for partition in data.get("sinfo", []):
                name = partition.get("partition", {}).get("name", "unknown")
                nodes = partition.get("nodes", {})
                total = nodes.get("total", 0)
                allocated = nodes.get("allocated", 0)
                idle = nodes.get("idle", 0)

                partitions.append(
                    {
                        "name": name,
                        "max_nodes": total,
                        "active_nodes": allocated,
                        "idle_nodes": idle,
                        "queue_depth": 0,
                        "instance_type": "n2-highmem-8" if name == "standard" else "n2-standard-4",
                        "use_spot": name == "standard",
                    }
                )
                total_nodes += total
                active_nodes += allocated

            # Get queue depth
            try:
                queue_output = await SlurmService._run_ssh_command("squeue --json")
                queue_data = json.loads(queue_output)
                queue_depth = len(queue_data.get("jobs", []))
            except Exception:
                pass

            burn_rate = ComputeCostService.get_cluster_burn_rate_from_nodes(active_nodes)

            status = {
                "controller_status": "running",
                "partitions": partitions,
                "total_nodes": total_nodes,
                "active_nodes": active_nodes,
                "queue_depth": queue_depth,
                "cost_burn_rate_hourly": burn_rate,
            }
            _cluster_status_cache = status
            _cluster_status_cache_time = now
            return status

        except Exception as e:
            logger.warning("Failed to get cluster status via SSH: %s", e)
            if _cluster_status_cache:
                return _cluster_status_cache
            return {
                "controller_status": "unknown",
                "partitions": [],
                "total_nodes": 0,
                "active_nodes": 0,
                "queue_depth": 0,
                "cost_burn_rate_hourly": None,
            }

    @staticmethod
    async def list_jobs(
        session: AsyncSession,
        org_id: int,
        page: int = 1,
        page_size: int = 25,
        user_id: int | None = None,
        status: str | None = None,
        partition: str | None = None,
        experiment_id: int | None = None,
    ) -> tuple[list[SlurmJob], int]:
        query = (
            select(SlurmJob)
            .options(selectinload(SlurmJob.user), selectinload(SlurmJob.experiment))
            .where(SlurmJob.organization_id == org_id)
        )
        count_query = select(func.count(SlurmJob.id)).where(SlurmJob.organization_id == org_id)

        if user_id:
            query = query.where(SlurmJob.user_id == user_id)
            count_query = count_query.where(SlurmJob.user_id == user_id)
        if status:
            query = query.where(SlurmJob.status == status)
            count_query = count_query.where(SlurmJob.status == status)
        if partition:
            query = query.where(SlurmJob.partition == partition)
            count_query = count_query.where(SlurmJob.partition == partition)
        if experiment_id:
            query = query.where(SlurmJob.experiment_id == experiment_id)
            count_query = count_query.where(SlurmJob.experiment_id == experiment_id)

        query = query.order_by(SlurmJob.submitted_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(query)
        jobs = list(result.scalars().all())

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return jobs, total

    @staticmethod
    async def get_job(session: AsyncSession, job_id: int) -> SlurmJob | None:
        result = await session.execute(
            select(SlurmJob)
            .options(selectinload(SlurmJob.user), selectinload(SlurmJob.experiment))
            .where(SlurmJob.id == job_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def submit_job(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        job_script: str,
        partition: str,
        cpu: int,
        memory_gb: int,
        job_name: str | None = None,
        experiment_id: int | None = None,
        notebook_session_id: int | None = None,
    ) -> SlurmJob:
        """Submit a job to SLURM via SSH."""
        output = await SlurmService._run_ssh_command(
            f"sbatch --parsable --partition={partition} --cpus-per-task={cpu} "
            f'--mem={memory_gb}G --job-name="{job_name or "bioaf-job"}" '
            f'--wrap="{job_script}"'
        )
        slurm_job_id = output.strip()

        job = SlurmJob(
            organization_id=org_id,
            user_id=user_id,
            slurm_job_id=slurm_job_id,
            job_name=job_name,
            partition=partition,
            status="pending",
            experiment_id=experiment_id,
            notebook_session_id=notebook_session_id,
            cpu_requested=cpu,
            memory_gb_requested=memory_gb,
        )
        session.add(job)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="slurm_job",
            entity_id=job.id,
            action="submit",
            details={"slurm_job_id": slurm_job_id, "partition": partition},
        )
        return job

    @staticmethod
    async def cancel_job(session: AsyncSession, job_id: int, user_id: int) -> SlurmJob:
        job = await SlurmService.get_job(session, job_id)
        if not job:
            raise NotFoundError("Job not found")

        await SlurmService._run_ssh_command(f"scancel {job.slurm_job_id}")

        old_status = job.status
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="slurm_job",
            entity_id=job.id,
            action="cancel",
            details={"slurm_job_id": job.slurm_job_id},
            previous_value={"status": old_status},
        )
        return job

    @staticmethod
    async def resubmit_job(session: AsyncSession, job_id: int, user_id: int, org_id: int) -> SlurmJob:
        original = await SlurmService.get_job(session, job_id)
        if not original:
            raise NotFoundError("Job not found")

        new_job = await SlurmService.submit_job(
            session,
            user_id=user_id,
            org_id=org_id,
            job_script=f"echo 'Resubmitted from job {original.slurm_job_id}'",
            partition=original.partition,
            cpu=original.cpu_requested or 1,
            memory_gb=original.memory_gb_requested or 1,
            job_name=original.job_name,
            experiment_id=original.experiment_id,
        )

        await log_action(
            session,
            user_id=user_id,
            entity_type="slurm_job",
            entity_id=new_job.id,
            action="resubmit",
            details={"original_job_id": original.id, "slurm_job_id": new_job.slurm_job_id},
        )
        # Re-fetch with eager-loaded relationships for serialization
        return await SlurmService.get_job(session, new_job.id)

    @staticmethod
    async def sync_job_statuses(session: AsyncSession) -> None:
        """Background task: sync job statuses from SLURM via sacct."""
        try:
            output = await SlurmService._run_ssh_command(
                "sacct --json --starttime=$(date -d '7 days ago' +%Y-%m-%dT%H:%M:%S)"
            )
            data = json.loads(output)

            slurm_status_map = {
                "COMPLETED": "completed",
                "FAILED": "failed",
                "CANCELLED": "cancelled",
                "TIMEOUT": "timeout",
                "RUNNING": "running",
                "PENDING": "pending",
            }

            for job_info in data.get("jobs", []):
                slurm_id = str(job_info.get("job_id", ""))
                state = job_info.get("state", {}).get("current", [""])[0]
                mapped_status = slurm_status_map.get(state, state.lower())

                result = await session.execute(select(SlurmJob).where(SlurmJob.slurm_job_id == slurm_id))
                job = result.scalar_one_or_none()
                if job and job.status != mapped_status:
                    job.status = mapped_status
                    if mapped_status in ("completed", "failed", "cancelled", "timeout"):
                        job.completed_at = datetime.now(timezone.utc)
                        if job.started_at:
                            duration_hours = (job.completed_at - job.started_at).total_seconds() / 3600
                            job.cost_estimate = ComputeCostService.estimate_job_cost(
                                "n2-highmem-8" if job.partition == "standard" else "n2-standard-4",
                                duration_hours,
                                job.partition == "standard",
                            )
                    elif mapped_status == "running" and not job.started_at:
                        job.started_at = datetime.now(timezone.utc)

            await session.flush()
            await session.commit()
            logger.info("Job status sync completed")

        except Exception as e:
            logger.error("Job status sync failed: %s", e)
