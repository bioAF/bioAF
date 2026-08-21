"""Which sample an output belongs to, decided by what the run actually emitted.

bioAF matched an output's path against the sample's name AS IT IS IN THE DATABASE
NOW. That holds only while two strings agree, and nothing enforces it: a
scientist accepts a recommended spelling, the sheet says ``SAMPLE_101`` and the
database says ``SAMPLE-101``, and the match finds nothing. The file is then
attributed to no sample at all.

Decision 2 of 2026-08-19 settles it, and settles the architecture around it:

> the UID is stored in the database. Any input or output readable name / emitted
> name is human interface level only. We process on the UID. This is exactly how
> software always works. Human users never see variable names or memory
> addresses.

So the sheet carries the readable name, bioAF matches against the name THAT RUN
EMITTED, and everything resolves internally to a UID. Decision 3 is what makes
that possible: the run's record keeps the sheet as submitted PLUS a UID column,
so the run knows which UID each emitted name stood for.

The constraint that must not be lost: **the UID column never reaches the CSV
submitted to Nextflow.** An undeclared column fails nf-schema's validation of the
whole sheet, so adding it to what runs would break every launch.
"""

import uuid as uuid_pkg
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.pipeline_output_service import PipelineOutputService
from app.services.sample_sheet_service import SampleSheetService


def _sample(sample_id: int, external_id: str, uid: uuid_pkg.UUID | None = None):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    sample.uuid = uid or uuid_pkg.uuid4()
    return sample


def _preview(columns, rows, identity_column="sample"):
    """A preview as ``SampleSheetService.preview`` returns one."""
    csv_lines = [",".join(columns)] + [",".join(row["values"]) for row in rows]
    return {
        "columns": columns,
        "rows": rows,
        "csv": "\r\n".join(csv_lines) + "\r\n",
        "identity_column": identity_column,
    }


class TestTheRunsRecordKeepsWhatItEmitted:
    """Decision 3, at the level it is decided: what the run keeps."""

    def test_the_snapshot_adds_a_uid_column(self):
        sample = _sample(7, "SAMPLE-101")
        preview = _preview(
            ["sample", "fastq_1"],
            [{"sample_id": 7, "external_id": "SAMPLE-101", "values": ["SAMPLE_101", "gs://b/a.fastq.gz"]}],
        )

        snapshot = SampleSheetService.identity_snapshot(preview, [sample])

        header, row = snapshot["csv"].strip().splitlines()
        assert header.split(",")[-1] == "bioaf_sample_uid"
        assert row.split(",")[-1] == str(sample.uuid)

    def test_the_submitted_sheet_is_not_touched(self):
        """The snapshot is an addition to the record, never a rewrite of what
        ran: an undeclared column fails nf-schema for the whole sheet."""
        sample = _sample(7, "SAMPLE-101")
        preview = _preview(
            ["sample", "fastq_1"],
            [{"sample_id": 7, "external_id": "SAMPLE-101", "values": ["SAMPLE_101", "gs://b/a.fastq.gz"]}],
        )
        submitted = preview["csv"]

        SampleSheetService.identity_snapshot(preview, [sample])

        assert preview["csv"] == submitted
        assert "bioaf_sample_uid" not in submitted

    def test_it_records_what_each_emitted_name_stood_for(self):
        """The map the matcher reads. Same computation as the CSV above, so the
        two cannot disagree about what this run emitted."""
        sample = _sample(7, "SAMPLE-101")
        preview = _preview(
            ["sample", "fastq_1"],
            [{"sample_id": 7, "external_id": "SAMPLE-101", "values": ["SAMPLE_101", "gs://b/a.fastq.gz"]}],
        )

        snapshot = SampleSheetService.identity_snapshot(preview, [sample])

        assert snapshot["emitted"] == [{"name": "SAMPLE_101", "uuid": str(sample.uuid), "sample_id": 7}]

    def test_a_sheet_with_no_identity_column_records_nothing_rather_than_guessing(self):
        """fetchngs emits an accession list with no header at all. Reading its
        first column as a name would invent a mapping."""
        snapshot = SampleSheetService.identity_snapshot(
            {"columns": [], "rows": [], "csv": "SRR1234567\nSRR7654321\n", "identity_column": None}, []
        )

        assert snapshot["emitted"] == []
        assert snapshot["csv"] == "SRR1234567\nSRR7654321\n"

    def test_two_rows_of_one_sample_are_one_fact_about_it(self):
        """A sample sequenced twice emits two rows carrying the same name."""
        sample = _sample(7, "GUT_A")
        preview = _preview(
            ["sample", "lane"],
            [
                {"sample_id": 7, "external_id": "GUT_A", "values": ["GUT_A", "1"]},
                {"sample_id": 7, "external_id": "GUT_A", "values": ["GUT_A", "2"]},
            ],
        )

        snapshot = SampleSheetService.identity_snapshot(preview, [sample])

        assert snapshot["emitted"] == [{"name": "GUT_A", "uuid": str(sample.uuid), "sample_id": 7}]
        assert len(snapshot["csv"].strip().splitlines()) == 3

    def test_a_row_naming_no_sample_carries_no_uid(self):
        """A tailored generator's sheet can carry a row bioAF cannot attribute.
        An empty cell is the honest answer; a borrowed UID would be a wrong
        one."""
        preview = _preview(
            ["sample", "fastq_1"],
            [{"sample_id": None, "external_id": None, "values": ["MYSTERY", "gs://b/a.fastq.gz"]}],
        )

        snapshot = SampleSheetService.identity_snapshot(preview, [])

        assert snapshot["csv"].strip().splitlines()[1].split(",")[-1] == ""
        assert snapshot["emitted"] == []


class TestMatchingUsesTheNameTheRunEmitted:
    def test_a_renamed_sample_still_matches_its_own_outputs(self):
        """The divergence this exists to remove: the sheet said SAMPLE_101 and
        the database says SAMPLE-101, so matching the database name found
        nothing and the file was attributed to nobody."""
        emitted = [{"name": "SAMPLE_101", "uuid": str(uuid_pkg.uuid4()), "sample_id": 7}]

        matched = PipelineOutputService._match_samples(
            "SAMPLE_101_1_fastqc.html",
            "gs://bucket/fastqc/SAMPLE_101/SAMPLE_101_1_fastqc.html",
            [("SAMPLE-101", 7)],
            emitted=emitted,
        )

        assert matched == [7]

    def test_the_database_name_still_matches_when_the_run_emitted_nothing(self):
        """Every run launched before the snapshot existed has no record of what
        it emitted, and nothing is reconstructed for it."""
        matched = PipelineOutputService._match_samples(
            "SAMPLE-101.bam", "gs://bucket/star/SAMPLE-101.bam", [("SAMPLE-101", 7)], emitted=None
        )

        assert matched == [7]

    def test_an_emitted_name_belonging_to_another_sample_is_not_taken(self):
        emitted = [
            {"name": "S_ONE", "uuid": str(uuid_pkg.uuid4()), "sample_id": 7},
            {"name": "S_TWO", "uuid": str(uuid_pkg.uuid4()), "sample_id": 8},
        ]

        matched = PipelineOutputService._match_samples(
            "S_TWO.bam", "gs://bucket/star/S_TWO.bam", [("S_ONE", 7), ("S_TWO", 8)], emitted=emitted
        )

        assert matched == [8]

    def test_an_output_naming_no_sample_still_matches_nothing(self):
        """multiqc names nobody, and a wrong link is worse than a missing one."""
        emitted = [{"name": "S_ONE", "uuid": str(uuid_pkg.uuid4()), "sample_id": 7}]

        matched = PipelineOutputService._match_samples(
            "multiqc_report.html", "gs://bucket/multiqc/multiqc_report.html", [("S_ONE", 7)], emitted=emitted
        )

        assert matched == []

    def test_a_longer_emitted_name_is_not_shadowed_by_a_shorter_one(self):
        """SAMPLE-10 must not claim SAMPLE-101's outputs."""
        emitted = [
            {"name": "SAMPLE-10", "uuid": str(uuid_pkg.uuid4()), "sample_id": 7},
            {"name": "SAMPLE-101", "uuid": str(uuid_pkg.uuid4()), "sample_id": 8},
        ]

        matched = PipelineOutputService._match_samples(
            "SAMPLE-101.bam", "gs://bucket/star/SAMPLE-101.bam", [], emitted=emitted
        )

        assert matched == [8]

    def test_an_identity_in_the_path_still_wins(self):
        """Step 5's read side is unchanged: a UID that appears in a path is
        exact, and stays the first route tried."""
        from app.services import asset_identity

        uid = uuid_pkg.uuid4()

        matched = PipelineOutputService._match_samples(
            f"{asset_identity.sheet_spelling(uid)}.bam",
            f"gs://bucket/star/{asset_identity.sheet_spelling(uid)}.bam",
            [],
            sample_uids=[(uid, 9)],
            emitted=[{"name": "OTHER", "uuid": str(uuid_pkg.uuid4()), "sample_id": 7}],
        )

        assert matched == [9]

    def test_the_emitted_name_is_tried_before_the_database_name(self):
        """Both could match different samples, and the run's own record is what
        decides. Otherwise a sample renamed since the run steals another's
        outputs."""
        emitted = [{"name": "SHARED", "uuid": str(uuid_pkg.uuid4()), "sample_id": 8}]

        matched = PipelineOutputService._match_samples(
            "SHARED.bam", "gs://bucket/star/SHARED.bam", [("SHARED", 7)], emitted=emitted
        )

        assert matched == [8]


@pytest_asyncio.fixture
async def world(session):
    org = Organization(name="EmittedOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    user = User(
        email="admin@emitted.test",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    exp = Experiment(name="E", organization_id=org.id, status="fastq_uploaded", owner_user_id=user.id)
    session.add(exp)
    await session.flush()
    sample = Sample(experiment_id=exp.id, external_id="SAMPLE-101")
    session.add(sample)
    await session.flush()
    run = PipelineRun(
        organization_id=org.id,
        experiment_id=exp.id,
        submitted_by_user_id=user.id,
        pipeline_name="nf-core/demo",
        pipeline_version="1.0.0",
        status="completed",
        parameters_json={},
    )
    session.add(run)
    await session.flush()
    session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=sample.id))
    await session.flush()
    await session.commit()
    return {"org": org, "exp": exp, "sample": sample, "run": run}


class TestThroughTheRealRegistrationPath:
    @pytest.mark.asyncio
    async def test_an_output_named_for_the_emitted_spelling_reaches_its_sample(self, session, world):
        """The whole point: the run emitted SAMPLE_101, the database says
        SAMPLE-101, and the output lands on the right sample instead of on
        nobody."""
        run, sample = world["run"], world["sample"]
        run.samplesheet_csv = "sample,fastq_1\r\nSAMPLE_101,gs://b/a.fastq.gz\r\n"
        run.samplesheet_mapping_json = {"values": {}, "bindings": {}}
        run.samplesheet_emitted_json = [{"name": "SAMPLE_101", "uuid": str(sample.uuid), "sample_id": sample.id}]
        await session.flush()

        created = await PipelineOutputService.register_outputs(
            session,
            run,
            [
                {
                    "filename": "SAMPLE_101_1_fastqc.html",
                    "gcs_uri": "gs://bucket/run/fastqc/SAMPLE_101/SAMPLE_101_1_fastqc.html",
                    "size_bytes": 10,
                    "md5_hash": "abc",
                }
            ],
        )
        await session.flush()

        assert len(created) == 1
        rows = (await session.execute(sample_files.select().where(sample_files.c.file_id == created[0].id))).all()
        assert [r.sample_id for r in rows] == [sample.id]

    @pytest.mark.asyncio
    async def test_a_run_with_no_record_of_what_it_emitted_is_unaffected(self, session, world):
        run, sample = world["run"], world["sample"]
        run.samplesheet_emitted_json = None
        await session.flush()

        created = await PipelineOutputService.register_outputs(
            session,
            run,
            [
                {
                    "filename": "SAMPLE-101.bam",
                    "gcs_uri": "gs://bucket/run/star/SAMPLE-101.bam",
                    "size_bytes": 10,
                    "md5_hash": "def",
                }
            ],
        )
        await session.flush()

        rows = (await session.execute(sample_files.select().where(sample_files.c.file_id == created[0].id))).all()
        assert [r.sample_id for r in rows] == [sample.id]
