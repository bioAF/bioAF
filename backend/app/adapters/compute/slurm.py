"""SLURM compute adapter stub.

SLURM support is planned for a future release. All methods raise NotImplementedError.
"""

from app.adapters.base import ComputeProvider


class SlurmComputeProvider(ComputeProvider):
    """SLURM compute backend - not yet implemented."""

    async def submit_job(self, job_spec: dict) -> dict:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def cancel_job(self, job_id: str) -> dict:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_job_status(self, job_id: str) -> dict:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def list_jobs(self, filters: dict | None = None) -> list[dict]:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_job_logs(self, job_id: str) -> str:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_job_progress(self, job_id: str) -> dict:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_cluster_status(self) -> dict:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_cluster_metrics(self) -> dict:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_cost_estimate(self, job_spec: dict) -> dict:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_connection_command(self, job_id: str) -> str:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")
