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

A third record answers a question the names cannot. A sample's FastQC entries are
its read groups times its mates, and the report carries only the product, so a
mate pair and two read groups whose read counts happen to tie look identical
(`#88`). The submitted sheet has one ROW per read group, so counting its rows per
sample factors the product.
"""

from __future__ import annotations

import csv
import io

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample
from app.services.qc.multiqc_registry import Roster, roster_from_emitted
from app.services.sample_sheet_service import SampleSheetService


async def roster_for_run(session: AsyncSession | None, run: PipelineRun) -> Roster | None:
    """The samples this run covered, or None when nothing records them.

    Never raises on a partially-built run: a caller with no session (the parsers'
    own tests) or a run with no id simply gets None, which leaves the report to
    speak for itself.
    """
    read_groups = read_groups_from_snapshot(
        getattr(run, "samplesheet_snapshot_csv", None), getattr(run, "samplesheet_emitted_json", None)
    )

    emitted = roster_from_emitted(getattr(run, "samplesheet_emitted_json", None))
    if emitted:
        return Roster(emitted, "samplesheet", read_groups)

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
    return Roster(names, "run_samples", read_groups) if names else None


def read_groups_from_snapshot(snapshot_csv: str | None, emitted: object) -> dict[str, int]:
    """How many read groups the submitted sheet carried for each sample.

    ``pipeline_runs.samplesheet_snapshot_csv`` is the sheet as submitted with a
    ``bioaf_sample_uid`` column added, one row per read group, so rows per uid
    is read groups per sample. ``samplesheet_emitted_json`` turns a uid back into
    the NAME that row put in the identity column, which is the name the pipeline
    was given and therefore the one FastQC's entries are decorated from.

    Keyed on uid rather than on the name in the sheet because the uid is what
    ``identity_snapshot`` guarantees: a row bioAF could not attribute carries an
    empty uid rather than a borrowed one, and such a row belongs to no sample.

    Empty for every run that carries no snapshot, which is every run launched
    before the column existed, and for a sheet with no identity column at all
    (fetchngs emits an accession list, which has neither header nor names). The
    report is then read on its own, exactly as it was.
    """
    if not snapshot_csv or not isinstance(emitted, list):
        return {}

    parsed = list(csv.reader(io.StringIO(snapshot_csv)))
    if len(parsed) < 2:
        return {}
    header = parsed[0]
    if SampleSheetService.IDENTITY_COLUMN not in header:
        return {}
    at = header.index(SampleSheetService.IDENTITY_COLUMN)

    rows_by_uid: dict[str, int] = {}
    for values in parsed[1:]:
        if at >= len(values):
            continue
        uid = values[at].strip()
        if uid:
            rows_by_uid[uid] = rows_by_uid.get(uid, 0) + 1

    read_groups: dict[str, int] = {}
    for entry in emitted:
        if not isinstance(entry, dict):
            continue
        name, uid = entry.get("name"), entry.get("uuid")
        if isinstance(name, str) and name and isinstance(uid, str) and uid in rows_by_uid:
            read_groups[name] = rows_by_uid[uid]
    return read_groups
