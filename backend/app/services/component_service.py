import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.component import ComponentState
from app.services.audit_service import log_action
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
        "config_schema": [
            {"key": "pipeline_pool_max_nodes", "label": "Max Nodes", "type": "number", "default": 20},
            {
                "key": "pipeline_pool_machine_type",
                "label": "Machine Type",
                "type": "string",
                "default": "n2-highmem-16",
            },
            {"key": "pipeline_pool_use_spot", "label": "Use Spot VMs", "type": "boolean", "default": True},
        ],
    },
    "k8s_interactive_pool": {
        "name": "K8s Interactive Node Pool",
        "description": "GKE node pool for notebooks and interactive sessions. Scales to zero when idle.",
        "category": "compute",
        "compute_stack": "kubernetes",
        "dependencies": [],
        "estimated_monthly_cost": "$0 (scales to zero)",
        "provisioning_time_estimate": "~10 minutes",
        "config_schema": [
            {"key": "interactive_pool_max_nodes", "label": "Max Nodes", "type": "number", "default": 5},
            {
                "key": "interactive_pool_machine_type",
                "label": "Machine Type",
                "type": "string",
                "default": "n2-standard-4",
            },
        ],
    },
    "nextflow_k8s": {
        "name": "Nextflow (K8s Executor)",
        "description": "Pipeline orchestration using Nextflow with native Kubernetes executor. Supports nf-core/scrnaseq.",
        "category": "pipeline_orchestration",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_pipeline_pool"],
        "estimated_monthly_cost": "$0 (uses K8s compute)",
        "provisioning_time_estimate": "~5 minutes",
        "config_schema": [],
    },
    "snakemake_k8s": {
        "name": "Snakemake (K8s Executor)",
        "description": "Pipeline orchestration using Snakemake with Kubernetes executor support.",
        "category": "pipeline_orchestration",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_pipeline_pool"],
        "estimated_monthly_cost": "$0 (uses K8s compute)",
        "provisioning_time_estimate": "~5 minutes",
        "config_schema": [],
    },
    "jupyter_k8s": {
        "name": "JupyterHub on K8s",
        "description": "Managed Jupyter notebook environment on Kubernetes with pre-built scRNA-seq kernels.",
        "category": "analysis",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_interactive_pool"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
        "config_schema": [
            {"key": "jupyter_cpu_limit", "label": "Max CPU per session", "type": "number", "default": 4},
            {"key": "jupyter_memory_limit", "label": "Max Memory per session (GB)", "type": "number", "default": 8},
            {"key": "session_idle_timeout_hours", "label": "Idle Timeout (hours)", "type": "number", "default": 4},
        ],
    },
    "rstudio_k8s": {
        "name": "RStudio on K8s",
        "description": "Managed RStudio environment on Kubernetes with Seurat and Bioconductor pre-installed.",
        "category": "analysis",
        "compute_stack": "kubernetes",
        "dependencies": ["k8s_interactive_pool"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
        "config_schema": [
            {"key": "rstudio_cpu_limit", "label": "Max CPU per session", "type": "number", "default": 4},
            {"key": "rstudio_memory_limit", "label": "Max Memory per session (GB)", "type": "number", "default": 8},
        ],
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
        "config_schema": [
            {"key": "slurm_max_nodes_standard", "label": "Max Standard Nodes", "type": "number", "default": 20},
            {
                "key": "slurm_instance_type_standard",
                "label": "Standard Instance Type",
                "type": "string",
                "default": "n2-highmem-16",
            },
            {"key": "slurm_use_spot_standard", "label": "Use Spot VMs", "type": "boolean", "default": True},
            {"key": "slurm_max_nodes_interactive", "label": "Max Interactive Nodes", "type": "number", "default": 5},
            {
                "key": "slurm_instance_type_interactive",
                "label": "Interactive Instance Type",
                "type": "string",
                "default": "n2-standard-4",
            },
            {"key": "slurm_idle_timeout_minutes", "label": "Idle Timeout (min)", "type": "number", "default": 10},
        ],
    },
    "filestore": {
        "name": "Filestore NFS",
        "description": "Managed NFS storage for shared file access across compute nodes and notebooks",
        "category": "compute",
        "compute_stack": "slurm",
        "dependencies": ["slurm"],
        "estimated_monthly_cost": "$200-$500",
        "provisioning_time_estimate": "~10 minutes",
        "config_schema": [
            {"key": "filestore_capacity_gb", "label": "Capacity (GB)", "type": "number", "default": 1024},
        ],
    },
    "jupyter": {
        "name": "JupyterHub",
        "description": "Managed Jupyter notebook environment with pre-built scRNA-seq kernels",
        "category": "analysis",
        "dependencies": ["slurm", "filestore"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
        "config_schema": [
            {"key": "jupyter_cpu_limit", "label": "Max CPU per session", "type": "number", "default": 4},
            {"key": "jupyter_memory_limit", "label": "Max Memory per session (GB)", "type": "number", "default": 8},
            {"key": "session_idle_timeout_hours", "label": "Idle Timeout (hours)", "type": "number", "default": 4},
        ],
    },
    "rstudio": {
        "name": "RStudio Server",
        "description": "Managed RStudio environment with Seurat and Bioconductor pre-installed",
        "category": "analysis",
        "dependencies": ["slurm", "filestore"],
        "estimated_monthly_cost": "$50-$200",
        "provisioning_time_estimate": "~10 minutes",
        "config_schema": [
            {"key": "rstudio_cpu_limit", "label": "Max CPU per session", "type": "number", "default": 4},
            {"key": "rstudio_memory_limit", "label": "Max Memory per session (GB)", "type": "number", "default": 8},
        ],
    },
    "nextflow": {
        "name": "Nextflow",
        "description": "Pipeline orchestration with nf-core/scrnaseq and custom workflow support",
        "category": "compute",
        "dependencies": ["slurm"],
        "estimated_monthly_cost": "$0 (uses SLURM compute)",
        "provisioning_time_estimate": "~5 minutes",
        "config_schema": [],
    },
    "snakemake": {
        "name": "Snakemake",
        "description": "Alternative pipeline orchestration with SLURM executor support",
        "category": "compute",
        "dependencies": ["slurm"],
        "estimated_monthly_cost": "$0 (uses SLURM compute)",
        "provisioning_time_estimate": "~5 minutes",
        "config_schema": [],
    },
    "cellxgene": {
        "name": "cellxgene",
        "description": "Interactive single-cell data explorer for h5ad files",
        "category": "visualization",
        "dependencies": [],
        "estimated_monthly_cost": "$20-$50",
        "provisioning_time_estimate": "~5 minutes",
        "config_schema": [],
    },
    "meilisearch": {
        "name": "Meilisearch",
        "description": "Full-text search over protocols, metadata, and pipeline logs",
        "category": "search",
        "dependencies": [],
        "estimated_monthly_cost": "$20-$50",
        "provisioning_time_estimate": "~5 minutes",
        "config_schema": [],
    },
    "qc_dashboard": {
        "name": "QC Dashboard",
        "description": "Auto-generated quality control dashboards after pipeline runs",
        "category": "visualization",
        "dependencies": ["nextflow"],
        "estimated_monthly_cost": "$10-$30",
        "provisioning_time_estimate": "~5 minutes",
        "config_schema": [],
    },
}

# Dependency graph for cascade checks
DEPENDENTS: dict[str, list[str]] = {}
for key, comp in COMPONENT_CATALOG.items():
    for dep in comp["dependencies"]:
        DEPENDENTS.setdefault(dep, []).append(key)


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
    def check_dependencies(component_key: str, enabled_components: set[str]) -> list[str]:
        """Check unmet dependencies. Returns list of missing dependency keys."""
        catalog_entry = COMPONENT_CATALOG.get(component_key)
        if not catalog_entry:
            return []
        return [dep for dep in catalog_entry["dependencies"] if dep not in enabled_components]

    @staticmethod
    def get_dependents(component_key: str) -> list[str]:
        """Get components that depend on this one."""
        return DEPENDENTS.get(component_key, [])

    @staticmethod
    async def enable_component(
        session: AsyncSession,
        component_key: str,
        user_id: int,
    ) -> ComponentState:
        state = await ComponentService.get_state(session, component_key)
        if not state:
            state = ComponentState(component_key=component_key, enabled=False, status="disabled", config_json={})
            session.add(state)
            await session.flush()

        old_status = state.status
        state.enabled = True
        state.status = "provisioning"
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="component",
            entity_id=state.id,
            action="enable",
            details={"component_key": component_key},
            previous_value={"status": old_status, "enabled": False},
        )
        return state

    @staticmethod
    async def disable_component(
        session: AsyncSession,
        component_key: str,
        user_id: int,
    ) -> ComponentState:
        state = await ComponentService.get_state(session, component_key)
        if not state:
            raise ValueError(f"Component {component_key} not found")

        old_status = state.status
        state.enabled = False
        state.status = "destroying"
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="component",
            entity_id=state.id,
            action="disable",
            details={"component_key": component_key},
            previous_value={"status": old_status, "enabled": True},
        )
        return state

    @staticmethod
    async def update_config(
        session: AsyncSession,
        component_key: str,
        config: dict,
        user_id: int,
    ) -> ComponentState:
        state = await ComponentService.get_state(session, component_key)
        if not state:
            raise ValueError(f"Component {component_key} not found")

        old_config = dict(state.config_json)
        state.config_json = config
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="component",
            entity_id=state.id,
            action="configure",
            details={"config": config},
            previous_value={"config": old_config},
        )
        return state

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
