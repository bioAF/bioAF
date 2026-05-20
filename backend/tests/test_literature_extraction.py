"""Unit tests for PDF metadata extraction helpers.

The full PDF stack pull is heavy; these tests target the parser and DOI
regex with synthetic strings and a minimal in-memory PDF built via
PyMuPDF. PyMuPDF is already a dependency of the project (thumbnail_service).
"""

from __future__ import annotations


import pytest

from app.services.literature import extraction


def test_find_doi_simple():
    text = "Cite as: doi.org/10.1038/s41592-024-00000-0 etc."
    assert extraction.find_doi(text) == "10.1038/s41592-024-00000-0"


def test_find_doi_returns_none_when_absent():
    assert extraction.find_doi("no doi here") is None


def test_parse_authors_semicolons():
    out = extraction.parse_authors("Chen, Sarah; Mills, Brent C")
    assert out == [
        {"given": "Sarah", "family": "Chen"},
        {"given": "Brent C", "family": "Mills"},
    ]


def test_parse_authors_and_separator():
    out = extraction.parse_authors("John Doe and Jane Smith")
    assert out == [
        {"given": "John", "family": "Doe"},
        {"given": "Jane", "family": "Smith"},
    ]


def test_extract_pdf_metadata_round_trip():
    pytest.importorskip("fitz")
    import fitz  # type: ignore

    doc = fitz.open()
    page = doc.new_page()
    abstract = (
        "Abstract: This study explores tumour heterogeneity in pancreatic ductal "
        "adenocarcinoma using single-cell RNA sequencing on 24 patient samples. "
        "We find that subclonal populations differ in their response to TGF-beta "
        "signalling, suggesting therapeutic windows for combination targeting."
    )
    body = f"Title page contents.\ndoi.org/10.1038/s41592-2026-test\n{abstract}\nIntroduction follows here..."
    page.insert_text((72, 72), body)
    doc.set_metadata({"title": "Tumour heterogeneity in PDAC", "author": "Chen, Sarah; Mills, Brent"})
    out = doc.tobytes()
    doc.close()

    result = extraction.extract_pdf_metadata(out)
    assert result["title"] == "Tumour heterogeneity in PDAC"
    assert result["authors"] == [
        {"given": "Sarah", "family": "Chen"},
        {"given": "Brent", "family": "Mills"},
    ]
    assert result["doi"] == "10.1038/s41592-2026-test"
    assert result["abstract"] is not None
    assert "tumour heterogeneity" in result["abstract"].lower()
    assert result["page_count"] == 1
    assert result["full_text"] is not None
    # Page markers preserve boundaries so the Agent Review can cite the page.
    assert "[Page 1]" in result["full_text"]


def test_extract_pdf_metadata_inserts_page_markers_per_page():
    pytest.importorskip("fitz")
    import fitz  # type: ignore

    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "First page content about alpha results.")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "Second page content about beta results.")
    p3 = doc.new_page()
    p3.insert_text((72, 72), "Third page content about gamma results.")
    out = doc.tobytes()
    doc.close()

    result = extraction.extract_pdf_metadata(out)
    assert result["page_count"] == 3
    text = result["full_text"]
    assert "[Page 1]" in text
    assert "[Page 2]" in text
    assert "[Page 3]" in text
    # Markers are ordered and precede their page's text.
    assert text.index("[Page 1]") < text.index("[Page 2]") < text.index("[Page 3]")
    assert text.index("[Page 2]") < text.index("beta results")
