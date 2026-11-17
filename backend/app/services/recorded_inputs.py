"""The input files a pipeline run recorded consuming, as reproduction reads them.

A reproduction repeats the original analysis: it uses the original run's recorded input selection,
ignores files linked to its samples afterward, and never silently replaces or drops an original
input. Two records describe that selection: the ``pipeline_run_input_files`` links (authoritative)
and ``PipelineRun.input_files_json`` (the launcher's copy). "Recorded no inputs" (an empty list) is
a valid selection; a missing, malformed, or contradictory record means the selection is unknown, and
that is refused rather than read as "none" or replaced with today's files.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_input_file import PipelineRunInputFile


async def recorded_input_ids(session: AsyncSession, run: PipelineRun, *, links_required: bool) -> list[int]:
    """The run's recorded input file IDs, or ValidationError when they cannot be established.

    ``links_required``: the launcher always wrote both records (custom pipelines). Otherwise a run
    from before input links were kept may state its inputs in ``input_files_json`` alone.
    """
    linked = list(
        (
            await session.execute(
                select(PipelineRunInputFile.file_id).where(PipelineRunInputFile.pipeline_run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    recorded = run.input_files_json
    if recorded is None:
        raise ValidationError(
            "This run cannot be reproduced exactly: it does not record which input files it used, so bioAF "
            "cannot tell which files the original analysis read. Start a new analysis instead."
        )
    if not isinstance(recorded, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in recorded):
        raise ValidationError("This run cannot be reproduced: its input files were not recorded in a readable form.")
    if len(recorded) != len(set(recorded)):
        raise ValidationError("This run cannot be reproduced: its records disagree about which input files it used.")
    if (linked or links_required) and set(recorded) != set(linked):
        raise ValidationError("This run cannot be reproduced: its records disagree about which input files it used.")
    return sorted(recorded)


async def unavailable_inputs(session: AsyncSession, org_id: int, file_ids: list[int]) -> list[int]:
    """Recorded inputs that no longer exist in ``org_id``, were deleted, or whose bytes were removed."""
    if not file_ids:
        return []
    files = (
        (await session.execute(select(File).where(File.id.in_(file_ids), File.organization_id == org_id)))
        .scalars()
        .all()
    )
    available = {f.id for f in files if f.deleted_at is None and not f.storage_deleted}
    return sorted(set(file_ids) - available)
