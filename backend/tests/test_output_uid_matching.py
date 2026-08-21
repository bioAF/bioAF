"""Linking a pipeline output to its sample by identity rather than by name.

bioAF decides which sample an output belongs to by matching text in the output's
path against the sample's name. That holds only while the name in the samplesheet
equals the name in the database, and nothing enforces it: a scientist accepting a
recommended spelling breaks it, and so does a pipeline renaming its own outputs.

An exact identifier removes the disagreement. These pin the identity match, and
just as importantly they pin the fallback: **nothing emits a UID into a
samplesheet yet**, so name matching must go on working exactly as it does today,
and the identity path must be reachable the moment emission ships.

The change is deliberately MONOTONIC. It can add an exact match; it can never
remove a match that works today. See ``test_a_stray_hex_token_does_not_suppress
_name_matching`` for the reason that matters.
"""

import uuid as uuid_pkg

from app.services.asset_identity import sheet_spelling
from app.services.pipeline_output_service import PipelineOutputService

_UID = uuid_pkg.UUID("3f2a9c1b-4d5e-4a7b-8c9d-0e1f2a3b4c5d")
_OTHER = uuid_pkg.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")


def _match(filename, uri, extids=(), uids=()):
    return PipelineOutputService._match_samples(filename, uri, list(extids), list(uids))


# ---------------------------------------------------------------------------
# The identity path
# ---------------------------------------------------------------------------


def test_an_output_carrying_a_sample_identity_links_to_that_sample():
    spelled = sheet_spelling(_UID)

    assert _match(f"{spelled}.bam", f"gs://b/star/{spelled}.bam", uids=[(_UID, 7)]) == [7]


def test_the_identity_wins_over_a_name_that_would_match_another_sample():
    """The whole point. If the sheet carried an identity, that is what the
    pipeline named the file after, and a name that also appears in the path is
    not evidence about a different sample."""
    spelled = sheet_spelling(_UID)

    matched = _match(
        f"{spelled}.bam",
        f"gs://b/SAMPLE-101/{spelled}.bam",
        extids=[("SAMPLE-101", 99)],
        uids=[(_UID, 7)],
    )

    assert matched == [7]


def test_an_output_naming_two_identities_links_to_both():
    """A derived output naming both its inputs belongs to both. Taking the first
    would be a wrong mapping standing in for a partial one."""
    a, b = sheet_spelling(_UID), sheet_spelling(_OTHER)

    matched = _match(f"{a}__{b}.bam", f"gs://b/pairs/{a}__{b}.bam", uids=[(_UID, 7), (_OTHER, 8)])

    assert sorted(matched) == [7, 8]


def test_an_identity_is_found_in_either_layout_a_pipeline_uses():
    """Observed on the demo: nf-core/demo puts the value in a directory segment
    AND the filename, bamtofastq puts it in the filename only. A rule written for
    one passes there and fails silently on the other."""
    spelled = sheet_spelling(_UID)

    segment = _match("x_1_fastqc.html", f"gs://b/fastqc/{spelled}/x_1_fastqc.html", uids=[(_UID, 7)])
    filename = _match(f"{spelled}.flagstat", f"gs://b/samtools/{spelled}.flagstat", uids=[(_UID, 7)])

    assert segment == [7]
    assert filename == [7]


# ---------------------------------------------------------------------------
# The fallback, which is still the live path until emission ships
# ---------------------------------------------------------------------------


def test_name_matching_is_unchanged_when_no_identity_is_present():
    assert _match("SAMPLE-101.bam", "gs://b/star/SAMPLE-101.bam", extids=[("SAMPLE-101", 3)]) == [3]
    assert _match("x.bam", "gs://b/star/SAMPLE-101/x.bam", extids=[("SAMPLE-101", 3)]) == [3]


def test_an_output_naming_nothing_still_links_to_nothing():
    """Its absence of any identifier is what makes it a run-level artifact."""
    assert _match("multiqc_report.html", "gs://b/multiqc/multiqc_report.html", extids=[("SAMPLE-101", 3)]) == []


def test_a_stray_hex_token_does_not_suppress_name_matching():
    """The reason the identity path is conditioned on belonging to THIS run.

    The spelling is ``s`` plus 32 lowercase hex, and an md5 is also 32 hex, so a
    path like ``s<md5>.tmp`` parses as an identity that belongs to nothing. If a
    found-but-unknown identity suppressed name matching, that coincidence would
    turn a correctly matched per-sample file into an unattributed one, which is a
    regression rather than a fix.
    """
    stray = "s" + "d41d8cd98f00b204e9800998ecf8427e"

    matched = _match(
        "SAMPLE-101.bam",
        f"gs://b/cache/{stray}/SAMPLE-101.bam",
        extids=[("SAMPLE-101", 3)],
        uids=[(_UID, 7)],
    )

    assert matched == [3]


def test_an_identity_belonging_to_another_run_does_not_link_here():
    """It names something real, but not something in this run, and the sample
    names present are what bioAF can still act on."""
    foreign = sheet_spelling(_OTHER)

    matched = _match("out.bam", f"gs://b/{foreign}/out.bam", extids=[("SAMPLE-101", 3)], uids=[(_UID, 7)])

    assert matched == []


def test_the_old_signature_still_works():
    """Every existing caller passes three arguments. Breaking them to add an
    identity path would be a regression dressed as a fix."""
    assert PipelineOutputService._match_samples("SAMPLE-101.bam", "gs://b/SAMPLE-101.bam", [("SAMPLE-101", 3)]) == [3]
