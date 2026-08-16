"""Characters a pipeline will not accept, identified and a fix recommended.

Scientists name samples the way their lab names samples. `SAMPLE-101` and
`DEMO4-SAMPLE-001` are both real names on the demo, and nf-core/ampliseq rejects
both: its schema requires `^[a-zA-Z][a-zA-Z0-9_]+$` and the hyphen is not
allowed. Until now bioAF emitted the name unchanged and the run died inside
Nextflow on a rule the scientist never saw.

**bioAF does not dictate the value of any field.** The user specifies it, and a
user may misspell a value or simply spell it the way they choose. bioAF's job is
to identify the characters that cause a problem and RECOMMEND a sanitized
alternative. It never silently substitutes one: a value quietly rewritten is a
value the scientist did not choose, and the sheet would then disagree with the
LIMS about what the sample is called.

So the shape is: block, name the offending value, offer a spelling that would
work, and let the scientist accept it or write their own. Accepting is the
ordinary step 2 path, since a stated value overrides everything.

Two further rules keep the recommendation honest.

**A recommendation is VERIFIED against the schema's own regex before it is
offered.** Candidates are a small ordered list and only one that actually
satisfies the pattern is ever shown. Where none does, bioAF recommends nothing:
no rearrangement of punctuation turns a mistyped
`^GC[AF]_[0-9]{9}\\.[0-9]+$` accession into a correct one, and suggesting one
that merely looks right would name the wrong assembly.

**Two samples must never be recommended the same name.** `SAMPLE-1` and
`SAMPLE_1` both want to become `SAMPLE_1`, and a sheet carrying that name twice
merges two samples' results. Where that would happen, bioAF says so instead of
recommending it.
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


def _names(csv_text: str, column: str = "sample") -> list[str]:
    rows = [line.split(",") for line in csv_text.strip().splitlines()]
    at = rows[0].index(column)
    return [r[at] for r in rows[1:]]


def _generate(contract, samples, parameters=None, sample_values=None):
    return SampleSheetService.generate_from_contract(contract, samples, parameters or {}, sample_values=sample_values)


def _check(contract, samples, parameters=None, sample_values=None):
    return SampleSheetService.check_contract_satisfiable(
        contract, samples, parameters or {}, sample_values=sample_values
    )


def _gap(contract, samples, column, parameters=None, sample_values=None):
    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(contract, samples, parameters, sample_values)
    missing = exc.value.details["missing_columns"]
    assert column in missing, f"expected a gap on {column}, got {sorted(missing)}"
    return missing[column]


# -- The problem is identified, not silently fixed --


def test_a_name_the_pipeline_rejects_blocks_and_says_which_sample():
    """SAMPLE-101 is a real name on the demo and ampliseq will not take it. The
    scientist finds out now rather than three minutes into a run."""
    samples = [_make_sample(1, "SAMPLE-101", files=_reads("SAMPLE-101"))]

    detail = _gap(_contract("ampliseq"), samples, "sample")

    assert detail["reason"] == "invalid_characters"
    assert [s["external_id"] for s in detail["samples"]] == ["SAMPLE-101"]


def test_it_recommends_a_spelling_that_would_work():
    """Identifying the problem without offering a way out just moves the dead
    end earlier. The recommendation is what makes the block actionable."""
    samples = [_make_sample(1, "SAMPLE-101", files=_reads("SAMPLE-101"))]

    detail = _gap(_contract("ampliseq"), samples, "sample")

    assert detail["samples"][0]["suggestion"] == "SAMPLE_101"


def test_it_reports_the_constraint_that_rejected_the_value():
    """The scientist may want to choose their own spelling rather than take the
    recommendation, and they cannot do that without knowing the rule."""
    samples = [_make_sample(1, "SAMPLE-101", files=_reads("SAMPLE-101"))]

    detail = _gap(_contract("ampliseq"), samples, "sample")

    assert detail["pattern"] == "^[a-zA-Z][a-zA-Z0-9_]+$"


def test_bioaf_does_not_rewrite_the_value_on_its_own():
    """The load-bearing rule. A value quietly rewritten is one the scientist did
    not choose, and the sheet would then disagree with the LIMS about what the
    sample is called."""
    samples = [_make_sample(1, "SAMPLE-101", files=_reads("SAMPLE-101"))]

    csv_text = _generate(_contract("ampliseq"), samples)

    assert _names(csv_text) == ["SAMPLE-101"]


def test_accepting_the_recommendation_unblocks_the_launch():
    """Accepting is the ordinary step 2 path: a stated value overrides
    everything, so the scientist's choice is what travels."""
    samples = [_make_sample(1, "SAMPLE-101", files=_reads("SAMPLE-101"))]
    accepted = {"1": {"sample": "SAMPLE_101"}}

    _check(_contract("ampliseq"), samples, sample_values=accepted)

    assert _names(_generate(_contract("ampliseq"), samples, sample_values=accepted)) == ["SAMPLE_101"]


def test_a_scientist_may_choose_a_different_spelling_entirely():
    """bioAF recommends; it does not decide. Any spelling the pipeline accepts
    is the scientist's to pick."""
    samples = [_make_sample(1, "SAMPLE-101", files=_reads("SAMPLE-101"))]
    theirs = {"1": {"sample": "gut_biopsy_101"}}

    _check(_contract("ampliseq"), samples, sample_values=theirs)

    assert _names(_generate(_contract("ampliseq"), samples, sample_values=theirs)) == ["gut_biopsy_101"]


def test_a_name_the_pipeline_already_accepts_is_never_mentioned():
    """The common case, and the control. A name that works is left entirely
    alone and raises nothing."""
    samples = [_make_sample(1, "SRX30659353", files=_reads("SRX30659353"))]

    _check(_contract("ampliseq"), samples)

    assert _names(_generate(_contract("ampliseq"), samples)) == ["SRX30659353"]


# -- It is not only sample names --


def test_a_species_with_a_space_is_reported_with_a_recommendation():
    """genomeqc declares `species` as `^\\S+$`, so "Homo sapiens" makes the sheet
    invalid. The recommendation matches the Ensembl species format that nf-core
    schemas say binomials are normalised to."""
    samples = [_make_sample(1, "S1", organism="Homo sapiens")]

    detail = _gap(_contract("genomeqc"), samples, "species")

    assert detail["samples"][0]["suggestion"] == "Homo_sapiens"


def test_the_species_value_itself_is_still_emitted_unchanged():
    """Same rule as names: bioAF does not decide what a field says. The preview
    shows exactly what is wrong, and the launch is blocked until it is settled."""
    samples = [_make_sample(1, "S1", organism="Homo sapiens")]

    assert _names(_generate(_contract("genomeqc"), samples), "species") == ["Homo sapiens"]


# -- A recommendation is verified, never invented --


def test_no_recommendation_is_offered_where_none_would_work():
    """genomeqc's `ncbi` is `^GC[AF]_[0-9]{9}\\.[0-9]+$`. Punctuation cannot turn a
    wrong accession into a right one, and offering one that merely looks like an
    accession would point at the wrong assembly."""
    samples = [_make_sample(1, "SPECIES_A")]
    values = {"1": {"species": "Homo_sapiens", "ncbi": "not an accession"}}

    detail = _gap(_contract("genomeqc"), samples, "ncbi", sample_values=values)

    assert detail["samples"][0]["suggestion"] is None


def test_every_recommendation_satisfies_the_schemas_own_regex():
    """The property that makes recommending safe at all."""
    import re

    contract = _contract("ampliseq")
    pattern = re.compile(contract.patterns["sample"])
    awkward = ["SAMPLE-101", "DEMO4-SAMPLE-001", "gut sample 1", "1st-run", "a..b--c"]
    samples = [_make_sample(i, name, files=_reads("x")) for i, name in enumerate(awkward, start=1)]

    detail = _gap(contract, samples, "sample")

    offered = [s["suggestion"] for s in detail["samples"] if s["suggestion"]]
    assert len(offered) == len(awkward)
    for suggestion in offered:
        assert pattern.match(suggestion), f"{suggestion!r} does not satisfy {contract.patterns['sample']}"


# -- Two samples must never be recommended the same name --


def test_it_refuses_to_recommend_a_name_that_would_merge_two_samples():
    """SAMPLE-1 and SAMPLE_1 both want to become SAMPLE_1. A sheet carrying that
    name twice merges two samples' results, so the recommendation is withheld
    and the clash is reported instead."""
    samples = [
        _make_sample(1, "SAMPLE-1", files=_reads("a")),
        _make_sample(2, "SAMPLE_1", files=_reads("b")),
    ]

    detail = _gap(_contract("ampliseq"), samples, "sample")

    assert detail["reason"] == "collision"
    assert sorted(s["external_id"] for s in detail["samples"]) == ["SAMPLE-1", "SAMPLE_1"]


def test_two_supplied_names_that_collide_are_refused():
    """The same hazard by a different route: the scientist can type a clash as
    easily as bioAF can recommend one."""
    samples = [
        _make_sample(1, "SAMPLE-1", files=_reads("a")),
        _make_sample(2, "SAMPLE-2", files=_reads("b")),
    ]
    clashing = {"1": {"sample": "SAME"}, "2": {"sample": "SAME"}}

    detail = _gap(_contract("ampliseq"), samples, "sample", sample_values=clashing)

    assert detail["reason"] == "collision"


def test_distinct_names_that_sanitize_distinctly_are_fine():
    """The control, so collision detection cannot pass by rejecting everything."""
    samples = [
        _make_sample(1, "SAMPLE-1", files=_reads("a")),
        _make_sample(2, "SAMPLE-2", files=_reads("b")),
    ]

    detail = _gap(_contract("ampliseq"), samples, "sample")

    assert detail["reason"] == "invalid_characters"
    assert [s["suggestion"] for s in detail["samples"]] == ["SAMPLE_1", "SAMPLE_2"]


def test_the_same_sample_across_several_lanes_is_not_a_collision():
    """A multi-lane sample legitimately repeats its own name across rows. Only
    two DIFFERENT samples sharing one name is a collision."""
    files = [
        _make_file("GUT_A_L001_R1_001.fastq.gz"),
        _make_file("GUT_A_L001_R2_001.fastq.gz"),
        _make_file("GUT_A_L002_R1_001.fastq.gz"),
        _make_file("GUT_A_L002_R2_001.fastq.gz"),
    ]
    samples = [_make_sample(1, "GUT_A", files=files)]

    _check(_contract("ampliseq"), samples)

    assert _names(_generate(_contract("ampliseq"), samples)) == ["GUT_A", "GUT_A"]


# -- What was already true stays true --


def test_a_purely_numeric_name_keeps_its_existing_handling():
    """nf-schema infers a column's type from its values, so a numeric name is
    read as an integer and rejected against a string field. That predates this
    work and must keep working."""
    samples = [_make_sample(1, "123", files=_reads("x"))]

    assert _names(_generate(_contract("demo"), samples)) == ["sample_123"]


def test_a_sample_with_no_name_at_all_still_falls_back_to_its_id():
    samples = [_make_sample(7, "", files=_reads("x"))]

    assert _names(_generate(_contract("demo"), samples)) == ["sample_7"]
