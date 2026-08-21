"""The samplesheet spelling of an identity, and finding it again in an output path.

bioAF matches an output to a sample by looking for text in the output's path. That
holds only while the name in the sheet equals the name bioAF searches for, and
nothing enforces it. An identifier removes the disagreement, but it cannot be a
raw UUID: ampliseq requires ``^[a-zA-Z][a-zA-Z0-9_]+$``, which a UUID fails on its
hyphens and on a leading digit.

These pin the spelling and the recovery. Nothing emits a UID into a sheet yet.
"""

import re
import uuid as uuid_pkg
from pathlib import Path

import pytest

from app.services.asset_identity import (
    parse_sheet_spelling,
    sheet_spelling,
    uids_in,
)

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"

# Columns a pipeline uses to identify the row's subject, which is where a UID goes.
_IDENTITY_COLUMNS = {"sample", "sample_id", "id", "patient", "sample_name"}

_UID = uuid_pkg.UUID("3f2a9c1b-4d5e-4a7b-8c9d-0e1f2a3b4c5d")


def test_the_spelling_is_a_letter_then_bare_hex():
    assert sheet_spelling(_UID) == "s3f2a9c1b4d5e4a7b8c9d0e1f2a3b4c5d"


def test_the_spelling_carries_no_hyphen_and_starts_with_a_letter():
    """The two things that get a value rejected by the strictest schema in the
    catalog. A UUID fails both."""
    spelled = sheet_spelling(_UID)

    assert "-" not in spelled
    assert spelled[0].isalpha()


def test_every_identity_column_in_the_catalog_accepts_the_spelling():
    """The claim the design rests on, checked against the stored contracts rather
    than asserted. If a pipeline is added whose identity pattern rejects this,
    this test is where that is discovered, before a launch does it."""
    spelled = sheet_spelling(_UID)
    rejected = []

    for path in sorted(FIXTURES.glob("*.json")):
        import json

        schema = json.loads(path.read_text())
        for column, spec in (schema.get("items", {}).get("properties", {}) or {}).items():
            if column.lower() not in _IDENTITY_COLUMNS:
                continue
            pattern = spec.get("pattern")
            if pattern and not re.compile(pattern).match(spelled):
                rejected.append(f"{path.stem}.{column} ({pattern})")

    assert rejected == [], "identity columns that would reject the UID spelling:\n" + "\n".join(rejected)


def test_a_spelling_round_trips_to_the_identity_it_denotes():
    assert parse_sheet_spelling(sheet_spelling(_UID)) == _UID


def test_a_name_that_is_not_a_spelling_denotes_nothing():
    """A scientist's own sample name must never be read as an identifier."""
    for text in ("SAMPLE-101", "s_not_hex", "", "sample_123", "3f2a9c1b4d5e4a7b8c9d0e1f2a3b4c5d"):
        assert parse_sheet_spelling(text) is None


def test_a_uid_is_found_as_a_path_segment():
    """nf-core/demo's layout: the identity is its own directory."""
    spelled = sheet_spelling(_UID)

    assert uids_in(f"gs://b/fastqc/{spelled}/{spelled}_1_fastqc.html") == {_UID}


def test_a_uid_is_found_as_a_filename_prefix():
    """nf-core/bamtofastq's layout: the identity is only in the filename, and the
    directory is the tool. A rule written for the segment form alone passes on
    demo and fails silently here, which is the failure this project removes."""
    spelled = sheet_spelling(_UID)

    assert uids_in(f"gs://b/samtools/{spelled}.flagstat") == {_UID}


def test_a_uid_is_found_mid_filename():
    """A third layout neither pipeline used, which a bounded token match covers
    for free and a positional rule would not."""
    spelled = sheet_spelling(_UID)

    assert uids_in(f"gs://b/reads/trimmed_{spelled}_R1.fq.gz") == {_UID}


def test_two_identities_in_one_path_are_both_found():
    """Taking the first would attach a derived output to one of its inputs and
    not the other, which is a wrong mapping standing in for a partial one."""
    other = uuid_pkg.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")

    found = uids_in(f"gs://b/pairs/{sheet_spelling(_UID)}__{sheet_spelling(other)}.bam")

    assert found == {_UID, other}


def test_a_path_naming_no_identity_yields_nothing():
    """A run-level report. Its ABSENCE of an identity is the signal that it
    belongs to the run rather than to a sample."""
    assert uids_in("gs://b/multiqc/multiqc_report.html") == set()
    assert uids_in("gs://b/fastqc/SAMPLE-101/SAMPLE-101_1_fastqc.html") == set()


def test_a_longer_hex_run_is_not_mistaken_for_an_identity():
    """A content hash in a filename is not an identity, and reading one as an
    identity would attach a file to a sample chosen by coincidence."""
    assert uids_in("gs://b/cache/s" + "a" * 40 + ".bin") == set()


@pytest.mark.parametrize("value", [uuid_pkg.uuid4() for _ in range(5)])
def test_any_generated_identity_survives_the_round_trip(value):
    assert parse_sheet_spelling(sheet_spelling(value)) == value
