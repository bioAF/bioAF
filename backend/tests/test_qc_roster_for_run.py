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

The roster also carries how many READ GROUPS each sample was submitted over,
read from the snapshot of the sheet itself. That is a different question from
which samples there were, and it is the one that tells a mate pair from two read
groups whose read counts happen to tie (`#88`).
"""

import pytest

from app.services.qc.roster import read_groups_from_snapshot, roster_for_run


async def _run_with_samples(session, admin_user, external_ids, emitted=None, snapshot_csv=None):
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
        samplesheet_snapshot_csv=snapshot_csv,
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


# --------------------------------------------------------------------------
# How many read groups each sample was submitted over
# --------------------------------------------------------------------------
#
# A QC report cannot say. FastQC has one entry per FILE, and the files of a
# sample are its read groups times its mates, a product whose factors the report
# does not carry. bioAF wrote the sheet, one row per read group, and kept it with
# a `bioaf_sample_uid` column naming the sample each row belongs to.

_SNAPSHOT = """sample,fastq_1,fastq_2,bioaf_sample_uid
SAMPLE-101,L1_R1.fq.gz,L1_R2.fq.gz,uid-101
SAMPLE-101,L2_R1.fq.gz,L2_R2.fq.gz,uid-101
SAMPLE-102,L1_R1.fq.gz,L1_R2.fq.gz,uid-102
"""

_EMITTED = [
    {"name": "SAMPLE-101", "uuid": "uid-101", "sample_id": 1},
    {"name": "SAMPLE-102", "uuid": "uid-102", "sample_id": 2},
]


def test_rows_per_sample_are_that_sample_read_groups():
    assert read_groups_from_snapshot(_SNAPSHOT, _EMITTED) == {"SAMPLE-101": 2, "SAMPLE-102": 1}


def test_the_count_is_keyed_on_the_name_the_sheet_emitted():
    """Not on the name the sample carries today. FastQC's entries are decorated
    from what the pipeline was given, which is the same name the identity column
    held, so a sample renamed since the run still matches."""
    emitted = [{"name": "AS-SUBMITTED", "uuid": "uid-101", "sample_id": 1}]

    assert read_groups_from_snapshot(_SNAPSHOT, emitted) == {"AS-SUBMITTED": 2}


def test_a_run_with_no_snapshot_has_no_counts():
    """Every run launched before the snapshot existed. The report is then read on
    its own, exactly as it was."""
    assert read_groups_from_snapshot(None, _EMITTED) == {}
    assert read_groups_from_snapshot("", _EMITTED) == {}


def test_a_snapshot_without_the_identity_column_yields_no_counts():
    """A sheet bioAF could not attribute row by row (fetchngs' accession list,
    a generator that emitted no name) says nothing about read groups, and a row
    count with nothing to key it on is not an answer."""
    assert read_groups_from_snapshot("accession\nSRR1234567\n", _EMITTED) == {}


def test_a_row_bioaf_could_not_attribute_is_not_counted():
    """`identity_snapshot` writes an EMPTY uid rather than a borrowed one. Such a
    row belongs to no sample, so it inflates none."""
    snapshot = "sample,bioaf_sample_uid\nSAMPLE-101,uid-101\nSOMETHING,\n"

    assert read_groups_from_snapshot(snapshot, _EMITTED) == {"SAMPLE-101": 1}


@pytest.mark.asyncio
async def test_the_roster_carries_the_read_group_counts(session, admin_user):
    run = await _run_with_samples(
        session, admin_user, ["SAMPLE-101", "SAMPLE-102"], emitted=_EMITTED, snapshot_csv=_SNAPSHOT
    )

    roster = await roster_for_run(session, run)

    assert roster.read_groups == {"SAMPLE-101": 2, "SAMPLE-102": 1}


@pytest.mark.asyncio
async def test_the_counts_survive_a_roster_that_fell_back_to_the_run_links(session, admin_user):
    """The names and the read groups are two records answering two questions. A
    run with no emitted sheet has no read groups to report, and its names still
    come from what it was launched on."""
    run = await _run_with_samples(session, admin_user, ["SAMPLE-101"], emitted=None, snapshot_csv=_SNAPSHOT)

    roster = await roster_for_run(session, run)

    assert roster.names == ["SAMPLE-101"]
    assert roster.source == "run_samples"
    assert roster.read_groups == {}


def test_the_record_a_real_launch_writes_is_the_record_this_reads():
    """The seam. `identity_snapshot` is what a launch stores, and its two halves
    are read back here as one fact: the CSV holds a row per read group, the
    emitted list holds one entry per sample, and the uid column joins them.

    Written against the producer rather than a hand-typed CSV so that a change to
    what a launch records breaks here, where the depth depends on it, instead of
    quietly returning no counts.
    """
    import uuid as uuid_pkg
    from unittest.mock import MagicMock

    from app.services.sample_sheet_service import SampleSheetService

    sample = MagicMock()
    sample.id = 7
    sample.external_id = "SAMPLE-101"
    sample.uuid = uuid_pkg.uuid4()

    rows = [
        {"sample_id": 7, "external_id": "SAMPLE-101", "values": ["SAMPLE_101", "1"]},
        {"sample_id": 7, "external_id": "SAMPLE-101", "values": ["SAMPLE_101", "2"]},
    ]
    preview = {
        "columns": ["sample", "lane"],
        "rows": rows,
        "csv": "sample,lane\r\nSAMPLE_101,1\r\nSAMPLE_101,2\r\n",
        "identity_column": "sample",
    }

    snapshot = SampleSheetService.identity_snapshot(preview, [sample])

    # Two read groups, under the name the sheet emitted rather than the one the
    # sample carries.
    assert read_groups_from_snapshot(snapshot["csv"], snapshot["emitted"]) == {"SAMPLE_101": 2}
