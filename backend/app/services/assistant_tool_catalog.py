"""Tool catalog (T1) for the conversational assistant (ai_pipeline_run).

Each bioAF capability the agent can call is registered here as a ToolDescriptor: a name, a
human description, a minimal argument schema, a consequence class (read_only | mutating |
spend), the RBAC permission the underlying action requires, and an async handler that does
the real work. The enforcement wrapper (T2, assistant_tool_service) dispatches through this
catalog and never runs a handler until its gates pass.

Registered tools: the read-only discovery tools (list_experiments, list_samples,
list_pipelines, check_status) that let the agent resolve plain-language intent to real
entities, recommend_pipeline (read-only analysis), and launch_run (spend; v1 stops at the
confirmed plan and never POSTs a run). Read-only handlers are org-scoped: they only ever
return data from the conversation's organization.
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from app.services.agent_review_artifact_builder import (
    _load_qc_dashboard_text,
    _load_samples_for_run,
    render_run_markdown,
)
from app.services.experiment_service import ExperimentService
from app.services.nf_core_registry_service import NfCoreRegistryService
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.pipeline_run_service import PipelineRunService
from app.services.qc_dashboard_service import QCDashboardService
from app.services.recommend_pipeline_service import PipelineRecommendation, RecommendPipelineService
from app.services.sample_service import SampleService

ToolHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    consequence_class: str  # read_only | mutating | spend
    permission: tuple[str, str]  # (resource, action), checked server-side before execution
    args_schema: dict  # minimal JSON-schema subset: {"required": [...], "properties": {...}}
    handler: ToolHandler  # async (session, *, org_id, user_id, arguments) -> dict | None


async def _list_experiments_handler(session, *, org_id, user_id, arguments):
    """List the org's experiments so the agent can resolve a plain-language reference to an id."""
    experiments, total = await ExperimentService.list_experiments(
        session,
        org_id,
        page=int(arguments.get("page", 1)),
        page_size=min(int(arguments.get("page_size", 25)), 100),
        status=arguments.get("status"),
        search=arguments.get("search"),
    )
    return {
        "experiments": [
            {
                "id": e.id,
                "name": e.name,
                "status": e.status,
                "description": e.description,
                "sample_count": len(e.samples) if e.samples else 0,
            }
            for e in experiments
        ],
        "total": total,
    }


async def _list_samples_handler(session, *, org_id, user_id, arguments):
    """List an experiment's samples (org-scoped). Surfaces the fields recommend_pipeline reasons on."""
    experiment_id = arguments["experiment_id"]
    experiment = await ExperimentService.get_experiment(session, experiment_id, org_id)
    if experiment is None:
        raise LookupError(f"experiment {experiment_id} not found in org {org_id}")
    samples = await SampleService.list_samples(session, experiment_id)
    return {
        "experiment_id": experiment_id,
        "samples": [
            {
                "id": s.id,
                "external_id": s.external_id,
                "organism": s.organism,
                "molecule_type": s.molecule_type,
                "library_prep_method": s.library_prep_method,
                "assay": s.assay,
                "status": s.status,
            }
            for s in samples
        ],
    }


async def _list_pipelines_handler(session, *, org_id, user_id, arguments):
    """List the org's enabled pipeline catalog entries. Pure read (no built-in seeding)."""
    enriched = await PipelineCatalogService.list_pipelines(session, org_id)
    return {
        "pipelines": [
            {
                "pipeline_key": entry.pipeline_key,
                "name": entry.name,
                "version": entry.version,
                "source_type": entry.source_type,
            }
            for entry, _username, _latest_version in enriched
        ],
    }


async def _check_status_handler(session, *, org_id, user_id, arguments):
    """Report the status of a pipeline run (org-scoped)."""
    run_id = arguments["run_id"]
    run = await PipelineRunService.get_run(session, run_id, org_id)
    if run is None:
        raise LookupError(f"pipeline run {run_id} not found in org {org_id}")
    return {
        "id": run.id,
        "status": run.status,
        "pipeline_name": run.pipeline_name,
        "pipeline_version": run.pipeline_version,
        "experiment_id": run.experiment_id,
        "progress": run.progress_json,
        "error_message": run.error_message,
        "failure_reason": run.failure_reason,
    }


async def _get_metrics_handler(session, *, org_id, user_id, arguments):
    """Return the QC metrics for a pipeline run (org-scoped, read-only). Reads the already-generated
    QC dashboard rather than computing one (generation reads the run's output files and is a separate,
    heavier action). When no ready dashboard exists, reports metrics_available=False with a reason so
    the agent can tell the user QC is not ready yet instead of failing the turn."""
    run_id = arguments["run_id"]
    run = await PipelineRunService.get_run(session, run_id, org_id)
    if run is None:
        raise LookupError(f"pipeline run {run_id} not found in org {org_id}")
    dashboard = await QCDashboardService.get_dashboard_by_run(session, org_id, run_id)
    if dashboard is None or dashboard.status != "ready":
        return {
            "run_id": run_id,
            "run_status": run.status,
            "metrics_available": False,
            "reason": (
                "No QC dashboard has been generated for this run yet; QC is produced as a separate "
                "step after the pipeline completes."
                if dashboard is None
                else f"The QC dashboard for this run is not ready (status: {dashboard.status})."
            ),
        }
    metrics = dashboard.metrics_json or {}
    return {
        "run_id": run_id,
        "run_status": run.status,
        "metrics_available": True,
        "quality_rating": metrics.get("quality_rating"),
        "summary": dashboard.summary_text,
        "metrics": metrics,
    }


async def _explain_results_handler(session, *, org_id, user_id, arguments):
    """Assemble the interpretation-ready results context for a run (org-scoped, read-only) so the
    assistant can narrate it in plain language. Reuses the agent-review artifact assembly (U3): run
    metadata + parameters + samples + QC dashboard text + errors, rendered as Markdown. The tool itself
    makes NO LLM call; the assistant's own loop model turns this context into the conversational
    explanation. Robust to run state: a still-running, failed, or QC-less run still returns its
    context rather than erroring."""
    run_id = arguments["run_id"]
    run = await PipelineRunService.get_run(session, run_id, org_id)
    if run is None:
        raise LookupError(f"pipeline run {run_id} not found in org {org_id}")
    dashboard = await QCDashboardService.get_dashboard_by_run(session, org_id, run_id)
    qc_text = await _load_qc_dashboard_text(session, run_id)
    samples = await _load_samples_for_run(session, run_id)
    markdown = render_run_markdown(run=run, samples=samples, qc_report_content=qc_text)
    quality_rating = (dashboard.metrics_json or {}).get("quality_rating") if dashboard else None
    return {
        "run_id": run_id,
        "run_status": run.status,
        "quality_rating": quality_rating,
        "qc_available": dashboard is not None and dashboard.status == "ready",
        "results_markdown": markdown,
    }


async def _install_handler(session, *, org_id, user_id, arguments):
    """Install an nf-core pipeline into the org's catalog. Mutating: only runs after confirmation.
    Accepts a bare name ('scrnaseq') or a pipeline_key ('nf-core/scrnaseq'); defaults to the latest
    available version. Idempotent: a pipeline already installed returns its key, not an error."""
    name = arguments["name"]
    if name.startswith("nf-core/"):
        name = name[len("nf-core/") :]
    version = arguments.get("version")
    if not version:
        versions = await NfCoreRegistryService.get_pipeline_versions(session, name)
        if not versions:
            raise ValueError(f"No versions found for nf-core/{name}; check the name or refresh the registry.")
        version = versions[0].get("tag_name")
    try:
        entry = await NfCoreRegistryService.install_pipeline(session, org_id, user_id, name, version)
    except NfCoreRegistryService.PipelineAlreadyInstalledError:
        return {"pipeline_key": f"nf-core/{name}", "already_installed": True}
    return {"pipeline_key": entry.pipeline_key, "name": entry.name, "version": entry.version}


async def _recommend_pipeline_handler(session, *, org_id, user_id, arguments):
    rec: PipelineRecommendation = await RecommendPipelineService.recommend(
        session, org_id=org_id, experiment_id=arguments["experiment_id"]
    )
    return asdict(rec)


async def _launch_run_handler(session, *, org_id, user_id, arguments):
    """Build the fully-formed launch request (a valid PipelineRunCreate). v1 stops at the confirmed
    plan and never POSTs a run. For a fetch-style pipeline (nf-core/fetchngs), the data is pulled
    from accessions rather than per-sample files, so an optional ``accessions`` list is folded into
    ``parameters`` (bioAF has no top-level accessions field; parameters is the carrier)."""
    parameters = dict(arguments.get("parameters") or {})
    accessions = arguments.get("accessions")
    if accessions:
        parameters["accessions"] = accessions
    return {
        "experiment_id": arguments["experiment_id"],
        "pipeline_key": arguments["pipeline_key"],
        "parameters": parameters,
        "reference_genome": arguments.get("reference_genome"),
    }


TOOL_CATALOG: dict[str, ToolDescriptor] = {
    "list_experiments": ToolDescriptor(
        name="list_experiments",
        description=(
            "List the organization's experiments (optionally filtered by status or a search term) so "
            "you can resolve a plain-language reference to a specific experiment id. Read-only."
        ),
        consequence_class="read_only",
        permission=("experiments", "view"),
        args_schema={
            "required": [],
            "properties": {
                "status": {"type": "string"},
                "search": {"type": "string"},
                "page": {"type": "integer"},
                "page_size": {"type": "integer"},
            },
        },
        handler=_list_experiments_handler,
    ),
    "list_samples": ToolDescriptor(
        name="list_samples",
        description=(
            "List the samples of an experiment, including organism, molecule type, library prep, and "
            "the assay field, so you can characterize what the experiment contains. Read-only."
        ),
        consequence_class="read_only",
        permission=("samples", "view"),
        args_schema={"required": ["experiment_id"], "properties": {"experiment_id": {"type": "integer"}}},
        handler=_list_samples_handler,
    ),
    "list_pipelines": ToolDescriptor(
        name="list_pipelines",
        description="List the pipelines installed in the organization's catalog. Read-only.",
        consequence_class="read_only",
        permission=("pipelines", "view"),
        args_schema={"required": [], "properties": {}},
        handler=_list_pipelines_handler,
    ),
    "check_status": ToolDescriptor(
        name="check_status",
        description="Report the current status (and progress) of a pipeline run by id. Read-only.",
        consequence_class="read_only",
        permission=("pipelines", "view"),
        args_schema={"required": ["run_id"], "properties": {"run_id": {"type": "integer"}}},
        handler=_check_status_handler,
    ),
    "get_metrics": ToolDescriptor(
        name="get_metrics",
        description=(
            "Get the QC metrics for a completed pipeline run by id (e.g. cell count, mapping rate, "
            "quality rating). Reads the run's QC dashboard; if QC has not been generated yet it says "
            "so. Read-only."
        ),
        consequence_class="read_only",
        # Results reading: experiments:view is a RESULTS_VIEW_PERMISSIONS member, so the same users
        # who can see a run's Results tab (incl. the bench persona) can ask the assistant about it.
        permission=("experiments", "view"),
        args_schema={"required": ["run_id"], "properties": {"run_id": {"type": "integer"}}},
        handler=_get_metrics_handler,
    ),
    "explain_results": ToolDescriptor(
        name="explain_results",
        description=(
            "Get the full results context for a pipeline run by id (status, parameters, samples, QC "
            "report, and any errors) so you can explain in plain language what the run produced and "
            "what its QC means. Use this when the user asks what their results mean or how a run went. "
            "Read-only."
        ),
        consequence_class="read_only",
        permission=("experiments", "view"),
        args_schema={"required": ["run_id"], "properties": {"run_id": {"type": "integer"}}},
        handler=_explain_results_handler,
    ),
    "recommend_pipeline": ToolDescriptor(
        name="recommend_pipeline",
        description=(
            "Recommend an nf-core pipeline and reference genome for an experiment, based on its "
            "samples (assay and organism). Pure analysis; changes nothing."
        ),
        consequence_class="read_only",
        permission=("experiments", "view"),
        args_schema={"required": ["experiment_id"], "properties": {"experiment_id": {"type": "integer"}}},
        handler=_recommend_pipeline_handler,
    ),
    "install": ToolDescriptor(
        name="install",
        description=(
            "Install an nf-core pipeline into the organization's catalog so it can be run. Pass the "
            "pipeline name (e.g. 'scrnaseq' or 'nf-core/scrnaseq'); the latest version is used unless "
            "one is given. Mutating: changes the catalog, so it is never executed without an explicit "
            "user confirmation of the proposed plan."
        ),
        consequence_class="mutating",
        # Mirrors the real POST /api/pipelines/registry/{name}/install guard.
        permission=("pipelines", "create"),
        args_schema={
            "required": ["name"],
            "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
        },
        handler=_install_handler,
    ),
    "launch_run": ToolDescriptor(
        name="launch_run",
        description=(
            "Launch a pipeline run against an experiment. Spends compute, so it is never executed "
            "without an explicit user confirmation of the proposed plan. To import data by accession, "
            "launch nf-core/fetchngs with the 'accessions' list (e.g. GEO/SRA/ENA ids); fetchngs pulls "
            "the data itself, so per-sample files are not required."
        ),
        consequence_class="spend",
        # Mirrors the real POST /api/pipeline-runs guard: require_permission("pipelines", "launch").
        permission=("pipelines", "launch"),
        args_schema={
            "required": ["experiment_id", "pipeline_key"],
            "properties": {
                "experiment_id": {"type": "integer"},
                "pipeline_key": {"type": "string"},
                "parameters": {"type": "object"},
                "reference_genome": {"type": "string"},
                "accessions": {"type": "array"},
            },
        },
        handler=_launch_run_handler,
    ),
}


def get_tool(name: str) -> ToolDescriptor | None:
    return TOOL_CATALOG.get(name)


def list_tools() -> list[ToolDescriptor]:
    return list(TOOL_CATALOG.values())
