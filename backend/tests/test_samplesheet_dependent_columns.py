"""Columns a schema requires only once another column is filled.

nf-core schemas express this as ``dependentRequired``: mag declares
``{"short_reads_1": ["short_reads_platform"]}``, meaning a row carrying short
reads must also say which platform produced them.

bioAF ignored the keyword entirely, so it emitted mag sheets carrying
``short_reads_1`` and no ``short_reads_platform`` at all. Those sheets are
rejected by nf-schema minutes after launch, on a rule the user never wrote and
cannot see. It is the exact failure the schema-driven work removed everywhere
else, surviving in a keyword nothing read.

The rule is per ROW, not per sheet: the requirement fires for a sample whose
trigger column is filled, and says nothing about a sample whose is not. A
single-end sample leaves ``short_reads_2`` empty and owes nothing for it.

Three of the nine captured schemas use the keyword (mag, funcscan, sarek), so
this is read from the schema like everything else rather than special-cased.
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


def _reads(name: str, paired: bool = True) -> list:
    files = [_make_file(f"{name}_R1_001.fastq.gz")]
    if paired:
        files.append(_make_file(f"{name}_R2_001.fastq.gz"))
    return files


def _make_sample(sample_id: int, external_id: str, files=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    return sample


def _header(csv_text: str) -> list[str]:
    return csv_text.strip().splitlines()[0].split(",")


def _generate(contract, samples, parameters=None, sample_values=None):
    return SampleSheetService.generate_from_contract(contract, samples, parameters or {}, sample_values=sample_values)


def _check(contract, samples, parameters=None, sample_values=None):
    return SampleSheetService.check_contract_satisfiable(
        contract, samples, parameters or {}, sample_values=sample_values
    )


# -- The keyword is read off the schema --


def test_the_contract_carries_the_dependencies_the_schema_declares():
    contract = _contract("mag")

    assert contract.dependent_required["short_reads_1"] == ("short_reads_platform",)
    assert contract.dependent_required["short_reads_2"] == ("short_reads_1",)


def test_a_schema_declaring_no_dependencies_carries_none():
    assert _contract("rnasplice").dependent_required == {}


# -- A filled trigger makes its dependents required --


def test_mag_blocks_when_it_has_reads_but_no_platform():
    """The defect this fixes. bioAF resolved mag's reads, filled `group`, judged
    the sheet valid and launched it. mag's own schema says a row with
    `short_reads_1` must carry `short_reads_platform`, so nf-schema rejected it
    after the run had already scaled up a node and pulled containers."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("mag"), samples, sample_values={"1": {"group": "gut"}})

    detail = exc.value.details["missing_columns"]["short_reads_platform"]
    assert [s["external_id"] for s in detail["samples"]] == ["GUT_A"]


def test_the_block_names_the_column_that_made_it_required():
    """ "short_reads_platform is missing" is unanswerable on its own: the column
    is optional in the schema's own `required` list. The user can only act on it
    knowing it became required because the row carries short reads."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("mag"), samples, sample_values={"1": {"group": "gut"}})

    detail = exc.value.details["missing_columns"]["short_reads_platform"]
    assert detail["required_by"] == "short_reads_1"
    assert detail["reason"] == "required_by"


def test_the_block_offers_the_values_the_schema_accepts():
    """short_reads_platform is an enum of 11 named platforms. A free-text answer
    is invalid, so the allowed set has to travel with the block."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("mag"), samples, sample_values={"1": {"group": "gut"}})

    assert "ILLUMINA" in exc.value.details["missing_columns"]["short_reads_platform"]["allowed_values"]


def test_supplying_the_platform_unblocks_mag_and_emits_the_column():
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    values = {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}

    _check(_contract("mag"), samples, sample_values=values)
    csv_text = _generate(_contract("mag"), samples, sample_values=values)

    assert "short_reads_platform" in _header(csv_text)
    assert "ILLUMINA" in csv_text


def test_a_platform_the_schema_rejects_does_not_unblock_it():
    """The dependency is satisfied by a value the pipeline accepts, not by any
    value at all. An out-of-enum answer is dropped, so the column is still
    empty and the block stands."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A"))]
    values = {"1": {"group": "gut", "short_reads_platform": "MY_SEQUENCER"}}

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("mag"), samples, sample_values=values)

    assert "short_reads_platform" in exc.value.details["missing_columns"]


# -- An empty trigger requires nothing --


def test_an_unfilled_trigger_column_imposes_nothing():
    """mag also declares short_reads_2 -> short_reads_1. A single-end sample
    leaves short_reads_2 empty, so that dependency must not fire; only the
    platform one does."""
    samples = [_make_sample(1, "GUT_A", files=_reads("GUT_A", paired=False))]
    values = {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}

    _check(_contract("mag"), samples, sample_values=values)


def test_paired_reads_satisfy_the_dependency_between_the_two_mates():
    """sarek declares fastq_2 -> fastq_1. bioAF emits both mates together, so
    this must never fire. It is here to prove the check is not indiscriminate."""
    samples = [_make_sample(1, "TUMOR_A", files=_reads("TUMOR_A"), donor_source="DONOR_1")]

    _check(_contract("sarek"), samples)


def test_a_pipeline_with_no_dependencies_is_untouched():
    """rnasplice declares none. The whole existing catalog runs through this
    path, so a schema without the keyword must behave exactly as before."""
    samples = [_make_sample(1, "A", files=_reads("A"))]
    values = {"1": {"condition": "ctrl", "strandedness": "reverse"}}

    _check(_contract("rnasplice"), samples, sample_values=values)


# -- It holds for file columns too, not just metadata --


def test_funcscan_blocks_when_a_protein_file_arrives_without_its_annotation():
    """funcscan declares protein -> gbk. A sample carrying a `.faa` resolves the
    protein column, and without the matching `.gbk` the row breaks the schema.
    Blocking says so; dropping the protein column would silently discard a file
    the scientist deliberately attached."""
    samples = [
        _make_sample(
            1,
            "ISOLATE_A",
            files=[_make_file("ISOLATE_A.contigs.fasta"), _make_file("ISOLATE_A.proteins.faa")],
        )
    ]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("funcscan"), samples)

    detail = exc.value.details["missing_columns"]["gbk"]
    assert detail["required_by"] == "protein"
    assert [s["external_id"] for s in detail["samples"]] == ["ISOLATE_A"]


def test_funcscan_is_unaffected_when_only_the_assembly_is_attached():
    """The control for the case above: no protein file, no dependency, and the
    launch that step 1 unblocked stays unblocked."""
    samples = [_make_sample(1, "ISOLATE_A", files=[_make_file("ISOLATE_A.contigs.fasta")])]

    _check(_contract("funcscan"), samples)
