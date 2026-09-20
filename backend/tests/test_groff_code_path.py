"""plan_8_5 section 3.5 and gate 2: the paper's own R, from the document it was supplied in.

Study 55 supplied every line of its analysis, as five R Markdown documents inside a Word file.
bioAF recorded the file as unreadable and left all twenty code points grey, which reads as a
statement about the paper and was a statement about bioAF.

This is the whole path on the committed fixture: the bytes, the DOCX extractor already in the
product, the chunks, an actual R parser, and the obligations those establish.
"""

import pathlib

import pytest

from app.services.validation_code_checks import assess_code
from app.services.validation_code_inspection import inspect_code

_DOCX = pathlib.Path(__file__).parent / "fixtures" / "groff" / "supplemental_file_2_allrcode.docx"


@pytest.fixture(scope="module")
def inspected():
    return inspect_code(
        [{"filename": "AllRCode.docx", "role": "code"}],
        bytes_for={"AllRCode.docx": _DOCX.read_bytes()},
    )


class TestTheDocumentIsReadBackIntoItsChunks:
    def test_every_r_markdown_document_becomes_one_source(self, inspected):
        assert len(inspected["sources"]) == 5
        assert {s["language"] for s in inspected["sources"]} == {"r"}
        assert inspected["unreadable"] == []

    def test_the_chunks_are_kept_with_their_places_in_the_document(self, inspected):
        segments = [s for source in inspected["sources"] for s in source["segments"]]
        assert len(segments) == 100
        assert all(s["line"] > 0 for s in segments)
        assert all(s["code"].strip() for s in segments)

    def test_two_documents_with_the_same_title_are_still_two_documents(self, inspected):
        paths = [s["path"] for s in inspected["sources"]]
        assert len(set(paths)) == len(paths)

    def test_the_provenance_says_what_it_was_extracted_from(self, inspected):
        provenance = inspected["sources"][0]["provenance"]
        assert provenance["container"] == "AllRCode.docx"
        assert provenance["sha256"]
        assert "word-processed" in provenance["extracted"]


class TestWhatTheCodeSectionEstablishesForThisPaper:
    def test_the_supplied_r_parses(self, inspected):
        found = assess_code(sources=inspected["sources"])["C1.A"]
        assert found["outcome"] == "verified"
        assert "R parser" in found["evidence"]["parser"]

    def test_the_analysis_declares_the_packages_it_uses(self, inspected):
        found = assess_code(sources=inspected["sources"])["C2.A"]
        assert found["outcome"] == "verified"
        assert "DESeq2" in str(found["evidence"])

    def test_the_paths_it_reads_from_are_a_named_defect(self, inspected):
        """A defect the pipeline found in the source itself, not one it was handed."""
        found = assess_code(sources=inspected["sources"])["C4.B"]
        assert found["outcome"] == "failed"
        assert any("/Users/" in p or p.startswith("~/") for p in found["evidence"]["paths"])
        assert found["impact"]

    def test_the_obligations_that_need_a_run_stay_behind_the_approval(self, inspected):
        assessed = assess_code(sources=inspected["sources"])
        assert assessed["C1.B"]["outcome"] == "undetermined"
        assert assessed["C2.B"]["outcome"] == "undetermined"

    def test_the_code_section_is_no_longer_entirely_grey(self, inspected):
        from app.services.validation_rubric_v3 import allocate, default_profile, score

        assessed = assess_code(sources=inspected["sources"])
        card = score(allocate(default_profile()), assessed)
        assert card["sections"]["C"]["verified"] > 0
        assert card["sections"]["C"]["failed"] > 0
        assert card["sections"]["C"]["undetermined"] > 0
