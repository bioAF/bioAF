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
# Package-local template dir. Builtin templates whose files live here ship inside the backend
# image (via `COPY app/ app/`), so they resolve without a per-org GitOps repo. The repo-root
# scripts dir above only exists in a source checkout, not in the deployed container.
PACKAGE_TEMPLATES_DIR = Path(__file__).parent / "notebook_templates"

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
    # Level-3 headless differential-analysis templates (lit_validation, ADR-069). R notebooks
    # (kernelspec 'ir') run by the headless executor; parameters are all str/number so the
    # Python-literal injector stays valid R. They write a normalizer-compatible result table to
    # /outputs. Both use DESeq2 (RNA on gene counts; ATAC/ChIP on the consensus-peak count matrix).
    {
        "name": "Differential Expression (DESeq2, headless)",
        "description": "Reproduce a paper's DEG finding from a gene-count matrix (Level-3 concordance)",
        "category": "differential_expression",
        "notebook_path": "notebooks/de_bulk_deseq2.ipynb",
        "local_file": "de_bulk_deseq2.ipynb",
        "compatible_with": "nf-core/rnaseq",
        "sort_order": 6,
        "parameters": {
            "counts_path": "/data/counts.tsv",
            "output_path": "/outputs/de_results.csv",
            "id_column": "gene_id",
            "test_samples": "",
            "reference_samples": "",
            # Default empty (unpaired `~ condition`). build_level3_inputs overrides with per-sample block
            # labels for a matched-pairs design (`~ block + condition`). The injector rebuilds the whole
            # parameters cell from this merged dict, so the default MUST live here to stay defined.
            "block_labels": "",
            "lfc_threshold": 1.0,
            "padj_threshold": 0.05,
        },
    },
    {
        # plan_7 step 8. The other three headless reproducers are DESeq2, which requires integer
        # counts. A DEPOSITED matrix is very often already normalized (GSE274331's is TPM, every
        # column summing to exactly 1e6), and feeding that to DESeq2 invalidates its dispersion model
        # and returns numbers that are confidently wrong. limma-trend is the standard, defensible
        # test for a matrix somebody else normalized.
        #
        # `limma` is already installed in the notebook image AND already in its build-time missing-
        # package assertion (notebook_image_service), so this needs no image change.
        "name": "Differential Expression (limma-trend, headless)",
        "description": "Reproduce a paper's finding from a deposited matrix of already-normalized values (Level-3)",
        "category": "differential_expression",
        "notebook_path": "notebooks/de_normalized_limma.ipynb",
        "local_file": "de_normalized_limma.ipynb",
        "compatible_with": None,
        "sort_order": 9,
        "parameters": {
            "counts_path": "/data/matrix.tsv",
            "output_path": "/outputs/de_results.csv",
            # A deposit often leaves its id column unnamed, so "" means "the first column" rather
            # than an error. The nf-core templates can hard-code a name; this one cannot.
            "id_column": "",
            "test_samples": "",
            "reference_samples": "",
            "block_labels": "",
            "lfc_threshold": 1.0,
            "padj_threshold": 0.05,
            "already_logged": "false",
        },
    },
    {
        "name": "Differential Expression (pseudobulk DESeq2, headless)",
        "description": "Reproduce a paper's DEG finding from scRNA-seq per-sample matrices (Level-3)",
        "category": "differential_expression",
        "notebook_path": "notebooks/de_pseudobulk_deseq2.ipynb",
        "local_file": "de_pseudobulk_deseq2.ipynb",
        "compatible_with": "nf-core/scrnaseq",
        "sort_order": 8,
        "parameters": {
            # Comma-separated: nf-core/scrnaseq emits one cell-called matrix per sample and pseudobulk
            # needs all of them, one per column of the genes x samples matrix.
            "counts_paths": "/data/sample_filtered_matrix.h5ad",
            # Must match the bulk template's output name: `_read_reproduction_output` picks the result
            # table by scoring the FILENAME, so a name outside ("finding","result","de_","diff") can
            # lose to another output and be read as the reproduced set.
            "output_path": "/outputs/de_results.csv",
            "test_samples": "",
            "reference_samples": "",
            "block_labels": "",
            "lfc_threshold": 1.0,
            "padj_threshold": 0.05,
            # Which namespace the pseudobulk matrix is keyed by. `mtx_to_h5ad_star.py` moves the
            # ENSEMBL ids INTO the h5ad's index and leaves the symbols in var["gene_symbol"], so this
            # is not the scanpy default it looks like. The Level-3 wiring overrides this per study
            # with the namespace the paper's own finding set uses; the default only covers a
            # hand-run of the template, and symbols are what papers deposit.
            "gene_id_namespace": "symbol",
        },
    },
    {
        "name": "Differential Accessibility (DESeq2, headless)",
        "description": "Reproduce a paper's differential-peak finding from a consensus-peak matrix (Level-3)",
        "category": "differential_accessibility",
        "notebook_path": "notebooks/da_peaks_deseq2.ipynb",
        "local_file": "da_peaks_deseq2.ipynb",
        "compatible_with": "nf-core/atacseq",
        "sort_order": 7,
        "parameters": {
            "counts_path": "/data/consensus_peaks.featureCounts.txt",
            "output_path": "/outputs/da_results.csv",
            "test_samples": "",
            "reference_samples": "",
            # Declared because the driver PASSES it for a paired design. It was absent here and
            # unread by the notebook, so an ATAC/ChIP study whose plan declared matched pairs was
            # analysed unpaired and nothing said so.
            "block_labels": "",
            "lfc_threshold": 1.0,
            "padj_threshold": 0.05,
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

        # Fall back to a local file: the repo-root scripts dir (source checkout), then the
        # package-local dir (shipped in the deployed image).
        basename = template.notebook_path.split("/")[-1]
        for base in (TEMPLATES_DIR, PACKAGE_TEMPLATES_DIR):
            local_file = base / basename
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
