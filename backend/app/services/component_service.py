import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.component import ComponentState
from app.services.event_bus import event_bus
from app.services.event_types import COMPONENT_HEALTH_DEGRADED, COMPONENT_HEALTH_DOWN

# Static component catalog definitions
# Each entry may include a "compute_stack" field to indicate which stack it belongs to:
# "kubernetes", "slurm", or absent for universal components.
COMPONENT_CATALOG: dict[str, dict] = {
    # --- Kubernetes-native components ---
    "k8s_pipeline_pool": {
        "name": "K8s Pipeline Node Pool",
        "description": "GKE Autopilot node pool for batch pipeline jobs. Scales to zero when idle.",
        "category": "compute",
        "compute_stack": "kubernetes",
        "dependencies": [],
        "estimated_monthly_cost": "$0 (scales to zero)",
        "provisioning_time_estimate": "~10 minutes",
    },
    "k8s_interactive_pool": {
        "name": "K8s Interactive Node Pool",
        "description": "GKE node pool for notebooks and interactive sessions. Scales to zero when idle.",
        "category": "compute",
        "compute_stack": "kubernetes",
        "dependencies": [],
        "estimated_monthly_cost": "$0 (scales to zero)",
        "provisioning_time_estimate": "~10 minutes",
        # Pool size is configured via the cluster config (k8s_interactive_machine_type
        # / k8s_interactive_max_nodes), which is what Terraform actually applies. No
        # per-component fields here, to avoid exposing settings that do nothing.
    },
    "nextflow_k8s": {
        "name": "Nextflow (K8s Executor)",
        "description": "Pipeline orchestration using Nextflow with native Kubernetes executor. Supports nf-core/scrnaseq.",
        "category": "pipeline_orchestration",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_pipeline_pool"],
        "estimated_monthly_cost": "$0 (uses K8s compute)",
        "provisioning_time_estimate": "~5 minutes",
    },
    "snakemake_k8s": {
        "name": "Snakemake (K8s Executor)",
        "description": "Pipeline orchestration using Snakemake with Kubernetes executor support.",
        "category": "pipeline_orchestration",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_pipeline_pool"],
        "estimated_monthly_cost": "$0 (uses K8s compute)",
        "provisioning_time_estimate": "~5 minutes",
    },
    "jupyter_k8s": {
        "name": "JupyterHub on K8s",
        "description": "Managed Jupyter notebook environment on Kubernetes with pre-built scRNA-seq kernels.",
        "category": "analysis",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_interactive_pool"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
    },
    "rstudio_k8s": {
        "name": "RStudio on K8s",
        "description": "Managed RStudio environment on Kubernetes with Seurat and Bioconductor pre-installed.",
        "category": "analysis",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_interactive_pool"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
    },
    # The runtime (image services, toggle endpoint, migration 025) writes
    # status for the K8s JupyterHub under the key "jupyterhub". Keep a
    # first-class catalog entry under that key so dependency checks, the
    # status read-back, and the wizard picker all resolve. The duplication
    # with `jupyter_k8s` is acknowledged tech-debt; collapsing the two is a
    # separate effort.
    "jupyterhub": {
        "name": "JupyterHub",
        "description": "Managed Jupyter notebook environment on Kubernetes with pre-built scRNA-seq kernels.",
        "category": "analysis",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_interactive_pool"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
    },
    # --- SLURM-stack components ---
    "slurm": {
        "name": "SLURM HPC Cluster",
        "description": "High-performance compute cluster with autoscaling for batch bioinformatics jobs",
        "category": "compute",
        "compute_stack": "slurm",
        "dependencies": [],
        "estimated_monthly_cost": "$200-$1,500",
        "provisioning_time_estimate": "~15 minutes",
    },
    "filestore": {
        "name": "Filestore NFS",
        "description": "Managed NFS storage for shared file access across compute nodes and notebooks",
        "category": "compute",
        "compute_stack": "slurm",
        "dependencies": ["slurm"],
        "estimated_monthly_cost": "$200-$500",
        "provisioning_time_estimate": "~10 minutes",
    },
    "jupyter": {
        "name": "JupyterHub",
        "description": "Managed Jupyter notebook environment with pre-built scRNA-seq kernels",
        "category": "analysis",
        "dependencies": ["slurm", "filestore"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
    },
    "rstudio": {
        "name": "RStudio Server",
        "description": "Managed RStudio environment with Seurat and Bioconductor pre-installed",
        "category": "analysis",
        "dependencies": ["slurm", "filestore"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
    },
    "nextflow": {
        "name": "Nextflow",
        "description": "Pipeline orchestration with nf-core/scrnaseq and custom workflow support",
        "category": "compute",
        "dependencies": ["slurm"],
        "estimated_monthly_cost": "$0 (uses SLURM compute)",
        "provisioning_time_estimate": "~5 minutes",
    },
    "snakemake": {
        "name": "Snakemake",
        "description": "Alternative pipeline orchestration with SLURM executor support",
        "category": "compute",
        "dependencies": ["slurm"],
        "estimated_monthly_cost": "$0 (uses SLURM compute)",
        "provisioning_time_estimate": "~5 minutes",
    },
    "cellxgene": {
        "name": "cellxgene",
        "description": "Interactive single-cell data explorer for h5ad files",
        "category": "visualization",
        "dependencies": [],
        "estimated_monthly_cost": "$20-$50",
        "provisioning_time_estimate": "~5 minutes",
    },
    "meilisearch": {
        "name": "Meilisearch",
        "description": "Full-text search over protocols, metadata, and pipeline logs",
        "category": "search",
        "dependencies": [],
        "estimated_monthly_cost": "$20-$50",
        "provisioning_time_estimate": "~5 minutes",
    },
    "qc_dashboard": {
        "name": "QC Dashboard",
        "description": "Auto-generated quality control dashboards after pipeline runs",
        "category": "visualization",
        "dependencies": ["nextflow"],
        "estimated_monthly_cost": "$10-$30",
        "provisioning_time_estimate": "~5 minutes",
    },
}


class ComponentService:
    @staticmethod
    def get_catalog() -> dict[str, dict]:
        return COMPONENT_CATALOG

    @staticmethod
    async def get_all_states(session: AsyncSession) -> list[ComponentState]:
        result = await session.execute(select(ComponentState).order_by(ComponentState.component_key))
        return list(result.scalars().all())

    @staticmethod
    async def get_state(session: AsyncSession, component_key: str) -> ComponentState | None:
        result = await session.execute(select(ComponentState).where(ComponentState.component_key == component_key))
        return result.scalar_one_or_none()

    @staticmethod
    async def is_enabled(session: AsyncSession, component_key: str) -> bool:
        state = await ComponentService.get_state(session, component_key)
        return bool(state and state.enabled)

    @staticmethod
    async def initialize_states(session: AsyncSession) -> None:
        """Initialize component_states rows for all catalog entries if missing."""
        for key in COMPONENT_CATALOG:
            existing = await ComponentService.get_state(session, key)
            if not existing:
                state = ComponentState(component_key=key, enabled=False, status="disabled", config_json={})
                session.add(state)
        await session.flush()

    @staticmethod
    async def report_health_issue(
        session: AsyncSession,
        component_key: str,
        org_id: int,
        status: str,
        message: str,
    ) -> None:
        """Report a component health issue and emit the appropriate event."""
        catalog = COMPONENT_CATALOG.get(component_key, {})
        component_name = catalog.get("name", component_key)

        if status == "degraded":
            event_type = COMPONENT_HEALTH_DEGRADED
            severity = "warning"
        else:
            event_type = COMPONENT_HEALTH_DOWN
            severity = "critical"

        asyncio.create_task(
            event_bus.emit(
                event_type,
                {
                    "event_type": event_type,
                    "org_id": org_id,
                    "entity_type": "component",
                    "title": f"{component_name} health {status}",
                    "message": message,
                    "severity": severity,
                    "summary": f"Component '{component_name}' is {status}: {message}",
                },
            )
        )
