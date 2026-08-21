"""A row bioAF is about to emit with a required column left empty.

Required-column checking was per SAMPLE, and the sheet is per ROW. A sample
sequenced over two lanes produces two rows, and a sample carrying only an R2
produces one row with ``fastq_1`` empty, and ``fastq_1`` is required. Nothing
blocked it, because the hole is structural and falls between all three of the
checks that exist:

- ``column_gaps`` skips required read columns outright (``if column in
  read_columns: continue``),
- ``_unusable_reads_gap`` is documented as deliberately narrow and fires only
  when NO attached file qualifies as a read, and this sample has one that does,
- the launch path's own gate fires only when a sample has no files at all.

So bioAF emitted ``A,,gs://...R2``, which nf-schema rejects after the node has
scaled up and the containers have pulled. That is exactly the cost these checks
exist to avoid.

The rule is narrow on purpose: an emitted row with an empty REQUIRED column
blocks, naming the sample and the column. It cannot refuse a launch that works
today, because a row with an empty required column is one nf-schema rejects
anyway. In particular SINGLE-END input stays legal: ``fastq_2`` is not required
by nf-core/demo, so an R1-only sample yields nothing to report. An earlier
version of this analysis offered an R1-only sample as evidence of the hole. It is
not, and the wrong version must not be re-derived.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.exceptions import SamplesMissingRequiredFieldsError
from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _read(name: str, read_type: str, lane: int | None = None):
    return SimpleNamespace(
        storage_uri=f"gs://bucket/{name}",
        filename=name,
        tags_json=[],
        lane=lane,
        read_type=read_type,
        flowcell_id=None,
        index_sequence=None,
        source_run_accession=None,
    )


def _sample(sample_id: int, external_id: str, files: list):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition"):
        setattr(sample, attr, None)
    sample.files = files
    sample._input_files = files
    return sample


def _check(contract, samples, parameters=None):
    return SampleSheetService.check_contract_satisfiable(contract, samples, parameters or {})


def test_a_sample_carrying_only_an_r2_blocks():
    """The verified hole. The sample HAS a file, and that file DOES qualify as a
    read, so every existing check passes it through and the sheet carries an
    empty required column."""
    sample = _sample(1, "SAMPLE-A", [_read("A_S1_L001_R2_001.fastq.gz", "R2", lane=1)])

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("demo"), [sample])

    assert "fastq_1" in exc.value.details["missing_columns"]


def test_the_block_names_the_sample_whose_row_is_incomplete():
    """A block the scientist cannot act on is barely better than the crash it
    replaces, so it says which sample and which column."""
    good = _sample(1, "SAMPLE-GOOD", [_read("G_R1.fastq.gz", "R1"), _read("G_R2.fastq.gz", "R2")])
    bad = _sample(2, "SAMPLE-BAD", [_read("B_S1_L001_R2_001.fastq.gz", "R2", lane=1)])

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("demo"), [good, bad])

    named = exc.value.details["missing_columns"]["fastq_1"]["samples"]
    assert [s["external_id"] for s in named] == ["SAMPLE-BAD"]


def test_single_end_input_is_not_blocked():
    """fastq_2 is not required by nf-core/demo, so an R1-only sample is legal
    single-end input and must stay launchable. This is the check that keeps the
    new rule from refusing work that succeeds today."""
    sample = _sample(1, "SAMPLE-SE", [_read("SE_S1_L001_R1_001.fastq.gz", "R1", lane=1)])

    _check(_contract("demo"), [sample])


def test_a_complete_pair_is_not_blocked():
    sample = _sample(
        1,
        "SAMPLE-PE",
        [_read("P_S1_L001_R1_001.fastq.gz", "R1", lane=1), _read("P_S1_L001_R2_001.fastq.gz", "R2", lane=1)],
    )

    _check(_contract("demo"), [sample])


def test_one_bad_lane_blocks_a_sample_whose_other_lane_is_complete():
    """The per-SAMPLE check could never see this: the sample has R1s, R2s, and
    files that qualify as reads. Only the second ROW is incomplete."""
    sample = _sample(
        1,
        "SAMPLE-2L",
        [
            _read("X_S1_L001_R1_001.fastq.gz", "R1", lane=1),
            _read("X_S1_L001_R2_001.fastq.gz", "R2", lane=1),
            _read("X_S1_L002_R2_001.fastq.gz", "R2", lane=2),
        ],
    )

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("demo"), [sample])

    assert "fastq_1" in exc.value.details["missing_columns"]


def test_the_gap_says_the_row_is_incomplete_rather_than_the_field_being_absent():
    """The remedy differs from an ordinary missing column, so the reason has to.
    "bioAF cannot derive this" sends the scientist to fill in a field; what is
    actually needed is the file that belongs in this row."""
    sample = _sample(1, "SAMPLE-A", [_read("A_S1_L001_R2_001.fastq.gz", "R2", lane=1)])

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("demo"), [sample])

    assert exc.value.details["missing_columns"]["fastq_1"]["reason"] == "empty_in_row"


def test_a_sample_with_no_reads_at_all_still_reports_the_narrower_gap():
    """A sample whose files cannot serve as reads is a different situation with
    its own report, and this must not displace it: a broader message that fires
    first would bury the specific one."""
    sample = _sample(1, "SAMPLE-BAM", [_read("aligned.bam", None)])

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("demo"), [sample])

    assert exc.value.details["missing_columns"]["fastq_1"]["reason"] == "no_matching_file"


def test_a_sample_with_no_files_at_all_is_left_to_the_launch_path():
    """A different situation with a different remedy, and one the launch path
    already owns: it blocks, or drops those samples and proceeds when asked. This
    check must not duplicate it, or it would refuse launches that work today and
    report the same problem twice under a reason that does not fit it.

    The hole this whole module exists for is the opposite case: a sample that
    HAS a file, whose file DOES qualify as a read, and whose row is still
    incomplete.
    """
    sample = _sample(1, "SAMPLE-EMPTY", [])

    _check(_contract("demo"), [sample])


def test_a_pipeline_with_no_contract_is_untouched():
    """No schema means "we do not know", and refusing on ignorance would regress
    every pipeline that works today."""
    sample = _sample(1, "SAMPLE-A", [_read("A_S1_L001_R2_001.fastq.gz", "R2", lane=1)])

    _check(parse_contract({}), [sample])
