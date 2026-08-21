"""Regression lock: the hand-written generators, byte for byte.

Schema-driven generation replaces the fixed generic fallback, and nothing else.
The four tailored generators and fetchngs build sheets a schema cannot describe
(chipseq pairs each IP sample with a detected control and labels its antibody;
fetchngs emits accessions, not a samplesheet), so they keep priority and must
produce exactly what they produce today.

Demo runs 17, 22 and 24 on the dev instance were launched from these generators.
If a byte moves here, a pipeline that works today has changed shape.
"""

from unittest.mock import MagicMock

from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

# A contract that WOULD produce a different sheet if it were ever consulted, so
# these tests fail loudly if a hand-written generator starts deferring to it.
_INTRUSIVE_CONTRACT = parse_contract(
    {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["totally_different"],
            "properties": {
                "totally_different": {"type": "string"},
                "fastq_1": {"pattern": r"^\S+\.fastq\.gz$"},
            },
        },
    }
)


def _make_sample(sample_id: int, external_id: str, prep_notes: str = ""):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    sample.prep_notes = prep_notes
    sample._input_files = []
    return sample


def _paths(*ids):
    return {"input_paths": {str(i): [f"/data/S{i}_R1.fastq.gz", f"/data/S{i}_R2.fastq.gz"] for i in ids}}


class TestHandwrittenGeneratorsAreUnchanged:
    def test_scrnaseq_header_and_rows(self):
        csv_text = SampleSheetService.generate_sheet(
            "nf-core/scrnaseq", [_make_sample(1, "SAMPLE_A")], {**_paths(1), "expected_cells": 5000}
        )

        assert csv_text.splitlines()[0] == "sample,fastq_1,fastq_2,expected_cells"
        assert csv_text.splitlines()[1] == "SAMPLE_A,/data/S1_R1.fastq.gz,/data/S1_R2.fastq.gz,5000"

    def test_rnaseq_header_and_rows(self):
        csv_text = SampleSheetService.generate_sheet(
            "nf-core/rnaseq", [_make_sample(1, "RNA_1")], {**_paths(1), "strandedness": "reverse"}
        )

        assert csv_text.splitlines()[0] == "sample,fastq_1,fastq_2,strandedness"
        assert csv_text.splitlines()[1] == "RNA_1,/data/S1_R1.fastq.gz,/data/S1_R2.fastq.gz,reverse"

    def test_chipseq_keeps_its_control_and_antibody_logic(self):
        """The logic a schema cannot express: the input sample is detected from
        its metadata and every IP sample points at it."""
        samples = [
            _make_sample(1, "H3K4me3_rep1", prep_notes="H3K4me3 ChIP-seq"),
            _make_sample(2, "INPUT_1", prep_notes="Input control"),
        ]
        csv_text = SampleSheetService.generate_sheet("nf-core/chipseq", samples, _paths(1, 2))

        rows = csv_text.strip().splitlines()
        assert rows[0] == "sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate"
        assert rows[1].endswith(",1,H3K4me3,INPUT_1,1")
        assert rows[2].endswith(",1,,,")

    def test_atacseq_header_and_replicate(self):
        csv_text = SampleSheetService.generate_sheet("nf-core/atacseq", [_make_sample(1, "ATAC_1")], _paths(1))

        assert csv_text.splitlines()[0] == "sample,fastq_1,fastq_2,replicate"
        assert csv_text.splitlines()[1] == "ATAC_1,/data/S1_R1.fastq.gz,/data/S1_R2.fastq.gz,1"

    def test_fetchngs_still_emits_an_accession_list(self):
        csv_text = SampleSheetService.generate_sheet("nf-core/fetchngs", [], {"accessions": ["SRR1", "SRR2"]})

        assert csv_text == "SRR1\nSRR2\n"


class TestAContractNeverOverridesAHandwrittenGenerator:
    """Passing a contract must change nothing for these five."""

    def test_scrnaseq_ignores_a_contract(self):
        args = ("nf-core/scrnaseq", [_make_sample(1, "S")], _paths(1))
        assert SampleSheetService.generate_sheet(*args) == SampleSheetService.generate_sheet(
            *args, contract=_INTRUSIVE_CONTRACT
        )

    def test_rnaseq_ignores_a_contract(self):
        args = ("nf-core/rnaseq", [_make_sample(1, "S")], _paths(1))
        assert SampleSheetService.generate_sheet(*args) == SampleSheetService.generate_sheet(
            *args, contract=_INTRUSIVE_CONTRACT
        )

    def test_chipseq_ignores_a_contract(self):
        args = ("nf-core/chipseq", [_make_sample(1, "S")], _paths(1))
        assert SampleSheetService.generate_sheet(*args) == SampleSheetService.generate_sheet(
            *args, contract=_INTRUSIVE_CONTRACT
        )

    def test_atacseq_ignores_a_contract(self):
        args = ("nf-core/atacseq", [_make_sample(1, "S")], _paths(1))
        assert SampleSheetService.generate_sheet(*args) == SampleSheetService.generate_sheet(
            *args, contract=_INTRUSIVE_CONTRACT
        )

    def test_fetchngs_ignores_a_contract(self):
        args = ("nf-core/fetchngs", [], {"accessions": ["SRR1"]})
        assert SampleSheetService.generate_sheet(*args) == SampleSheetService.generate_sheet(
            *args, contract=_INTRUSIVE_CONTRACT
        )

    def test_all_five_are_reported_as_handwritten(self):
        for key in ("nf-core/scrnaseq", "nf-core/rnaseq", "nf-core/chipseq", "nf-core/atacseq", "nf-core/fetchngs"):
            assert SampleSheetService.has_handwritten_generator(key) is True

    def test_an_arbitrary_pipeline_is_not_handwritten(self):
        for key in ("nf-core/sarek", "nf-core/mag", "nf-core/funcscan", "nf-core/demo"):
            assert SampleSheetService.has_handwritten_generator(key) is False


class TestNoContractMeansTodaysBehavior:
    def test_a_pipeline_with_no_schema_gets_the_generic_sheet(self):
        """The 18 pipelines publishing no schema_input.json must keep working
        exactly as they do now: refusing them on ignorance would be a regression."""
        samples = [_make_sample(1, "S1")]

        assert SampleSheetService.generate_sheet(
            "nf-core/eager", samples, _paths(1)
        ) == SampleSheetService.generate_generic_sheet(samples, _paths(1))

    def test_an_empty_contract_gets_the_generic_sheet(self):
        samples = [_make_sample(1, "S1")]

        assert SampleSheetService.generate_sheet(
            "nf-core/eager", samples, _paths(1), contract=parse_contract(None)
        ) == SampleSheetService.generate_generic_sheet(samples, _paths(1))
