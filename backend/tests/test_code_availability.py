"""plan_7 step 3: keep what the paper said about its analysis code.

"Check if they have code available so we can just plug and chug our way through" is one of the
bioinformaticians' seven asks, and today the feature has nowhere to put the answer: no column, no
prompt, and no mention of github or zenodo anywhere in the literature services.

**This step stores and displays. It does not execute anything.** Running a stranger's repository
against a deposited matrix is a sandboxing and provenance problem of its own. What it buys now is
that a scientist at the C1 gate can see "analysis code: github.com/lab/paper-2024, R" and weigh the
reproduction accordingly, and that a divergence can be attributed to a NAMED difference ("the paper
published its DESeq2 script and we used ours") rather than left unexplained.
"""

import pytest

from app.services.validation_extraction_service import parse_code_availability


def test_reads_a_github_repository():
    out = parse_code_availability(
        [{"kind": "github", "url": "https://github.com/lab/paper-2024", "language": "R", "stated_in": "methods"}]
    )
    assert len(out) == 1
    assert out[0]["kind"] == "github"
    assert out[0]["url"] == "https://github.com/lab/paper-2024"
    assert out[0]["language"] == "R"


def test_a_paper_with_no_code_yields_an_empty_list_not_null():
    """[] means "we asked and the paper named none". NULL means "extracted before this column
    existed". The C1 gate renders those differently and must be able to tell them apart, which is
    the same distinction migration 127 drew for library_strategy."""
    assert parse_code_availability([]) == []
    assert parse_code_availability(None) == []


def test_an_entry_with_no_url_and_no_identifier_is_dropped():
    """A row saying only "code is available on request" is not a location and helps nobody."""
    assert parse_code_availability([{"kind": "github", "url": "", "identifier": ""}]) == []


def test_an_unknown_kind_is_kept_as_other_rather_than_discarded():
    """The location is the valuable part. A hosting service nobody enumerated is still a place the
    code is, and dropping the row to protect a vocabulary would lose it."""
    out = parse_code_availability([{"kind": "bitbucket", "url": "https://bitbucket.org/lab/x"}])
    assert len(out) == 1
    assert out[0]["kind"] == "other"
    assert out[0]["url"] == "https://bitbucket.org/lab/x"


def test_a_non_http_url_is_refused():
    """The gate renders these as links. A javascript: or file: URL is not a code location and must
    never reach an href."""
    assert parse_code_availability([{"kind": "github", "url": "javascript:alert(1)"}]) == []
    assert parse_code_availability([{"kind": "github", "url": "file:///etc/passwd"}]) == []


def test_an_identifier_without_a_url_is_kept():
    """A DOI or an accession is a location even without a URL."""
    out = parse_code_availability([{"kind": "zenodo", "identifier": "10.5281/zenodo.123456"}])
    assert len(out) == 1
    assert out[0]["identifier"] == "10.5281/zenodo.123456"
    assert out[0]["url"] is None


def test_confidence_is_clamped_and_defaulted():
    assert parse_code_availability([{"kind": "github", "url": "https://github.com/a/b"}])[0]["confidence"] == 0.0
    out = parse_code_availability([{"kind": "github", "url": "https://github.com/a/b", "confidence": 5}])
    assert out[0]["confidence"] == 1.0


def test_junk_is_survived():
    for junk in ("not a list", 42, [None], [[]], [{"kind": None}]):
        assert parse_code_availability(junk) == []


def test_duplicate_locations_are_collapsed():
    """A paper naming the same repository in both its methods and its data-availability statement is
    one code location, not two."""
    out = parse_code_availability(
        [
            {"kind": "github", "url": "https://github.com/lab/x", "stated_in": "methods"},
            {"kind": "github", "url": "https://github.com/lab/x", "stated_in": "data availability"},
        ]
    )
    assert len(out) == 1


def test_the_extractor_schema_asks_for_code_availability():
    """The contract lives in the system prompt, so the ask has to be in the schema hint or no
    provider will return it."""
    from app.services.validation_extraction_service import _SCHEMA_HINT

    assert "code_availability" in _SCHEMA_HINT


@pytest.mark.parametrize(
    "url,expected_kind",
    [
        ("https://github.com/lab/x", "github"),
        ("https://gitlab.com/lab/x", "gitlab"),
        ("https://zenodo.org/record/123", "zenodo"),
        ("https://codeocean.com/capsule/123", "codeocean"),
    ],
)
def test_the_kind_is_corrected_from_the_url(url, expected_kind):
    """The URL is evidence and the model's label is a claim, so where they disagree the URL wins.
    Same precedence the depositor's Type takes over a filename in step 1."""
    out = parse_code_availability([{"kind": "supplementary", "url": url}])
    assert out[0]["kind"] == expected_kind
