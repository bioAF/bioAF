"""SLURM notebook adapter stub.

SLURM notebook support is planned for a future release. All methods raise NotImplementedError.
"""

from app.adapters.base import NotebookProvider


class SlurmNotebookProvider(NotebookProvider):
    """SLURM notebook backend - not yet implemented."""

    async def launch_session(self, session_spec: dict) -> dict:  # type: ignore[override]
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def terminate_session(self, session_id: str, **kwargs) -> dict:  # type: ignore[override]
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_session_status(self, session_id: str) -> dict:  # type: ignore[override]
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def list_sessions(self, filters: dict | None = None) -> list[dict]:  # type: ignore[override]
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")

    async def get_connection_command(self, session_id: str) -> str:
        raise NotImplementedError("SLURM compute backend coming soon. Select Kubernetes during setup.")
