"""Tool catalog (T1) for the conversational assistant (ai_pipeline_run).

Each bioAF capability the agent can call is registered here as a ToolDescriptor: a name, a
human description, a minimal argument schema, a consequence class (read_only | mutating |
spend), the RBAC permission the underlying action requires, and an async handler that does
the real work. The enforcement wrapper (T2, assistant_tool_service) dispatches through this
catalog and never runs a handler until its gates pass.

Phase 1 registers two tools end-to-end: recommend_pipeline (read-only) and launch_run
(spend; v1 stops at the confirmed plan and never POSTs a run). The remaining read tools
(list_experiments, list_samples, list_pipelines, check_status) are a follow-up slice.
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from app.services.recommend_pipeline_service import PipelineRecommendation, RecommendPipelineService

ToolHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    consequence_class: str  # read_only | mutating | spend
    permission: tuple[str, str]  # (resource, action), checked server-side before execution
    args_schema: dict  # minimal JSON-schema subset: {"required": [...], "properties": {...}}
    handler: ToolHandler  # async (session, *, org_id, user_id, arguments) -> dict | None


async def _recommend_pipeline_handler(session, *, org_id, user_id, arguments):
    rec: PipelineRecommendation = await RecommendPipelineService.recommend(
        session, org_id=org_id, experiment_id=arguments["experiment_id"]
    )
    return asdict(rec)


async def _launch_run_handler(session, *, org_id, user_id, arguments):
    """Build the fully-formed launch request. v1 never executes a launch: the spend gate stops
    at the confirmed plan, so the wrapper does not call this on the spend path. It is here so the
    later confirm step has one place that assembles the request."""
    return {
        "experiment_id": arguments["experiment_id"],
        "pipeline_key": arguments["pipeline_key"],
        "parameters": arguments.get("parameters", {}),
        "reference_genome": arguments.get("reference_genome"),
    }


TOOL_CATALOG: dict[str, ToolDescriptor] = {
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
    "launch_run": ToolDescriptor(
        name="launch_run",
        description=(
            "Launch a pipeline run against an experiment. Spends compute, so it is never executed "
            "without an explicit user confirmation of the proposed plan."
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
            },
        },
        handler=_launch_run_handler,
    ),
}


def get_tool(name: str) -> ToolDescriptor | None:
    return TOOL_CATALOG.get(name)


def list_tools() -> list[ToolDescriptor]:
    return list(TOOL_CATALOG.values())
