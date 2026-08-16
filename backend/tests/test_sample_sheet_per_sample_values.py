"""Per-sample values a scientist supplies, applied by sample ID.

bioAF fills a samplesheet column from three sources: the reads it resolved, the
sample's own fields, and the launch parameters. A column describing EXPERIMENTAL
DESIGN has none of those. mag's ``group`` controls co-assembly and rnasplice's
``condition`` defines the differential contrast; both are required, neither is a
fact bioAF holds, and guessing either produces a run that completes green and is
scientifically wrong. So they block.

This adds the fourth source: a value the scientist states. It is the primitive
under every surface in the step 2 design (the entry grid collects it, the review
step edits it, the mapping stores it, the run snapshots it).

Two properties are load-bearing and are what most of these tests pin:

**Applied by sample ID, never by row position.** A positional application
misaligned by one row assigns every value to the wrong sample, and the run then
completes green with the wrong co-assembly grouping. That is precisely the
failure this project refuses to commit.

**Overrides every other source, then obeys the schema's enum.** Overriding is
what lets the review step correct a wrongly-resolved file in place (design
section 7): a reference genome satisfies funcscan's ``fasta`` pattern as well as
the real assembly does, and the scientist has to be able to say so. Obeying the
enum is what stops "a human typed it" from producing a sheet that passes bioAF
and dies inside Nextflow: an unacceptable value is dropped, which leaves the
column empty, which blocks the launch with a message naming it.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.exceptions import SamplesMissingRequiredFieldsError
from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _make_file(filename: str, storage_uri: str | None = None):
    f = MagicMock()
    f.filename = filename
    f.storage_uri = storage_uri or f"gs://bucket/{filename}"
    f.tags_json = []
    return f


def _reads(name: str) -> list:
    """A paired FASTQ set named so the Illumina convention classifies it."""
    return [_make_file(f"{name}_R1_001.fastq.gz"), _make_file(f"{name}_R2_001.fastq.gz")]


def _make_sample(sample_id: int, external_id: str, files=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    return sample


def _rows(csv_text: str) -> list[list[str]]:
    return [line.split(",") for line in csv_text.strip().splitlines()]


def _header(csv_text: str) -> list[str]:
    return _rows(csv_text)[0]


def _cell_for(csv_text: str, external_id: str, column: str) -> str:
    """One sample's value for one column, found by name rather than row index."""
    rows = _rows(csv_text)
    header = rows[0]
    sample_col = header.index("sample")
    for row in rows[1:]:
        if row[sample_col] == external_id:
            return row[header.index(column)]
    raise AssertionError(f"no row for {external_id} in {csv_text!r}")


def _generate(contract, samples, parameters=None, sample_values=None):
    return SampleSheetService.generate_from_contract(contract, samples, parameters or {}, sample_values=sample_values)


def _check(contract, samples, parameters=None, sample_values=None):
    return SampleSheetService.check_contract_satisfiable(
        contract, samples, parameters or {}, sample_values=sample_values
    )


# -- A supplied value fills a column bioAF cannot source --


def test_a_supplied_value_fills_a_design_column_bioaf_refuses_to_guess():
    """mag's `group` controls co-assembly. bioAF has no source for it and must
    never invent one, so it blocks today. A scientist stating it is the whole
    point of step 2."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    values = {"1": {"group": "gut"}}

    csv_text = _generate(_contract("mag"), samples, sample_values=values)

    assert _cell_for(csv_text, "GUT_A", "group") == "gut"


def test_a_supplied_value_unblocks_a_launch_that_is_otherwise_refused():
    """The satisfiability check must see the same value generation will use.
    If it does not, a run that can now produce a valid sheet is still refused."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    with pytest.raises(SamplesMissingRequiredFieldsError):
        _check(_contract("mag"), samples)

    _check(_contract("mag"), samples, sample_values={"1": {"group": "gut"}})


def test_a_value_for_only_some_samples_still_blocks_and_names_the_rest():
    """Half a design is not a design. The block has to name the samples still
    missing a value, because that is what the user has to go and fill in."""
    samples = [
        _make_sample(1, "GUT_A", files=_reads("GUT_A")),
        _make_sample(2, "GUT_B", files=_reads("GUT_B")),
    ]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("mag"), samples, sample_values={"1": {"group": "gut"}})

    offenders = exc.value.details["missing_columns"]["group"]["samples"]
    assert [s["external_id"] for s in offenders] == ["GUT_B"]


# -- Applied by sample ID, never by row position --


def test_values_follow_the_sample_id_not_the_row_order():
    """The failure this guards is a paste misaligned by one row, which assigns
    every value to the wrong sample and still runs green. Values are keyed by
    sample ID, so the order samples arrive in cannot change what they mean."""
    samples = [
        _make_sample(7, "GUT_A", files=_reads("GUT_A")),
        _make_sample(3, "SKIN_B", files=_reads("SKIN_B")),
    ]
    values = {"3": {"group": "skin"}, "7": {"group": "gut"}}

    csv_text = _generate(_contract("mag"), samples, sample_values=values)

    assert _cell_for(csv_text, "GUT_A", "group") == "gut"
    assert _cell_for(csv_text, "SKIN_B", "group") == "skin"


def test_a_value_for_a_sample_not_in_this_run_is_simply_not_applied():
    """A carried-over mapping names samples this run may not include (design
    section 5). Those values do not apply, and must not leak onto another row."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    values = {"1": {"group": "gut"}, "99": {"group": "from_another_experiment"}}

    csv_text = _generate(_contract("mag"), samples, sample_values=values)

    assert "from_another_experiment" not in csv_text
    assert _cell_for(csv_text, "GUT_A", "group") == "gut"


# -- Overrides every other source (design section 7, correcting in place) --


def test_a_supplied_value_overrides_a_file_bioaf_resolved_by_pattern():
    """A regex match is not proof of the right file: a reference genome
    satisfies funcscan's `fasta` pattern exactly as well as the scientist's
    assembly does. Correcting it in the review step is the only backstop."""
    samples = [_make_sample(1, "ISOLATE_A", files=[_make_file("ISOLATE_A.contigs.fasta")])]
    values = {"1": {"fasta": "gs://bucket/the_one_i_meant.fasta"}}

    csv_text = _generate(_contract("funcscan"), samples, sample_values=values)

    assert _cell_for(csv_text, "ISOLATE_A", "fasta") == "gs://bucket/the_one_i_meant.fasta"


def test_a_supplied_value_overrides_a_resolved_read_column():
    """Step 1 blocks when two files match one column rather than choosing. The
    scientist resolves that by naming the file, which has to beat resolution."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    values = {"1": {"group": "gut", "short_reads_1": "gs://bucket/the_right_R1.fastq.gz"}}

    csv_text = _generate(_contract("mag"), samples, sample_values=values)

    assert _cell_for(csv_text, "GUT_A", "short_reads_1") == "gs://bucket/the_right_R1.fastq.gz"


def test_a_supplied_value_overrides_a_value_taken_from_the_sample_record():
    """sarek's `patient` comes from `Sample.donor_source`. When that is wrong
    for this run, the scientist says so, and the run uses what they said."""
    samples = [_make_sample(1, "TUMOR_A", files=_reads("TUMOR_A"), donor_source="DONOR_1")]
    values = {"1": {"patient": "DONOR_CORRECTED"}}

    csv_text = _generate(_contract("sarek"), samples, sample_values=values)

    assert _cell_for(csv_text, "TUMOR_A", "patient") == "DONOR_CORRECTED"


def test_a_supplied_value_overrides_a_launch_parameter():
    """`strandedness` is one run-level answer applied to every row. A single
    sample that differs is stated per sample and must win for that row only."""
    samples = [
        _make_sample(1, "A", files=_reads("A")),
        _make_sample(2, "B", files=_reads("B")),
    ]
    params = {"strandedness": "forward"}
    values = {"2": {"strandedness": "reverse"}}

    csv_text = _generate(
        _contract("rnasplice"),
        samples,
        parameters=params,
        sample_values={"1": {"condition": "ctrl"}, "2": {"condition": "treat", **values["2"]}},
    )

    assert _cell_for(csv_text, "A", "strandedness") == "forward"
    assert _cell_for(csv_text, "B", "strandedness") == "reverse"


# -- The schema still decides what is acceptable --


def test_a_value_the_schema_rejects_is_dropped_and_blocks_rather_than_shipping():
    """rnasplice's `strandedness` enum is forward/reverse/unstranded. bioAF's own
    "auto" default is already dropped here. A typed value gets no more licence:
    emitting it produces a sheet that passes bioAF and dies inside Nextflow."""
    samples = [_make_sample(1, "A", files=_reads("A"))]
    values = {"1": {"condition": "ctrl", "strandedness": "sideways"}}

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("rnasplice"), samples, sample_values=values)

    assert "strandedness" in exc.value.details["missing_columns"]

    csv_text = _generate(_contract("rnasplice"), samples, sample_values=values)
    assert "sideways" not in csv_text


def test_a_value_the_schema_accepts_is_kept():
    """The other half of the enum rule, so the check above cannot pass by
    rejecting everything."""
    samples = [_make_sample(1, "A", files=_reads("A"))]
    values = {"1": {"condition": "ctrl", "strandedness": "reverse"}}

    _check(_contract("rnasplice"), samples, sample_values=values)
    csv_text = _generate(_contract("rnasplice"), samples, sample_values=values)

    assert _cell_for(csv_text, "A", "strandedness") == "reverse"


def test_a_blank_value_counts_as_no_value():
    """An empty grid cell is an unanswered question, not an answer. Design
    section 5: a sample added since the design was set arrives blank and cannot
    be left blank."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    with pytest.raises(SamplesMissingRequiredFieldsError):
        _check(_contract("mag"), samples, sample_values={"1": {"group": "   "}})


def test_a_supplied_value_is_stripped_of_surrounding_whitespace():
    """Values arrive by paste from a spreadsheet, which carries stray spaces.
    mag's `group` is pattern-constrained to `^\\S+$`, so an untrimmed value is
    rejected by nf-schema after the launch rather than by bioAF before it."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    csv_text = _generate(_contract("mag"), samples, sample_values={"1": {" group ": " gut "}})

    assert _cell_for(csv_text, "GUT_A", "group") == "gut"


def test_a_column_the_pipeline_never_declared_is_not_emitted():
    """A mapping carried over from another pipeline names columns this one does
    not have (design section 8). They are ignored, not appended: an undeclared
    column fails nf-schema's validation of the whole sheet."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    values = {"1": {"group": "gut", "condition": "from_rnasplice"}}

    csv_text = _generate(_contract("mag"), samples, sample_values=values)

    assert "condition" not in _header(csv_text)
    assert "from_rnasplice" not in csv_text


# -- Nothing changes for a run that supplies no values --


def test_supplying_no_values_leaves_todays_behaviour_exactly_as_it_was():
    """The whole existing catalog runs through this path. An unsupplied value
    must be indistinguishable from the argument never existing."""
    samples = [_make_sample(1, "TUMOR_A", files=_reads("TUMOR_A"), donor_source="DONOR_1")]

    before = SampleSheetService.generate_from_contract(_contract("sarek"), samples, {})
    after = _generate(_contract("sarek"), samples, sample_values={})
    also = _generate(_contract("sarek"), samples, sample_values=None)

    assert before == after == also
