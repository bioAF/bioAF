"""The typed inputs a samplesheet is built from.

Preflight and launch used to hang a transient ``_input_files`` list on each ORM
sample, and the sheet helpers read it. When it was absent, or not a list, they
silently fell back to every linked file with a storage URI, deleted files and a
previous run's outputs included. Nothing in production omitted it, which is why
it never showed; the fallback was still the rule.

Now a sheet is built from ``SheetSampleInput`` records whose eligible files are a
required argument. Omitting them, or passing ``None``, is a programming error.
An explicitly empty collection is a real answer and stays empty, whatever the
ORM sample still has attached.
"""

from types import SimpleNamespace

import pytest

from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_inputs import SheetFileInput, SheetSampleInput


def _file(file_id, name, **kw):
    attrs = {
        "id": file_id,
        "filename": name,
        "storage_uri": f"gs://b/{name}",
        "file_type": "fastq",
        "tags_json": [],
        "lane": None,
        "read_type": None,
        "flowcell_id": None,
        "source_run_accession": None,
        "deleted_at": None,
        "source_type": "upload",
    }
    attrs.update(kw)
    return SimpleNamespace(**attrs)


class _Untouchable:
    """A sample whose linked files must never be read: building inputs from it proves they were not."""

    id = 5
    uuid = "a8f1c2de-0000-4000-8000-000000000005"
    external_id = "S5"
    organism = "Homo sapiens"
    tissue_type = None
    donor_source = None
    sex = None
    treatment_condition = None
    chemistry_version = None
    molecule_type = None
    library_prep_method = None
    assay = None
    library_layout = None
    prep_notes = None
    custom_fields: list = []

    @property
    def files(self):
        raise AssertionError("the sheet read the sample's linked files")


def _stale_sample():
    """An ORM-shaped sample still carrying a deleted file and a previous run's output."""
    return SimpleNamespace(
        id=7,
        uuid=None,
        external_id="S7",
        organism=None,
        custom_fields=[],
        files=[
            _file(1, "S7_R1_001.fastq.gz", deleted_at="2026-01-01"),
            _file(2, "S7_R2_001.fastq.gz", source_type="pipeline_output"),
        ],
    )


class TestEligibleFilesAreRequired:
    def test_omitting_them_is_refused(self):
        with pytest.raises(TypeError):
            SheetSampleInput.from_sample(_stale_sample())  # type: ignore[call-arg]

    def test_none_is_refused(self):
        with pytest.raises(TypeError):
            SheetSampleInput.from_sample(_stale_sample(), None)  # type: ignore[arg-type]

    def test_an_explicit_empty_set_stays_empty(self):
        sample = SheetSampleInput.from_sample(_stale_sample(), [])

        assert sample.input_files == ()
        sheet = SampleSheetService.generate_rnaseq_sheet([sample], {})
        assert "S7_R1" not in sheet
        assert "S7_R2" not in sheet

    def test_the_linked_files_are_never_consulted(self):
        sample = SheetSampleInput.from_sample(_Untouchable(), [_file(9, "S5_R1_001.fastq.gz")])

        sheet = SampleSheetService.generate_rnaseq_sheet([sample], {})

        assert "gs://b/S5_R1_001.fastq.gz" in sheet

    def test_a_sheet_refuses_a_sample_that_is_not_a_typed_input(self):
        legacy = _stale_sample()
        legacy._input_files = []
        with pytest.raises(TypeError):
            SampleSheetService.generate_rnaseq_sheet([legacy], {})


class TestSnapshots:
    def test_changing_the_callers_list_afterwards_changes_nothing(self):
        files = [_file(1, "S5_R1_001.fastq.gz")]
        sample = SheetSampleInput.from_sample(_Untouchable(), files)

        files.append(_file(2, "S5_R2_001.fastq.gz"))
        files[0].storage_uri = "gs://elsewhere/x.fastq.gz"

        assert [f.id for f in sample.input_files] == [1]
        assert sample.input_files[0].storage_uri == "gs://b/S5_R1_001.fastq.gz"

    def test_records_cannot_be_changed_in_place(self):
        sample = SheetSampleInput.from_sample(_Untouchable(), [])
        with pytest.raises(AttributeError):
            sample.external_id = "renamed"  # type: ignore[misc]

    def test_the_file_order_given_is_kept(self):
        files = [_file(3, "b.fastq.gz"), _file(1, "a.fastq.gz"), _file(2, "c.fastq.gz")]
        sample = SheetSampleInput.from_sample(_Untouchable(), files)

        assert [f.id for f in sample.input_files] == [3, 1, 2]

    def test_identity_facts_and_legacy_tags_are_carried(self):
        f = _file(4, "x.fastq.gz", lane=2, read_type="R1", flowcell_id="HAAA", source_run_accession="SRR1")
        f.tags_json = ["lane:002", 7]

        snap = SheetFileInput.from_file(f)

        assert (snap.lane, snap.read_type, snap.flowcell_id, snap.source_run_accession) == (2, "R1", "HAAA", "SRR1")
        assert snap.tags_json == ("lane:002",)

    def test_custom_fields_are_carried_by_name(self):
        orm = _stale_sample()
        orm.custom_fields = [SimpleNamespace(field_name="slide", field_value="S-12")]

        sample = SheetSampleInput.from_sample(orm, [])

        assert [(c.field_name, c.field_value) for c in sample.custom_fields] == [("slide", "S-12")]
