"""Samplesheet columns that hold a file, not just the ones that hold FASTQ.

The schema-driven generator recognized exactly one kind of per-sample file: a
FASTQ read. Everything else (an assembly, an alignment, a variant set) was
invisible, so ``is_sample_launchable`` was false for any pipeline that did not
declare a read column and the launch was refused outright.

That refusal did not depend on what the samples actually held. bioAF stores
arbitrary files per sample (``Sample.files`` -> ``File.file_type``), so a
funcscan run whose samples each carry an assembly was refused for lacking reads
it never wanted.

A file column is now recognized from the schema the same way a read column
always was: from the pipeline's own declaration. Resolution matches a sample's
files against the column's own ``pattern``, which is the same regex nf-schema
will apply, rather than against a list of extensions bioAF maintains.

The governing rule from the rest of this project still holds: a wrong mapping is
worse than a missing one. Two files matching one column is ambiguity, so it
blocks instead of choosing.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.exceptions import (
    PipelineNotSampleLaunchableError,
    SamplesMissingRequiredFieldsError,
)
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


def _make_sample(sample_id: int, external_id: str, files=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    return sample


def _rows(csv_text: str) -> list[list[str]]:
    return [line.split(",") for line in csv_text.strip().splitlines()]


def _header(csv_text: str) -> list[str]:
    return _rows(csv_text)[0]


def _col(csv_text: str, column: str, row: int = 1) -> str:
    rows = _rows(csv_text)
    return rows[row][rows[0].index(column)]


def _check(contract, samples, parameters=None):
    return SampleSheetService.check_contract_satisfiable(contract, samples, parameters or {})


# -- A pipeline whose input is a file bioAF holds is launchable --


def test_funcscan_launches_when_the_sample_carries_the_assembly_it_wants():
    """funcscan requires `sample` and `fasta` and declares no read column. It was
    refused unconditionally. A sample carrying an assembly can feed it, so it
    must launch."""
    samples = [_make_sample(1, "ISOLATE_A", files=[_make_file("ISOLATE_A.contigs.fasta")])]

    _check(_contract("funcscan"), samples)
    csv_text = SampleSheetService.generate_from_contract(_contract("funcscan"), samples, {})

    assert _header(csv_text) == ["sample", "fasta"]
    assert _col(csv_text, "fasta") == "gs://bucket/ISOLATE_A.contigs.fasta"
    assert _col(csv_text, "sample") == "ISOLATE_A"


def test_the_column_accepts_any_extension_its_own_pattern_allows():
    """funcscan's fasta pattern is ^\\S+\\.(fasta|fas|fna|fa)(\\.gz)?$. bioAF must
    honour the whole alternation, including the optional gzip suffix, rather than
    a shorter list of its own."""
    for filename in ("a.fasta", "a.fa", "a.fna", "a.fas", "a.fa.gz"):
        samples = [_make_sample(1, "S1", files=[_make_file(filename)])]

        csv_text = SampleSheetService.generate_from_contract(_contract("funcscan"), samples, {})

        assert _col(csv_text, "fasta") == f"gs://bucket/{filename}", filename


# -- No matching file is a block, naming the column, not a flat refusal --


def test_a_missing_file_blocks_and_names_the_column():
    """The sample has a file, just not the one the pipeline asked for. That is
    the same shape as any other unsourced required column, and it is actionable:
    attach an assembly."""
    samples = [_make_sample(1, "S1", files=[_make_file("S1.bam")])]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("funcscan"), samples)

    assert "fasta" in exc.value.details["missing_columns"]


def test_the_block_lists_only_the_samples_actually_missing_the_file():
    samples = [
        _make_sample(1, "HAS_IT", files=[_make_file("HAS_IT.fasta")]),
        _make_sample(2, "NO_ASSEMBLY", files=[]),
    ]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("funcscan"), samples)

    offenders = exc.value.details["missing_columns"]["fasta"]["samples"]
    assert [s["external_id"] for s in offenders] == ["NO_ASSEMBLY"]


# -- Ambiguity blocks rather than guessing --


def test_two_files_matching_one_column_block_rather_than_picking_one():
    """Choosing between two assemblies is the same class of silent wrongness as
    auto-filling mag's co-assembly `group`: the run goes green on the wrong
    input. The user has to say which."""
    samples = [
        _make_sample(
            1,
            "S1",
            files=[_make_file("S1.spades.fasta"), _make_file("S1.megahit.fasta")],
        )
    ]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("funcscan"), samples)

    assert "fasta" in exc.value.details["missing_columns"]


def test_an_ambiguous_column_says_it_is_ambiguous_not_missing():
    """'Attach an assembly' is the wrong instruction when two are already
    attached, so the detail distinguishes the two cases."""
    samples = [
        _make_sample(
            1,
            "S1",
            files=[_make_file("S1.spades.fasta"), _make_file("S1.megahit.fasta")],
        )
    ]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("funcscan"), samples)

    detail = exc.value.details["missing_columns"]["fasta"]
    assert detail["reason"] == "ambiguous"
    assert sorted(detail["candidates"]) == ["S1.megahit.fasta", "S1.spades.fasta"]


# -- The refusal still exists, for pipelines that take no per-sample file --


def test_a_pipeline_that_wants_no_per_sample_file_is_still_refused():
    """The refusal is not gone, it is narrowed. A pipeline whose required input
    is a bare identifier cannot be fed from samples at all, and no amount of
    attaching files changes that."""
    contract = parse_contract(
        {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["taxid"],
                "properties": {
                    "sample": {"type": "string", "pattern": r"^\S+$"},
                    "taxid": {"type": "integer"},
                },
            },
        }
    )

    with pytest.raises(PipelineNotSampleLaunchableError):
        _check(contract, [_make_sample(1, "S1", files=[_make_file("S1.fasta")])])


# -- Optional file columns keep today's behavior --


def test_an_optional_file_column_is_omitted_when_nothing_matches():
    """sarek declares bam/cram/vcf as optional alternatives to its reads. A
    FASTQ run must not sprout empty alignment columns."""
    samples = [
        _make_sample(
            1,
            "S1",
            donor_source="D1",
            files=[_make_file("S1_R1.fastq.gz"), _make_file("S1_R2.fastq.gz")],
        )
    ]

    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), samples, {})

    assert "bam" not in _header(csv_text)
    assert "cram" not in _header(csv_text)
    assert "vcf" not in _header(csv_text)


def test_an_optional_file_column_is_filled_when_the_sample_has_one():
    """The same schema, a sample carrying an alignment: sarek accepts it, so
    bioAF should pass it rather than drop it."""
    samples = [_make_sample(1, "S1", donor_source="D1", files=[_make_file("S1.bam")])]

    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), samples, {})

    assert _col(csv_text, "bam") == "gs://bucket/S1.bam"


# -- Read handling is untouched --


def test_a_fastq_pipeline_is_unchanged():
    """The read path is the one that already worked. Generalising file columns
    must not perturb it."""
    samples = [_make_sample(1, "S1", files=[])]
    parameters = {"input_paths": {"1": ["/data/S1_R1.fastq.gz", "/data/S1_R2.fastq.gz"]}}

    csv_text = SampleSheetService.generate_from_contract(_contract("demo"), samples, parameters)

    assert _header(csv_text) == ["sample", "fastq_1", "fastq_2"]
    assert _col(csv_text, "fastq_1") == "/data/S1_R1.fastq.gz"


def test_reads_are_not_also_offered_as_a_generic_file_column():
    """genomeqc declares `fastq` as a file column and `fasta` alongside it. The
    read column must still be resolved as a read, not matched twice."""
    samples = [
        _make_sample(1, "S1", organism="Homo_sapiens", files=[_make_file("S1.fastq.gz")]),
    ]

    csv_text = SampleSheetService.generate_from_contract(_contract("genomeqc"), samples, {})

    assert _col(csv_text, "fastq") == "gs://bucket/S1.fastq.gz"
    assert "fasta" not in _header(csv_text)


# -- Filtering reads must not turn a loud failure into a silent one --


def test_reads_that_fail_the_declared_pattern_block_instead_of_vanishing():
    """sarek's read pattern demands `.gz`. A scientist holding uncompressed
    FASTQs used to have them passed through and rejected by nf-schema, which at
    least named the problem. Filtering them out silently would emit an empty
    read column and fail inside Nextflow on a blank field instead.

    The sample HAS reads; they are just not in a form this pipeline accepts, and
    that has to be said out loud."""
    samples = [_make_sample(1, "S1", donor_source="D1", files=[_make_file("S1_R1.fastq"), _make_file("S1_R2.fastq")])]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("sarek"), samples)

    detail = exc.value.details["missing_columns"]["fastq_1"]
    assert detail["reason"] == "no_matching_file"
    assert [s["external_id"] for s in detail["samples"]] == ["S1"]


def test_the_unusable_read_block_shows_the_pattern_the_files_failed():
    """'fastq_1 is missing' is not actionable when the file is right there. The
    accepted form is what tells the user to gzip it."""
    samples = [_make_sample(1, "S1", donor_source="D1", files=[_make_file("S1_R1.fastq")])]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("sarek"), samples)

    assert ".gz" in exc.value.details["missing_columns"]["fastq_1"]["pattern"]


def test_a_sample_with_no_files_at_all_is_unchanged():
    """The block is scoped to files that exist and do not qualify. A sample with
    nothing attached is a different situation, handled elsewhere in the launch
    path, and must keep behaving as it does today."""
    _check(_contract("sarek"), [_make_sample(1, "S1", donor_source="D1", files=[])])


def test_reads_in_an_acceptable_form_do_not_block():
    samples = [
        _make_sample(1, "S1", donor_source="D1", files=[_make_file("S1_R1.fastq.gz"), _make_file("S1_R2.fastq.gz")])
    ]

    _check(_contract("sarek"), samples)


def test_an_alternative_input_satisfies_the_pipeline_without_reads():
    """sarek takes reads OR an alignment. A sample carrying only a BAM has no
    reads by design, so it must not be blocked for lacking them."""
    _check(_contract("sarek"), [_make_sample(1, "S1", donor_source="D1", files=[_make_file("S1.bam")])])


# -- Reads and their alternatives are not emitted together --


def test_an_alternative_input_is_not_added_when_reads_are_present():
    """sarek accepts reads OR an alignment for a row, not both. A scientist who
    uploaded a BAM alongside their FASTQs would otherwise get a sheet carrying
    both, which is not a row sarek can act on."""
    samples = [
        _make_sample(
            1,
            "S1",
            donor_source="D1",
            files=[_make_file("S1_R1.fastq.gz"), _make_file("S1_R2.fastq.gz"), _make_file("S1.bam")],
        )
    ]

    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), samples, {})

    assert _col(csv_text, "fastq_1") == "gs://bucket/S1_R1.fastq.gz"
    assert "bam" not in _header(csv_text)


def test_empty_read_columns_are_dropped_when_an_alternative_input_is_used():
    """The mirror of the rule above. A BAM-only row has no reads by design, so
    carrying `fastq_1,fastq_2` as empty columns states something false about the
    row and contradicts the rule that unfilled optional columns are dropped."""
    samples = [_make_sample(1, "S1", donor_source="D1", files=[_make_file("S1.bam")])]

    csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), samples, {})

    assert _header(csv_text) == ["sample", "bam", "patient"]
    assert _col(csv_text, "bam") == "gs://bucket/S1.bam"


def test_an_ambiguous_alternative_does_not_count_as_a_usable_input():
    """genomeqc accepts either reads or an assembly, both optional. Two
    assemblies and no reads leaves nothing bioAF can place: the assembly is
    ambiguous so it stays empty, and the read column has no candidate. Launching
    would provision a node for a row with no input in it at all."""
    samples = [
        _make_sample(
            1,
            "S1",
            organism="Homo_sapiens",
            files=[_make_file("S1.spades.fasta"), _make_file("S1.megahit.fasta")],
        )
    ]

    with pytest.raises(SamplesMissingRequiredFieldsError):
        _check(_contract("genomeqc"), samples)


def test_read_columns_are_kept_when_there_is_no_alternative():
    """Dropping them is only right when something else feeds the row. A sample
    with nothing attached still gets the read columns it is expected to fill."""
    csv_text = SampleSheetService.generate_from_contract(
        _contract("sarek"), [_make_sample(1, "S1", donor_source="D1", files=[])], {}
    )

    assert "fastq_1" in _header(csv_text)
