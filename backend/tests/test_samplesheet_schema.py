"""The samplesheet contract parser, against real nf-core schemas.

Samplesheet generation used to be keyed on a substring of the pipeline name, so
52% of the catalog received a sheet missing at least one required column and died
inside Nextflow minutes after launch. It is now keyed on the pipeline's own
``assets/schema_input.json``, the same move the generic QC engine made from
pipeline templates to MultiQC modules.

These assert what the parser CONCLUDES from a schema, not how it walks it.
"""

import json
from pathlib import Path

from app.services.samplesheet_schema import SamplesheetContract, parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# -- Required columns and properties --


def test_parses_sareks_required_columns():
    """nf-core/sarek 3.9.0 requires patient and sample."""
    c = parse_contract(_fixture("sarek"))

    assert c.required == {"patient", "sample"}
    assert "fastq_1" in c.columns
    assert "fastq_2" in c.columns


def test_parses_demos_required_columns():
    """nf-core/demo 1.2.0: the minimal valid case, already working today."""
    c = parse_contract(_fixture("demo"))

    assert c.required == {"sample", "fastq_1"}
    assert c.columns == {"sample", "fastq_1", "fastq_2"}


# -- Sample-launchable classification --


def test_pipeline_defining_fastq_is_sample_launchable():
    """A schema with a fastq_1 column consumes per-sample reads."""
    assert parse_contract(_fixture("sarek")).is_sample_launchable is True
    assert parse_contract(_fixture("demo")).is_sample_launchable is True
    assert parse_contract(_fixture("mag")).is_sample_launchable is True


def test_read_columns_are_found_by_pattern_not_by_name():
    """nf-core/mag names its read columns short_reads_1/short_reads_2/long_reads,
    not fastq_N. It declares the FASTQ extension in each column's own `pattern`,
    which is the pipeline stating the fact machine-readably. Detection must
    follow the declaration, not the spelling: a name-only rule misclassifies mag
    as unlaunchable and refuses a pipeline that works."""
    c = parse_contract(_fixture("mag"))

    assert c.read_columns == {"short_reads_1", "short_reads_2", "long_reads"}
    assert c.is_sample_launchable is True


def test_read_columns_fall_back_to_name_when_no_pattern_is_declared():
    """nf-core/bacass declares no pattern on R1/R2, so the name carries it.
    This fallback is deliberately tiny; it exists for exactly this case."""
    c = parse_contract(_fixture("bacass"))

    assert {"R1", "R2"} <= c.read_columns
    assert c.is_sample_launchable is True


def test_a_fasta_pattern_is_not_mistaken_for_reads():
    """genomeqc declares both a `fasta` column (^\\S+\\.(fa|fasta|fna)...) and a
    `fastq` one. Only the latter is reads; a looser extension match would call
    every assembly pipeline sample-launchable."""
    c = parse_contract(_fixture("genomeqc"))

    assert "fastq" in c.read_columns
    assert "fasta" not in c.read_columns
    assert "gff" not in c.read_columns


def test_pipeline_requiring_fasta_is_sample_launchable():
    """nf-core/funcscan takes assemblies, not reads. That used to mean "refuse",
    on the assumption bioAF could only hold FASTQ. It holds arbitrary files per
    sample, so a sample carrying an assembly can feed it.

    Launchability is a property of the PIPELINE (does it want a per-sample file
    at all); whether THESE samples satisfy it is decided per sample, so the user
    is told which column and which samples rather than being turned away."""
    c = parse_contract(_fixture("funcscan"))

    assert c.is_sample_launchable is True
    assert "fasta" in c.required
    assert "fasta" in c.file_columns


def test_a_pipeline_wanting_no_per_sample_file_is_not_sample_launchable():
    """The refusal is narrowed, not removed. A pipeline whose required input is a
    bare identifier cannot be fed from samples however many files are attached,
    and the message still has to say what it wants or the user cannot act."""
    c = parse_contract(
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

    assert c.is_sample_launchable is False
    assert c.required_non_fastq_inputs == ["taxid"]


# -- Enum discipline --


def test_captures_enum_constraints():
    """taxprofiler's instrument_platform accepts 11 named platforms, so a
    free-text fill is invalid even though the column is 'present'."""
    c = parse_contract(_fixture("taxprofiler"))

    assert "ILLUMINA" in c.enum_for("instrument_platform")
    assert "NOT_A_PLATFORM" not in c.enum_for("instrument_platform")


def test_rnasplice_strandedness_enum_excludes_bioafs_auto_default():
    """bioAF defaults strandedness to 'auto'. That is legal for nf-core/rnaseq
    and illegal here, which is why the default cannot be blindly reused."""
    c = parse_contract(_fixture("rnasplice"))

    assert "auto" not in c.enum_for("strandedness")
    assert set(c.enum_for("strandedness")) == {"forward", "reverse", "unstranded"}


def test_enum_for_returns_empty_when_unconstrained():
    """A column with no enum accepts anything; callers must not treat the empty
    list as 'nothing is allowed'."""
    assert parse_contract(_fixture("sarek")).enum_for("patient") == []
    assert parse_contract(_fixture("sarek")).enum_for("no_such_column") == []


# -- Defaults --


def test_column_with_a_default_is_not_treated_as_missing():
    """nf-schema fills a defaulted column, so its absence from bioAF's output is
    not a launch blocker."""
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["sample", "fastq_1", "replicate"],
            "properties": {
                "sample": {"type": "string"},
                "fastq_1": {"type": "string"},
                "replicate": {"type": "integer", "default": 1},
            },
        },
    }
    c = parse_contract(schema)

    assert "replicate" in c.required
    assert c.defaulted == {"replicate"}
    assert c.required_without_default == {"sample", "fastq_1"}


# -- Malformed and absent input never raises into the launch path --


def test_unrecognized_layout_yields_an_empty_contract():
    """A schema with no items.properties is not a contract we understand. Treat
    it as absent and fall back, never raise into a launch."""
    c = parse_contract({"type": "object", "properties": {"whatever": {}}})

    assert c.is_empty is True
    assert c.required == set()


def test_none_yields_an_empty_contract():
    assert parse_contract(None).is_empty is True


def test_garbage_yields_an_empty_contract():
    for junk in ("", [], 42, {"items": "not-an-object"}, {"items": {"properties": []}}):
        assert parse_contract(junk).is_empty is True


def test_empty_contract_is_not_sample_launchable_but_does_not_block():
    """An empty contract means 'we do not know', which must fall back to the old
    heuristic rather than assert either answer."""
    c = parse_contract(None)

    assert c.is_empty is True
    assert isinstance(c, SamplesheetContract)
