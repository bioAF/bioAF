import logging
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.component import ComponentState
from app.platform.platform_config_service import PlatformConfigService
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import COMPONENT_HEALTH_DEGRADED, COMPONENT_HEALTH_DOWN
from app.services.notebook_image_service import build_notebook_image
from app.runtime import spawn_event

logger = logging.getLogger("bioaf.component_service")

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


# The stack-based view: what the Components page lists, the setup wizard accepts,
# and the toggle acts on. Its dependency rules are not COMPONENT_CATALOG's: these
# are written against kubernetes_cluster, which that catalog does not model.

KUBERNETES_COMPONENTS: list[dict] = [
    {
        "key": "nextflow",
        "name": "Nextflow",
        "category": "pipeline_orchestration",
        "description": "Pipeline orchestration using Nextflow with native Kubernetes executor. Supports nf-core workflows.",
        "cost_estimate": "$0 (uses Kubernetes compute)",
        "dependencies": ["kubernetes_cluster"],
        "configurable": False,
    },
    {
        "key": "snakemake",
        "name": "Snakemake",
        "category": "pipeline_orchestration",
        "description": "Pipeline orchestration using Snakemake with Kubernetes executor support.",
        "cost_estimate": "$0 (uses Kubernetes compute)",
        "dependencies": ["kubernetes_cluster"],
        "configurable": False,
    },
    {
        "key": "jupyterhub",
        "name": "JupyterHub",
        "category": "analysis",
        "description": "Managed Jupyter notebook environment on Kubernetes with pre-built scRNA-seq kernels.",
        "cost_estimate": "$50-$200/month",
        "dependencies": ["kubernetes_cluster"],
        "configurable": True,
    },
    {
        "key": "rstudio",
        "name": "RStudio",
        "category": "analysis",
        "description": "Managed RStudio environment on Kubernetes with Seurat and Bioconductor pre-installed.",
        "cost_estimate": "$50-$200/month",
        "dependencies": ["kubernetes_cluster"],
        "configurable": True,
    },
    {
        "key": "cellxgene",
        "name": "cellxgene",
        "category": "visualization",
        "description": "Interactive single-cell data explorer for h5ad files.",
        "cost_estimate": "$20-$50/month",
        "dependencies": [],
        "configurable": False,
    },
    {
        "key": "qc_dashboard",
        "name": "QC Dashboard",
        "category": "visualization",
        "description": "Auto-generated quality control dashboards after pipeline runs.",
        "cost_estimate": "$10-$30/month",
        "dependencies": ["nextflow"],
        "configurable": False,
    },
    {
        "key": "meilisearch",
        "name": "Meilisearch",
        "category": "search",
        "description": "Full-text search over protocols, metadata, and pipeline logs.",
        "cost_estimate": "$20-$50/month",
        "dependencies": [],
        "configurable": False,
    },
]

# Components that require the bioaf-scrna notebook image
_NOTEBOOK_COMPONENTS = {"rstudio", "jupyterhub"}
# Components that require the cellxgene image
_CELLXGENE_COMPONENTS = {"cellxgene"}


@dataclass(frozen=True)
class ComponentToggleResult:
    component_key: str
    enabled: bool
    status: str


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
    async def toggle_stack_component(
        session: AsyncSession,
        component_key: str,
        *,
        user_id: int,
        force_rebuild: bool = False,
    ) -> ComponentToggleResult:
        """Flip a stack component, enforcing dependencies and starting image builds.

        Raises NotFoundError or ValidationError with the caller-facing message.
        Flushes state and audit; the caller owns the commit. A failed image build
        does not refuse the toggle: the component is enabled as ``build_failed``.
        """
        # Block toggling kubernetes_cluster directly (use stack teardown)
        if component_key == "kubernetes_cluster":
            raise ValidationError("Cannot toggle kubernetes_cluster directly. Use stack deploy/teardown.")

        comp_def = next((c for c in KUBERNETES_COMPONENTS if c["key"] == component_key), None)
        if comp_def is None:
            raise NotFoundError(f"Component '{component_key}' not found")

        row = (
            await session.execute(
                text("SELECT enabled, status FROM component_states WHERE component_key = :key").bindparams(
                    key=component_key
                )
            )
        ).fetchone()
        currently_enabled = row[0] if row else False

        if currently_enabled:
            new_enabled = False
            new_status = "disabled"
            await session.execute(
                text("""
                UPDATE component_states SET enabled = false, status = 'disabled'
                WHERE component_key = :key
                """).bindparams(key=component_key)
            )
        else:
            await ComponentService._require_dependencies(session, comp_def["dependencies"])
            new_enabled = True
            new_status = await ComponentService._ensure_component_image(session, component_key, force_rebuild)
            await session.execute(
                text("""
                UPDATE component_states SET enabled = true, status = :status
                WHERE component_key = :key
                """).bindparams(key=component_key, status=new_status)
            )

        await log_action(
            session,
            user_id=user_id,
            entity_type="component",
            entity_id=0,
            action="enable" if new_enabled else "disable",
            details={
                "component_key": component_key,
                "component_name": comp_def["name"],
                "status": new_status,
            },
        )
        return ComponentToggleResult(component_key=component_key, enabled=new_enabled, status=new_status)

    @staticmethod
    async def _require_dependencies(session: AsyncSession, dependencies: list[str]) -> None:
        if not dependencies:
            return
        dep_rows = (
            await session.execute(
                text("SELECT component_key, enabled FROM component_states WHERE component_key = ANY(:keys)").bindparams(
                    keys=dependencies
                )
            )
        ).fetchall()
        enabled_deps = {r[0] for r in dep_rows if r[1]}
        missing = [d for d in dependencies if d not in enabled_deps]
        if missing:
            raise ValidationError(f"Dependency not met: {', '.join(missing)} must be enabled first")

    @staticmethod
    async def _ensure_component_image(session: AsyncSession, component_key: str, force_rebuild: bool) -> str:
        """Start the component's image build if it needs one; return its status."""
        if component_key in _NOTEBOOK_COMPONENTS:
            needs_build = await ComponentService._needs_image_build(
                session, "bioaf_scrna_image", "notebook_image_build_status", force_rebuild
            )
            if needs_build:
                try:
                    await build_notebook_image(session)
                    return "provisioning"
                except Exception as exc:
                    logger.warning("Failed to start notebook image build: %s", exc)
                    return "build_failed"

        if component_key in _CELLXGENE_COMPONENTS:
            from app.services.cellxgene_image_service import build_cellxgene_image

            needs_build = await ComponentService._needs_image_build(
                session, "cellxgene_image", "cellxgene_image_build_status", force_rebuild
            )
            if needs_build:
                try:
                    await build_cellxgene_image(session)
                    return "provisioning"
                except Exception as exc:
                    logger.warning("Failed to start cellxgene image build: %s", exc)
                    return "build_failed"

        return "enabled"

    @staticmethod
    async def _needs_image_build(session: AsyncSession, image_key: str, status_key: str, force_rebuild: bool) -> bool:
        image = await PlatformConfigService.get(session, image_key)
        build_status = await PlatformConfigService.get(session, status_key)
        return force_rebuild or not image or image == "null" or build_status not in ("SUCCESS",)

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

        spawn_event(
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
            ),
            event_type=event_type,
        )
