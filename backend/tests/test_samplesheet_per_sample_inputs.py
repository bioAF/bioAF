"""What the entry grid has to ask a scientist for, and how to render each answer.

bioAF blocks a launch when a required column has no source. The block names the
column and the samples, which is enough to explain a refusal and not enough to
collect an answer: a grid also needs to know whether the column takes a file,
whether the schema constrains it to a list, and what the pipeline says the column
is for.

This is that description, derived from the SAME gap computation the block uses.
One computation, two renderings: a block and a form. Two computations would drift,
and a grid that asks for a column the check does not block on (or misses one it
does) is worse than no grid.

Two rules from the design are pinned here because they are easy to lose:

**A pipeline's enum constrains a PIPELINE PARAMETER, never a field recorded on the
sample.** rnastructurome's `condition` is an rf-norm chemistry value, so its three
legal values are the whole truth and belong in a dropdown. raredisease's `sex` is
a PED code (`0/1/2/other`) and is emphatically NOT a vocabulary for sex; letting
it constrain the sample record would write a false biological model into the LIMS.

**bioAF explains a column only in the pipeline's own words.** Most nf-core
schemas carry no `description` at all, and the honest answer there is to show the
column name and the schema's format hint rather than to invent a meaning. A
hand-written per-column glossary is the guessing this project removed everywhere
else.
"""

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


def _reads(name: str) -> list:
    return [_make_file(f"{name}_R1_001.fastq.gz"), _make_file(f"{name}_R2_001.fastq.gz")]


def _make_sample(sample_id: int, external_id: str, files=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    return sample


def _inputs(contract, samples, parameters=None, sample_values=None):
    return SampleSheetService.per_sample_inputs(contract, samples, parameters or {}, sample_values=sample_values)


def _by_name(specs) -> dict:
    return {s["name"]: s for s in specs}


# -- Which columns the grid asks for --


def test_it_asks_only_for_the_columns_that_actually_block():
    """rnastructurome requires sample, fastq_1, sample_group, condition and
    replicate. bioAF resolves the first two, so asking for them would be busywork
    that also invites a scientist to overwrite a correct value."""
    samples = [_make_sample(1, "RNA_A", files=_reads("RNA_A"))]

    names = [s["name"] for s in _inputs(_contract("rnastructurome"), samples)]

    assert set(names) == {"sample_group", "condition", "replicate"}


def test_it_asks_for_nothing_when_the_pipeline_is_already_satisfied():
    """The entry step is shown only when there is something to enter, so a
    pipeline bioAF can fully source must produce an empty list rather than an
    empty grid."""
    samples = [_make_sample(1, "A", files=_reads("A"))]

    assert _inputs(_contract("demo"), samples) == []


def test_an_answered_column_drops_out_of_the_list():
    """The grid shrinks as it is filled. A column that still appears after being
    answered would make the step impossible to finish."""
    samples = [_make_sample(1, "RNA_A", files=_reads("RNA_A"))]
    values = {"1": {"condition": "treated", "sample_group": "g1", "replicate": "1"}}

    assert _inputs(_contract("rnastructurome"), samples, sample_values=values) == []


def test_it_asks_for_the_case_id_bioaf_cannot_source():
    """raredisease's case_id is the headline column of this whole step: a real
    required field that names nothing bioAF holds."""
    samples = [_make_sample(1, "PROBAND_A", files=_reads("PROBAND_A"))]

    spec = _by_name(_inputs(_contract("raredisease"), samples))["case_id"]

    assert spec["required"] is True
    assert spec["sample_field"] is None
    assert spec["is_file"] is False


def test_it_names_the_sample_field_a_column_would_have_come_from():
    """sarek's patient comes from the sample's donor source. The grid says so,
    because filling it here and filling it on the sample are different acts and
    the scientist has to be able to tell which one they are doing."""
    samples = [_make_sample(1, "TUMOR_A", files=_reads("TUMOR_A"))]

    spec = _by_name(_inputs(_contract("sarek"), samples))["patient"]

    assert spec["sample_field"] == "donor_source"


def test_the_columns_come_back_in_the_order_the_sheet_uses():
    """The grid and the review table are read one after the other. Two different
    column orders make comparing them work the reader should not have to do."""
    samples = [_make_sample(1, "RNA_A", files=_reads("RNA_A"))]

    names = [s["name"] for s in _inputs(_contract("rnastructurome"), samples)]

    assert names == ["condition", "replicate", "sample_group"]


# -- How each answer is rendered --


def test_a_pipeline_vocabulary_is_offered_as_a_closed_list():
    """rnastructurome's `condition` accepts exactly treated, untreated and
    denatured. It is an rf-norm chemistry value, so those three ARE the whole
    truth and a free-text box invites a launch that dies in Nextflow."""
    samples = [_make_sample(1, "RNA_A", files=_reads("RNA_A"))]

    spec = _by_name(_inputs(_contract("rnastructurome"), samples))["condition"]

    assert spec["constrained"] is True
    assert spec["allowed_values"] == ["treated", "untreated", "denatured"]


def test_a_field_recorded_on_the_sample_is_never_a_closed_list():
    """raredisease requires `sex` and encodes it as a PED code. That is what
    raredisease ingests, not what biology exists: XXY, X0, XYY, XXX and mosaics
    are all real and none of them is 0, 1 or 2. Constraining the sample's own
    field to a pipeline's vocabulary would write a false biological model into
    the LIMS, so the grid takes free text here whatever the schema lists."""
    samples = [_make_sample(1, "PROBAND_A", files=_reads("PROBAND_A"))]

    spec = _by_name(_inputs(_contract("raredisease"), samples))["sex"]

    assert spec["sample_field"] == "sex"
    assert spec["constrained"] is False


def test_an_unconstrained_column_offers_no_list_at_all():
    """mag's `group` is any string. An empty allowed set means "anything goes",
    and must never be rendered as a dropdown with nothing in it."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    spec = _by_name(_inputs(_contract("mag"), samples))["group"]

    assert spec["constrained"] is False
    assert spec["allowed_values"] == []


def test_a_column_the_row_made_necessary_says_which_column_did_it():
    """short_reads_platform is absent from mag's own required list. The grid has
    to explain why it is being asked for, or the question looks arbitrary."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    spec = _by_name(_inputs(_contract("mag"), samples))["short_reads_platform"]

    assert spec["required_by"] == "short_reads_1"
    assert spec["constrained"] is True
    assert "ILLUMINA" in spec["allowed_values"]


def test_a_file_column_is_marked_as_one_so_the_grid_can_offer_attachments():
    """A file column is answered by picking one of the sample's own files, not by
    typing a path. funcscan's `fasta` is the case: the sample has none attached,
    so the grid has to offer the file picker rather than a text box."""
    samples = [_make_sample(1, "ISOLATE_A", files=[])]

    spec = _by_name(_inputs(_contract("funcscan"), samples))["fasta"]

    assert spec["is_file"] is True


# -- What the grid may say about a column --


def test_it_carries_the_pipelines_own_description_when_there_is_one():
    """rnastructurome documents its columns. That text is the pipeline's own
    words about its own contract, so it is the one explanation bioAF can show
    without inventing anything."""
    samples = [_make_sample(1, "RNA_A", files=_reads("RNA_A"))]

    spec = _by_name(_inputs(_contract("rnastructurome"), samples))["sample_group"]

    assert "rf-norm" in spec["description"]


def test_it_carries_the_schemas_format_hint():
    """mag's group has no description but its errorMessage says the value cannot
    contain spaces. That is a real constraint the scientist needs before pasting
    a column of values, not after nf-schema rejects it."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    spec = _by_name(_inputs(_contract("mag"), samples))["group"]

    assert "no spaces" in spec["format_hint"]


def test_it_invents_no_explanation_when_the_schema_gives_none():
    """0 of the 10 captured schemas describe a column like mag's `group`. bioAF
    saying what `group` means would be hand-written per-pipeline knowledge, which
    is exactly what the schema-driven work removed, and it would go stale
    silently when the pipeline changed."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    spec = _by_name(_inputs(_contract("mag"), samples))["group"]

    assert spec["description"] is None


# -- It agrees with the block, because it is the same computation --


def test_it_asks_for_exactly_what_the_launch_check_blocks_on():
    """The two must not drift. A grid that omits a blocking column strands the
    user on a launch button that never enables; one that adds a column asks for
    something no rule requires."""
    import pytest

    from app.exceptions import SamplesMissingRequiredFieldsError

    samples = [_make_sample(1, "RNA_A", files=_reads("RNA_A"))]
    contract = _contract("rnastructurome")

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        SampleSheetService.check_contract_satisfiable(contract, samples, {})

    assert {s["name"] for s in _inputs(contract, samples)} == set(exc.value.details["missing_columns"])


def test_it_lists_the_samples_still_missing_a_value():
    """The grid flags the rows that need attention. Design section 5: samples
    added since a design was set arrive blank and cannot be left blank."""
    samples = [
        _make_sample(1, "RNA_A", files=_reads("RNA_A")),
        _make_sample(2, "RNA_B", files=_reads("RNA_B")),
    ]
    values = {"1": {"condition": "treated", "sample_group": "g1", "replicate": "1"}}

    spec = _by_name(_inputs(_contract("rnastructurome"), samples, sample_values=values))["condition"]

    assert [s["external_id"] for s in spec["samples"]] == ["RNA_B"]


def test_a_pipeline_that_cannot_run_from_samples_asks_for_nothing():
    """Collecting answers would imply a launch that is still impossible. The
    same reasoning already governs the run-level inputs."""
    contract = parse_contract(None)

    assert _inputs(contract, [_make_sample(1, "A")]) == []
