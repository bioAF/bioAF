"""Which samples a run covered, for a QC report that cannot say.

MultiQC's FastQC section has one entry per FILE. A pipeline that runs an aligner
publishes a per-sample section, and the roster comes from there because it is
written after lanes are merged and mates paired. A pipeline that runs no aligner
publishes nothing of the kind, and its entry count is three multipliers deep:
mates, lanes and samples.

bioAF does not have to read the report to know. Two records carry the answer,
tried in this order:

1. ``pipeline_runs.samplesheet_emitted_json``, the sheet actually submitted.
   Preferred because those are the names the pipeline was given, and therefore
   the ones FastQC's entries are decorated from.
2. The run's own samples, through ``pipeline_run_samples``. Every run has these,
   including every run that predates the emitted column, which is what makes an
   existing dashboard recoverable rather than merely honest about not knowing.

Which one answered is recorded in ``metric_sources``, so a number on a dashboard
can always be traced to the record it came from.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample
from app.services.qc.multiqc_registry import Roster, roster_from_emitted


async def roster_for_run(session: AsyncSession | None, run: PipelineRun) -> Roster | None:
    """The samples this run covered, or None when nothing records them.

    Never raises on a partially-built run: a caller with no session (the parsers'
    own tests) or a run with no id simply gets None, which leaves the report to
    speak for itself.
    """
    emitted = roster_from_emitted(getattr(run, "samplesheet_emitted_json", None))
    if emitted:
        return Roster(emitted, "samplesheet")

    if session is None or getattr(run, "id", None) is None:
        return None

    result = await session.execute(
        select(Sample.external_id)
        .join(PipelineRunSample, PipelineRunSample.sample_id == Sample.id)
        .where(PipelineRunSample.pipeline_run_id == run.id)
        .where(Sample.external_id.is_not(None))
    )
    names: list[str] = []
    for (name,) in result.all():
        if name and name not in names:
            names.append(name)
    return Roster(names, "run_samples") if names else None
