"""Unit tests for the Naming Profile parser.

The parser is the heart of the Naming Profile feature: it reads a filename
against a profile and returns a typed map of the fields the profile
recognized, plus any unrecognized tokens and warnings. It is a pure
function with no DB / filesystem / network side effects.

See local/Naming Profiles/spec-parser.md for the full contract.
"""

import inspect

import pytest

from app.services.naming_profile_parser import parse_filename


def _make_profile(
    segments,
    delimiter="_",
    strip_extension=True,
    profile_id=1,
    name="TestProfile",
):
    """Build a minimal duck-typed profile object for parser unit tests.

    Per the spec, the parser only needs `segments_json`, `delimiter`, and
    `strip_extension` off the profile. Tests pass plain dicts in
    `segments` (matching the SegmentDefinition shape) so they read as the
    on-disk JSON does.
    """

    class _Profile:
        def __init__(self):
            self.id = profile_id
            self.name = name
            self.delimiter = delimiter
            self.strip_extension = strip_extension
            self.segments_json = segments

    return _Profile()


def _seg(
    field_name,
    field_type,
    identifier=None,
    position=0,
    padding=None,
    date_format=None,
    is_system_chip=False,
):
    """Helper to build a SegmentDefinition-shaped dict."""
    return {
        "position": position,
        "identifier": identifier,
        "field_name": field_name,
        "field_type": field_type,
        "padding": padding,
        "date_format": date_format,
        "is_system_chip": is_system_chip,
    }


# ---------------------------------------------------------------------------
# Signature / purity guards
# ---------------------------------------------------------------------------


def test_parser_has_no_db_session_argument():
    """The parser is pure; no db / session argument may be added."""
    sig = inspect.signature(parse_filename)
    forbidden = {"db", "session", "conn", "engine"}
    assert not (forbidden & set(sig.parameters)), f"parse_filename signature must stay pure; saw {set(sig.parameters)}"


def test_parser_returns_documented_shape():
    profile = _make_profile([_seg("requestor", "string", identifier="req", position=0)])
    result = parse_filename("req-bmills.txt", profile)
    assert set(result) == {"parsed", "unrecognized", "warnings"}
    assert isinstance(result["parsed"], dict)
    assert isinstance(result["unrecognized"], list)
    assert isinstance(result["warnings"], list)


# ---------------------------------------------------------------------------
# Number segments
# ---------------------------------------------------------------------------


def test_parses_single_number_segment():
    profile = _make_profile([_seg("SampleID", "number", identifier="SMP", padding=2, position=0)])
    result = parse_filename("SMP0042.txt", profile)
    assert result["parsed"] == {"SampleID": "0042"}
    assert result["unrecognized"] == []


def test_number_value_preserves_leading_zeros():
    profile = _make_profile([_seg("Batch", "number", identifier="B", padding=3, position=0)])
    result = parse_filename("B007.txt", profile)
    assert result["parsed"]["Batch"] == "007"


def test_lenient_padding_under_width():
    """A number segment whose value has fewer digits than padding still binds."""
    profile = _make_profile([_seg("SampleID", "number", identifier="SMP", padding=3, position=0)])
    result = parse_filename("SMP4.txt", profile)
    assert result["parsed"]["SampleID"] == "4"
    assert result["warnings"] == []


def test_lenient_padding_over_width():
    """A number segment whose value has more digits than padding still binds."""
    profile = _make_profile([_seg("SampleID", "number", identifier="SMP", padding=2, position=0)])
    result = parse_filename("SMP12345.txt", profile)
    assert result["parsed"]["SampleID"] == "12345"


def test_letters_only_no_digits_is_unrecognized():
    """`SMP` alone (no digit portion) is unrecognized, not an error."""
    profile = _make_profile([_seg("SampleID", "number", identifier="SMP", padding=2, position=0)])
    result = parse_filename("SMP.txt", profile)
    assert "SampleID" not in result["parsed"]
    assert "SMP" in result["unrecognized"]


# ---------------------------------------------------------------------------
# String segments
# ---------------------------------------------------------------------------


def test_parses_single_string_segment():
    profile = _make_profile(
        [_seg("Requestor", "string", identifier="req", position=0)],
        delimiter="_",
    )
    # delimiter "_" implies inner separator "-"
    result = parse_filename("req-bmills.txt", profile)
    assert result["parsed"] == {"Requestor": "bmills"}


def test_string_value_contains_inner_separator_split_first_only():
    """`req-bmills-jr` parses to identifier `req` and value `bmills-jr`."""
    profile = _make_profile(
        [_seg("Requestor", "string", identifier="req", position=0)],
        delimiter="_",
    )
    result = parse_filename("req-bmills-jr.txt", profile)
    assert result["parsed"] == {"Requestor": "bmills-jr"}


def test_string_inner_separator_with_hyphen_delimiter():
    """Delimiter `-` implies inner separator `_`."""
    profile = _make_profile(
        [_seg("Requestor", "string", identifier="req", position=0)],
        delimiter="-",
    )
    result = parse_filename("req_bmills.txt", profile)
    assert result["parsed"] == {"Requestor": "bmills"}


# ---------------------------------------------------------------------------
# Date segments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "date_format,token,iso",
    [
        ("YYYYMMDD", "20260602", "2026-06-02"),
        ("YYYY-MM-DD", "2026-06-02", "2026-06-02"),
        ("YYMMDD", "260602", "2026-06-02"),
    ],
)
def test_parses_each_date_format(date_format, token, iso):
    """All three date formats produce ISO YYYY-MM-DD output."""
    # YYYY-MM-DD uses underscore delimiter to avoid the collision case
    # (which has its own test below).
    profile = _make_profile(
        [_seg("RunDate", "date", date_format=date_format, position=0)],
        delimiter="_",
    )
    result = parse_filename(f"{token}.txt", profile)
    assert result["parsed"]["RunDate"] == iso


def test_date_recombination_when_delimiter_is_hyphen():
    """Delimiter `-` plus YYYY-MM-DD: parser recombines `2026-06-02` into one date."""
    profile = _make_profile(
        [_seg("RunDate", "date", date_format="YYYY-MM-DD", position=0)],
        delimiter="-",
    )
    result = parse_filename("2026-06-02.txt", profile)
    assert result["parsed"]["RunDate"] == "2026-06-02"


def test_ambiguous_date_triples_emit_warning_and_pick_first():
    """If a filename has two date triples, parser picks the first and warns."""
    profile = _make_profile(
        [_seg("RunDate", "date", date_format="YYYY-MM-DD", position=0)],
        delimiter="-",
    )
    result = parse_filename("2026-06-02-2027-07-03.txt", profile)
    assert result["parsed"]["RunDate"] == "2026-06-02"
    assert any("ambiguous" in w.lower() for w in result["warnings"])


def test_date_token_in_profile_without_date_segment_is_unrecognized():
    profile = _make_profile([_seg("SampleID", "number", identifier="SMP", padding=2, position=0)])
    result = parse_filename("SMP0042_20260602.txt", profile)
    assert "20260602" in result["unrecognized"]
    assert result["parsed"] == {"SampleID": "0042"}


# ---------------------------------------------------------------------------
# Order independence
# ---------------------------------------------------------------------------


def test_reordered_segments_produce_same_result():
    """Identifier letters drive parsing; segment order in the filename is irrelevant."""
    profile = _make_profile(
        [
            _seg("SampleID", "number", identifier="SMP", padding=2, position=0),
            _seg("Requestor", "string", identifier="req", position=1),
            _seg("RunDate", "date", date_format="YYYYMMDD", position=2),
        ],
        delimiter="_",
    )

    a = parse_filename("SMP0042_req-bmills_20260602.txt", profile)
    b = parse_filename("20260602_req-bmills_SMP0042.txt", profile)
    c = parse_filename("req-bmills_20260602_SMP0042.txt", profile)
    assert a["parsed"] == b["parsed"] == c["parsed"]
    assert a["parsed"] == {
        "SampleID": "0042",
        "Requestor": "bmills",
        "RunDate": "2026-06-02",
    }


# ---------------------------------------------------------------------------
# Unrecognized handling, strip_extension, case-insensitive match
# ---------------------------------------------------------------------------


def test_unrecognized_tokens_reported_no_raise():
    profile = _make_profile([_seg("SampleID", "number", identifier="SMP", padding=2, position=0)])
    result = parse_filename("SMP0042_garbage_morejunk.txt", profile)
    assert result["parsed"] == {"SampleID": "0042"}
    assert "garbage" in result["unrecognized"]
    assert "morejunk" in result["unrecognized"]


def test_strip_extension_true_removes_suffix():
    profile = _make_profile(
        [_seg("SampleID", "number", identifier="SMP", padding=2, position=0)],
        strip_extension=True,
    )
    result = parse_filename("SMP0042.fastq.gz", profile)
    assert result["parsed"]["SampleID"] == "0042"


def test_strip_extension_false_keeps_suffix():
    """With strip_extension=False, the extension stays attached to the last token."""
    profile = _make_profile(
        [_seg("SampleID", "number", identifier="SMP", padding=2, position=0)],
        strip_extension=False,
    )
    result = parse_filename("SMP0042.fastq.gz", profile)
    # `SMP0042.fastq.gz` is a single token but doesn't match number shape
    # because trailing non-digits; expected as unrecognized.
    assert "SMP0042.fastq.gz" in result["unrecognized"]
    assert "SampleID" not in result["parsed"]


def test_case_insensitive_identifier_match():
    """Authored `SMP` matches `smp0042` in a filename, case-insensitively."""
    profile = _make_profile([_seg("SampleID", "number", identifier="SMP", padding=2, position=0)])
    result = parse_filename("smp0042.txt", profile)
    assert result["parsed"]["SampleID"] == "0042"


# ---------------------------------------------------------------------------
# Multi-segment realistic example
# ---------------------------------------------------------------------------


def test_realistic_multi_segment_filename():
    profile = _make_profile(
        [
            _seg("ProjectCode", "number", identifier="PRJ", padding=2, position=0, is_system_chip=True),
            _seg("SampleID", "number", identifier="SMP", padding=2, position=1, is_system_chip=True),
            _seg("Read", "number", identifier="R", padding=0, position=2),
            _seg("RunDate", "date", date_format="YYYYMMDD", position=3),
        ],
        delimiter="_",
    )
    result = parse_filename("PRJ01_SMP0042_R1_20260602.fastq.gz", profile)
    assert result["parsed"] == {
        "ProjectCode": "01",
        "SampleID": "0042",
        "Read": "1",
        "RunDate": "2026-06-02",
    }
    assert result["unrecognized"] == []
