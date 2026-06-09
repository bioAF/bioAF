import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.models.template_notebook import TemplateNotebook
from app.services.audit_service import log_action
from app.services.gitops_service import GitOpsService

logger = logging.getLogger("bioaf.template_notebook")

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "notebooks" / "templates"

BUILTIN_TEMPLATES = [
    {
        "name": "QC & Filtering",
        "description": "Quality control metrics, filtering, and visualization for scRNA-seq data",
        "category": "qc",
        "notebook_path": "notebooks/01_qc_filtering.ipynb",
        "local_file": "01_qc_filtering.ipynb",
        "compatible_with": "nf-core/scrnaseq",
        "sort_order": 1,
        "parameters": {
            "input_h5ad_path": "/data/results/experiment/adata.h5ad",
            "experiment_name": "my_experiment",
            "mito_threshold": 20,
            "min_genes": 200,
            "min_cells": 3,
            "bioaf_api_url": "http://localhost:8000",
            "experiment_id": None,
        },
    },
    {
        "name": "Normalization & Dimensionality Reduction",
        "description": "Normalization, HVG selection, PCA, UMAP, and t-SNE",
        "category": "normalization",
        "notebook_path": "notebooks/02_normalization_dimreduction.ipynb",
        "local_file": "02_normalization_dimreduction.ipynb",
        "compatible_with": "nf-core/scrnaseq",
        "sort_order": 2,
        "parameters": {
            "input_h5ad_path": "/data/results/experiment/adata_filtered.h5ad",
            "n_highly_variable": 2000,
            "n_pcs": 50,
            "n_neighbors": 15,
            "bioaf_api_url": "http://localhost:8000",
            "experiment_id": None,
        },
    },
    {
        "name": "Clustering & Marker Genes",
        "description": "Leiden clustering and marker gene identification",
        "category": "clustering",
        "notebook_path": "notebooks/03_clustering_markers.ipynb",
        "local_file": "03_clustering_markers.ipynb",
        "compatible_with": "nf-core/scrnaseq",
        "sort_order": 3,
        "parameters": {
            "input_h5ad_path": "/data/results/experiment/adata_processed.h5ad",
            "clustering_resolution": 1.0,
            "n_marker_genes": 25,
            "bioaf_api_url": "http://localhost:8000",
            "experiment_id": None,
        },
    },
    {
        "name": "Differential Expression",
        "description": "Differential expression analysis between conditions",
        "category": "differential_expression",
        "notebook_path": "notebooks/04_differential_expression.ipynb",
        "local_file": "04_differential_expression.ipynb",
        "compatible_with": "nf-core/scrnaseq",
        "sort_order": 4,
        "parameters": {
            "input_h5ad_path": "/data/results/experiment/adata_clustered.h5ad",
            "groupby": "condition",
            "reference_group": "control",
            "test_group": "treatment",
            "bioaf_api_url": "http://localhost:8000",
            "experiment_id": None,
        },
    },
    {
        "name": "Trajectory Inference",
        "description": "RNA velocity, PAGA, and pseudotime analysis",
        "category": "trajectory",
        "notebook_path": "notebooks/05_trajectory_inference.ipynb",
        "local_file": "05_trajectory_inference.ipynb",
        "compatible_with": "nf-core/scrnaseq",
        "sort_order": 5,
        "parameters": {
            "input_h5ad_path": "/data/results/experiment/adata_clustered.h5ad",
            "root_cell_type": None,
            "method": "paga",
            "bioaf_api_url": "http://localhost:8000",
            "experiment_id": None,
        },
    },
]


class TemplateNotebookService:
    @staticmethod
    async def initialize_builtin_templates(session: AsyncSession, org_id: int) -> list[TemplateNotebook]:
        """Create DB records for built-in template notebooks."""
        created = []
        for tmpl in BUILTIN_TEMPLATES:
            result = await session.execute(
                select(TemplateNotebook).where(
                    TemplateNotebook.organization_id == org_id,
                    TemplateNotebook.notebook_path == tmpl["notebook_path"],
                )
            )
            if result.scalar_one_or_none():
                continue

            nb = TemplateNotebook(
                organization_id=org_id,
                name=tmpl["name"],
                description=tmpl["description"],
                category=tmpl["category"],
                notebook_path=tmpl["notebook_path"],
                parameters_json=tmpl["parameters"],
                compatible_with=tmpl["compatible_with"],
                sort_order=tmpl["sort_order"],
                is_builtin=True,
            )
            session.add(nb)
            created.append(nb)

        if created:
            await session.flush()
            logger.info("Initialized %d template notebooks for org %d", len(created), org_id)

        return created

    @staticmethod
    async def list_templates(session: AsyncSession, org_id: int) -> list[TemplateNotebook]:
        result = await session.execute(
            select(TemplateNotebook)
            .where(TemplateNotebook.organization_id == org_id)
            .order_by(TemplateNotebook.sort_order)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_template(session: AsyncSession, org_id: int, template_id: int) -> TemplateNotebook | None:
        result = await session.execute(
            select(TemplateNotebook).where(
                TemplateNotebook.id == template_id,
                TemplateNotebook.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_template_content(org_id: int, template: TemplateNotebook) -> str:
        """Read notebook content from GitOps repo or local filesystem."""
        # Try GitOps repo first
        try:
            from app.database import async_session_factory

            async with async_session_factory() as session:
                repo = await GitOpsService.get_repo(session, org_id)
                if repo:
                    return await GitOpsService.get_file(
                        org_id,
                        repo.github_repo_name,
                        template.notebook_path,
                    )
        except Exception:
            pass

        # Fall back to local file
        local_file = TEMPLATES_DIR / template.notebook_path.split("/")[-1]
        if local_file.exists():
            return local_file.read_text()

        raise NotFoundError(f"Template notebook not found: {template.notebook_path}")

    @staticmethod
    async def clone_template(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        template_id: int,
        new_name: str,
        experiment_id: int | None = None,
        parameter_overrides: dict | None = None,
    ) -> str:
        """Clone a template notebook with parameterization. Returns file path."""
        template = await TemplateNotebookService.get_template(session, org_id, template_id)
        if not template:
            raise NotFoundError(f"Template {template_id} not found")

        content = await TemplateNotebookService.get_template_content(org_id, template)
        nb_data = json.loads(content)

        # Build parameters
        params = dict(template.parameters_json)
        if experiment_id:
            params["experiment_id"] = experiment_id
            # Try to set experiment-specific paths
            from app.models.experiment import Experiment

            result = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
            exp = result.scalar_one_or_none()
            if exp:
                params["experiment_name"] = exp.name
                params["input_h5ad_path"] = f"/data/results/{exp.name}/adata.h5ad"

        if parameter_overrides:
            params.update(parameter_overrides)

        # Update the parameters cell
        nb_data = TemplateNotebookService._inject_parameters(nb_data, params)

        # Write to user's notebook directory
        from app.models.user import User

        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        username = user.email.split("@")[0] if user else "user"

        output_path = f"/home/{username}/notebooks/{new_name}.ipynb"

        await log_action(
            session,
            user_id=user_id,
            entity_type="template_notebook",
            entity_id=template_id,
            action="clone",
            details={
                "new_name": new_name,
                "experiment_id": experiment_id,
                "output_path": output_path,
            },
        )

        return output_path

    @staticmethod
    def _inject_parameters(nb_data: dict, params: dict) -> dict:
        """Replace values in the parameters cell of a notebook."""
        for cell in nb_data.get("cells", []):
            metadata = cell.get("metadata", {})
            tags = metadata.get("tags", [])
            if "parameters" in tags and cell.get("cell_type") == "code":
                # Rebuild the parameters cell
                lines = []
                for key, value in params.items():
                    if isinstance(value, str):
                        lines.append(f'{key} = "{value}"\n')
                    elif value is None:
                        lines.append(f"{key} = None\n")
                    else:
                        lines.append(f"{key} = {value}\n")
                cell["source"] = lines
                break
        return nb_data
