"""The sheet a launch would submit, produced without launching anything.

Every launch ends with a review step, because a regex match is not proof of the
right file: a reference genome satisfies funcscan's ``fasta`` pattern exactly as
well as the scientist's assembly does. The preview is the only thing standing
between that and a confidently wrong result.

Two properties make it worth trusting.

**It is the same sheet, not a rendering of one.** The table and the raw CSV come
from one pass over the samples, so they cannot disagree, and neither can differ
from what ``generate_sheet`` hands to Nextflow. A preview built by a second code
path would eventually show something the run did not use, which is worse than
showing nothing.

**Every row names its sample**, so a wrongly-resolved cell can be corrected in
place. A row identified only by position is the same hazard as a positional
paste: the correction lands on the wrong sample and the run completes green.
"""

import csv
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _make_file(filename: str):
    f = MagicMock()
    f.filename = filename
    f.storage_uri = f"gs://bucket/{filename}"
    f.tags_json = []
    return f


def _reads(name: str, lane: str = "001") -> list:
    return [
        _make_file(f"{name}_L{lane}_R1_001.fastq.gz"),
        _make_file(f"{name}_L{lane}_R2_001.fastq.gz"),
    ]


def _make_sample(sample_id: int, external_id: str, files=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    return sample


def _preview(pipeline_key, samples, parameters=None, contract=None, sample_values=None):
    return SampleSheetService.preview(
        pipeline_key,
        samples,
        parameters or {},
        contract=contract,
        sample_values=sample_values,
    )


# -- The preview is the sheet the run would use --


def test_the_previewed_csv_is_the_one_the_launch_would_submit():
    """The load-bearing property. A preview built by its own code path would
    eventually show a sheet the run did not use."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    contract = _contract("mag")
    values = {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}

    preview = _preview("nf-core/mag", samples, contract=contract, sample_values=values)
    submitted = SampleSheetService.generate_sheet("nf-core/mag", samples, {}, contract=contract, sample_values=values)

    assert preview["csv"] == submitted


def test_the_table_and_the_raw_csv_carry_the_same_values():
    """The review step shows a table by default and the CSV behind a button.
    They are two renderings of one thing, so a reader can trust either."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    values = {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}

    preview = _preview("nf-core/mag", samples, contract=_contract("mag"), sample_values=values)

    parsed = list(csv.reader(io.StringIO(preview["csv"])))
    assert parsed[0] == preview["columns"]
    assert [r["values"] for r in preview["rows"]] == parsed[1:]


# -- Every row names its sample --


def test_each_row_names_the_sample_it_belongs_to():
    """A cell is corrected in place from this table, so the correction has to
    land on a sample rather than on a row number."""
    samples = [
        _make_sample(7, "GUT_A", files=_reads("GUT_A")),
        _make_sample(3, "SKIN_B", files=_reads("SKIN_B")),
    ]
    values = {
        "7": {"group": "gut", "short_reads_platform": "ILLUMINA"},
        "3": {"group": "skin", "short_reads_platform": "ILLUMINA"},
    }

    preview = _preview("nf-core/mag", samples, contract=_contract("mag"), sample_values=values)

    assert [(r["sample_id"], r["external_id"]) for r in preview["rows"]] == [(7, "GUT_A"), (3, "SKIN_B")]


def test_a_multi_lane_sample_produces_one_row_per_lane_under_the_same_sample():
    """bioAF emits a row per lane. All of them belong to one sample, and an
    edit to that sample's design applies to every one."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A", lane="001") + _reads("GUT_A", lane="002"))]
    values = {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}

    preview = _preview("nf-core/mag", samples, contract=_contract("mag"), sample_values=values)

    assert len(preview["rows"]) == 2
    assert {r["sample_id"] for r in preview["rows"]} == {1}


# -- It shows what the scientist stated --


def test_it_reflects_a_value_the_scientist_supplied():
    """Otherwise the review step would confirm a sheet other than the one about
    to run, which is worse than no review at all."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    values = {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}

    preview = _preview("nf-core/mag", samples, contract=_contract("mag"), sample_values=values)

    row = preview["rows"][0]
    assert row["values"][preview["columns"].index("group")] == "gut"


def test_it_previews_a_sheet_that_is_still_blocked():
    """The review step is not gated on the launch being possible: seeing the
    empty column is how a user understands what the block is about."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    preview = _preview("nf-core/mag", samples, contract=_contract("mag"))

    assert preview["rows"][0]["values"][preview["columns"].index("group")] == ""


# -- Pipelines that do not go through the schema path --


def test_a_hand_written_generator_is_previewed_too():
    """Design section 6: the review step is shown on EVERY launch. rnaseq builds
    its sheet with a tailored generator, and that sheet needs reviewing exactly
    as much as a schema-derived one."""
    samples = [_make_sample(1, "A", files=_reads("A"))]

    preview = _preview("nf-core/rnaseq", samples, parameters={"strandedness": "auto"})

    assert preview["columns"] == ["sample", "fastq_1", "fastq_2", "strandedness"]
    assert preview["csv"] == SampleSheetService.generate_sheet("nf-core/rnaseq", samples, {"strandedness": "auto"})


def test_a_pipeline_with_no_contract_still_previews_its_generic_sheet():
    """No schema means today's behaviour, never a refusal. The preview follows
    the same rule rather than showing nothing."""
    samples = [_make_sample(1, "A", files=_reads("A"))]

    preview = _preview("nf-core/unknown", samples, contract=parse_contract(None))

    assert preview["columns"] == ["sample", "fastq_1", "fastq_2"]
    assert preview["rows"][0]["external_id"] == "A"


def test_a_sheet_that_is_not_a_samplesheet_is_previewed_as_its_own_text():
    """fetchngs takes a list of accessions with no header, not a samplesheet.
    Rendering it as a table would invent a structure it does not have, so the
    preview carries the text and no columns."""
    preview = _preview("nf-core/fetchngs", [], parameters={"accessions": ["SRR123", "SRR456"]})

    assert preview["columns"] == []
    assert preview["rows"] == []
    assert preview["csv"] == "SRR123\nSRR456\n"


# -- What the sheet leaves out, and why (design section 9) --


def test_a_value_the_pipelines_vocabulary_cannot_express_is_reported():
    """sarek accepts XX, XY or NA. A sample recorded 47,XXY is real biology the
    pipeline cannot ingest, so the value is dropped and nf-schema fills its own
    default. Dropping it is right; dropping it silently is not, because the
    scientist then reads a sheet that simply does not mention their sample's sex.
    """
    sample = _make_sample(1, "SAMPLE-1", files=_reads("SAMPLE-1"), donor_source="P1", sex="47,XXY")

    preview = _preview("nf-core/sarek", [sample], contract=_contract("sarek"))

    omission = next(o for o in preview["omissions"] if o["column"] == "sex")
    assert omission["sample_id"] == 1
    assert omission["external_id"] == "SAMPLE-1"
    assert omission["value"] == "47,XXY"
    assert omission["reason"] == "not_in_enum"
    assert omission["allowed_values"] == ["XX", "XY", "NA"]


def test_a_value_the_vocabulary_does_express_is_not_reported():
    sample = _make_sample(1, "SAMPLE-1", files=_reads("SAMPLE-1"), donor_source="P1", sex="XX")

    preview = _preview("nf-core/sarek", [sample], contract=_contract("sarek"))

    assert [o for o in preview["omissions"] if o["column"] == "sex"] == []


def test_a_stated_value_the_pipeline_rejects_is_reported_too():
    """The grid lets a scientist type a value, and a typed value obeys the enum
    like any other. Being told it was dropped is the whole point of typing it."""
    sample = _make_sample(1, "SAMPLE-1", files=_reads("SAMPLE-1"), donor_source="P1")

    preview = _preview(
        "nf-core/sarek",
        [sample],
        contract=_contract("sarek"),
        sample_values={"1": {"sex": "intersex"}},
    )

    omission = next(o for o in preview["omissions"] if o["column"] == "sex")
    assert omission["value"] == "intersex"


def test_one_sample_is_reported_once_however_many_rows_it_has():
    """A sample sequenced over two lanes produces two rows. The sex it cannot
    express is one fact about the sample, not one per row."""
    sample = _make_sample(
        1,
        "SAMPLE-1",
        files=_reads("SAMPLE-1", "001") + _reads("SAMPLE-1", "002"),
        donor_source="P1",
        sex="47,XXY",
    )

    preview = _preview("nf-core/sarek", [sample], contract=_contract("sarek"))

    assert len([o for o in preview["omissions"] if o["column"] == "sex"]) == 1


def test_a_sheet_from_a_hand_written_generator_reports_no_omissions():
    """Nothing is claimed about a sheet bioAF did not build from a contract."""
    samples = [_make_sample(1, "A", files=_reads("A"))]

    preview = _preview("nf-core/rnaseq", samples, parameters={"strandedness": "auto"})

    assert preview["omissions"] == []
