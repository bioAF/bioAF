"""The read group: the thing between a sample and its files.

bioAF's model was sample -> files with nothing in between, so a sample sequenced
over several lanes, or re-sequenced in a top-up, had nowhere to record which unit
a file came from. Three parts of the code improvised around that absence, and
four pipelines in the catalog encode the same axis under four different names
(sarek's `lane`, mag's `run`, taxprofiler's `run_accession`, ampliseq's `run`).

Decision 6 of 2026-08-19: the concept is a **Read Group**, the industry term from
the SAM spec's `@RG`, where `PU` is flowcell.lane.barcode. Every aligner and
GATK's best practices already use it, so a bioinformatician reads it correctly
with no explanation. It cannot be confused with Sample Batch or Sequencing Batch,
which are cohorts ACROSS samples rather than a decomposition of one.

Phase C derives the grouping rather than storing it. A `read_groups` table waits
until something needs to hang metadata on the unit itself, and when it arrives it
gets a nullable `library_id` so a Library level can slot in above without
re-parenting files twice.

The rule that governs the whole thing: everything is OPTIONAL and everything
unknown collapses to ONE implicit group. A lab receiving pre-merged FASTQs from a
CRO, or pulling from a public archive, has no lane at all and must be wholly
unaffected.
"""

from unittest.mock import MagicMock

import pytest

from app.services.read_groups import read_groups_for


def _file(name, lane=None, flowcell=None, accession=None, read_type=None):
    f = MagicMock()
    f.id = abs(hash(name)) % 100000
    f.filename = name
    f.storage_uri = f"gs://bucket/{name}"
    f.tags_json = []
    f.lane = lane
    f.flowcell_id = flowcell
    f.source_run_accession = accession
    f.read_type = read_type
    f.index_sequence = None
    f.deleted_at = None
    return f


class TestOneSampleDecomposedIntoItsReadGroups:
    def test_two_lanes_of_one_flow_cell_are_two_groups(self):
        groups = read_groups_for(
            [
                _file("a_L001_R1.fastq.gz", lane=1, flowcell="HFWFVDMXX", read_type="R1"),
                _file("a_L001_R2.fastq.gz", lane=1, flowcell="HFWFVDMXX", read_type="R2"),
                _file("a_L002_R1.fastq.gz", lane=2, flowcell="HFWFVDMXX", read_type="R1"),
            ]
        )

        assert [(g["flowcell_id"], g["lane"]) for g in groups] == [("HFWFVDMXX", 1), ("HFWFVDMXX", 2)]
        assert [len(g["files"]) for g in groups] == [2, 1]

    def test_one_lane_number_on_two_flow_cells_is_two_groups(self):
        """The collision the axis exists to remove. A lane number alone is not
        an identity: L001 on two flow cells is two different lanes."""
        groups = read_groups_for(
            [
                _file("a_R1.fastq.gz", lane=1, flowcell="HFWFVDMXX", read_type="R1"),
                _file("b_R1.fastq.gz", lane=1, flowcell="HJKLMDMXX", read_type="R1"),
            ]
        )

        assert len(groups) == 2

    def test_sibling_archive_runs_are_separate_groups(self):
        """A fetched FASTQ has no flow cell and no lane, and carries its archive
        run accession instead. That distinguishes sibling runs of one sample
        without pretending to be a lane."""
        groups = read_groups_for(
            [
                _file("SRR111_1.fastq.gz", accession="SRR111", read_type="R1"),
                _file("SRR222_1.fastq.gz", accession="SRR222", read_type="R1"),
            ]
        )

        assert [g["source_run_accession"] for g in groups] == ["SRR111", "SRR222"]

    def test_everything_unknown_collapses_to_one_group(self):
        """A CRO's pre-merged FASTQs. The case that must be wholly
        unaffected."""
        groups = read_groups_for(
            [
                _file("merged_R1.fastq.gz", read_type="R1"),
                _file("merged_R2.fastq.gz", read_type="R2"),
            ]
        )

        assert len(groups) == 1
        assert groups[0]["flowcell_id"] is None
        assert groups[0]["lane"] is None
        assert len(groups[0]["files"]) == 2

    def test_a_sample_with_no_files_has_no_read_groups(self):
        assert read_groups_for([]) == []

    def test_a_deleted_file_belongs_to_no_read_group(self):
        """Deletion retires a file from every working view, and this is one."""
        retired = _file("gone_R1.fastq.gz", lane=1, flowcell="HFWFVDMXX", read_type="R1")
        retired.deleted_at = "2026-08-19"

        assert read_groups_for([retired]) == []


class TestHowAGroupDescribesItself:
    def test_it_is_named_the_way_the_sam_spec_names_one(self):
        """`PU` is flowcell.lane.barcode. bioAF holds the first two and refuses
        the third, so the label carries what it actually knows."""
        groups = read_groups_for([_file("a_R1.fastq.gz", lane=2, flowcell="HFWFVDMXX", read_type="R1")])

        assert groups[0]["label"] == "HFWFVDMXX.2"

    def test_an_archive_run_is_labelled_by_its_accession(self):
        groups = read_groups_for([_file("SRR111_1.fastq.gz", accession="SRR111", read_type="R1")])

        assert groups[0]["label"] == "SRR111"

    def test_a_group_that_knows_nothing_says_so_plainly(self):
        """Not "unknown.0", and not a fabricated lane. The scientist reading
        this has one group because bioAF has one fact: these files came from
        somewhere."""
        groups = read_groups_for([_file("merged_R1.fastq.gz", read_type="R1")])

        assert groups[0]["label"] == "Not recorded"

    def test_it_reports_the_read_types_it_holds(self):
        groups = read_groups_for(
            [
                _file("a_R1.fastq.gz", lane=1, flowcell="HF", read_type="R1"),
                _file("a_R2.fastq.gz", lane=1, flowcell="HF", read_type="R2"),
            ]
        )

        assert groups[0]["read_types"] == ["R1", "R2"]


class TestTheApiOffersThem:
    """Phase C makes the axis visible. It stays DERIVED: nothing is stored, so
    nothing can drift from the files it describes."""

    @pytest.mark.asyncio
    async def test_a_sample_reports_its_read_groups(self, client, session, admin_user, admin_token):
        from app.models.experiment import Experiment
        from app.models.file import File
        from app.models.sample import Sample, sample_files

        exp = Experiment(
            name="RG",
            organization_id=admin_user.organization_id,
            status="fastq_uploaded",
            owner_user_id=admin_user.id,
        )
        session.add(exp)
        await session.flush()
        sample = Sample(experiment_id=exp.id, external_id="RG-1")
        session.add(sample)
        await session.flush()

        for lane, read in ((1, "R1"), (1, "R2"), (2, "R1")):
            f = File(
                organization_id=admin_user.organization_id,
                experiment_id=exp.id,
                filename=f"RG-1_L{lane:03d}_{read}_001.fastq.gz",
                storage_uri=f"gs://bucket/RG-1_L{lane:03d}_{read}_001.fastq.gz",
                file_type="fastq",
                source_type="upload",
                size_bytes=10,
                lane=lane,
                read_type=read,
                flowcell_id="HFWFVDMXX",
            )
            session.add(f)
            await session.flush()
            await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
        await session.flush()
        await session.commit()

        r = await client.get(
            f"/api/samples/{sample.id}/read-groups", headers={"Authorization": f"Bearer {admin_token}"}
        )

        assert r.status_code == 200
        groups = r.json()["read_groups"]
        assert [g["label"] for g in groups] == ["HFWFVDMXX.1", "HFWFVDMXX.2"]
        assert [len(g["files"]) for g in groups] == [2, 1]
