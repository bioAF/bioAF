"""Which samples a run covered, when its QC report cannot say.

`#89` took the roster from the sheet the run submitted, which fixed every run
launched after that column existed and left every earlier one reporting nothing:
a QC report with no aligner section has one FastQC entry per FILE, and with no
roster there is no honest sample count to give.

Those runs are not actually unknown. `pipeline_run_samples` records what every
run was launched on, including all thirty-three that predate the emitted column,
so the answer was in the database the whole time. It is the weaker of the two
records and is tried second: the emitted sheet carries the names the pipeline
was actually given, which is what FastQC's entries are decorated from, while a
sample's `external_id` can have been renamed since the run.
"""

import pytest

from app.services.qc.roster import roster_for_run


async def _run_with_samples(session, admin_user, external_ids, emitted=None):
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun, PipelineRunSample
    from app.models.sample import Sample

    org_id = admin_user.organization_id
    experiment = Experiment(name="Roster", organization_id=org_id, owner_user_id=admin_user.id, status="processing")
    session.add(experiment)
    await session.flush()

    samples = []
    for external_id in external_ids:
        sample = Sample(external_id=external_id, experiment_id=experiment.id, status="registered")
        session.add(sample)
        samples.append(sample)
    await session.flush()

    run = PipelineRun(
        pipeline_name="nf-core/demo",
        status="completed",
        organization_id=org_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        samplesheet_emitted_json=emitted,
    )
    session.add(run)
    await session.flush()
    for sample in samples:
        session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=sample.id))
    await session.flush()
    return run


@pytest.mark.asyncio
async def test_a_run_that_predates_the_emitted_sheet_still_has_a_roster(session, admin_user):
    """The six dashboards `#89` blanked. Every one of them links to its samples."""
    run = await _run_with_samples(session, admin_user, ["SAMPLE-101"], emitted=None)

    roster = await roster_for_run(session, run)

    assert roster is not None
    assert roster.names == ["SAMPLE-101"]
    assert roster.source == "run_samples"


@pytest.mark.asyncio
async def test_the_submitted_sheet_outranks_the_run_link(session, admin_user):
    """The sheet holds the name the pipeline was GIVEN. A sample renamed since
    the run would otherwise silently regroup that run's output."""
    run = await _run_with_samples(
        session,
        admin_user,
        ["RENAMED-SINCE"],
        emitted=[{"name": "AS-SUBMITTED", "uuid": "abc", "sample_id": 1}],
    )

    roster = await roster_for_run(session, run)

    assert roster.names == ["AS-SUBMITTED"]
    assert roster.source == "samplesheet"


@pytest.mark.asyncio
async def test_one_name_per_sample_however_many_lanes(session, admin_user):
    run = await _run_with_samples(session, admin_user, ["SAMPLE-101", "SAMPLE-102"])

    roster = await roster_for_run(session, run)

    assert sorted(roster.names) == ["SAMPLE-101", "SAMPLE-102"]


@pytest.mark.asyncio
async def test_a_run_linked_to_no_samples_has_no_roster(session, admin_user):
    """None, not an empty roster: the report is then left to speak for itself
    rather than being told there are zero samples."""
    run = await _run_with_samples(session, admin_user, [])

    assert await roster_for_run(session, run) is None


@pytest.mark.asyncio
async def test_no_session_is_not_an_error(session, admin_user):
    """The parsers' own tests call extract() without one."""
    run = await _run_with_samples(session, admin_user, ["SAMPLE-101"])

    assert await roster_for_run(None, run) is None
