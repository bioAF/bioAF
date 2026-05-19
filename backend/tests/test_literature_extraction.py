"""Unit tests for PDF metadata extraction helpers.

The full PDF stack pull is heavy; these tests target the parser and DOI
regex with synthetic strings and a minimal in-memory PDF built via
PyMuPDF. PyMuPDF is already a dependency of the project (thumbnail_service).
"""

from __future__ import annotations

from datetime import date

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
    body = (
        "Title page contents.\n"
        "doi.org/10.1038/s41592-2026-test\n"
        f"{abstract}\n"
        "Introduction follows here..."
    )
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
