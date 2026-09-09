"""change_7.1 section 2: find the paper's own attachments, independently of GEO.

Supplement discovery did not exist. Code availability came from what the model read in the prose,
the deposit inventory only ever listed GEO, and an article's own attachments were never looked at.
For Groff et al. that lost a 54-row sample metadata table, the authors' R code, and the
differential-results table, all of them public the whole time.

**A reference and an attachment are different things.** The paper's prose says "Supplemental File
S2"; the bytes live in a bundle. Naming the first is free at read time and resolving it to the
second costs a download, so the inventory carries both states and says which one it is in.

**An unresolved reference is a discovery limitation, never an established absence.** "We have not
fetched it yet" and "it is not there" are different statements, and only one of them is a finding
about the authors.

Fixtures are the live 2026-09-08 public evidence for `10.1101/gr.252981.119`: the Europe PMC JATS
and the three supplemental files as deposited.
"""

import pathlib

import pytest

from app.services.supplement_inventory import (
    CODE,
    NAMED_IN_TEXT,
    RESULTS_TABLE,
    SAMPLE_METADATA,
    UNKNOWN_ROLE,
    classify_supplement,
    extract_docx_text,
    parse_jats_supplements,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "groff"
_JATS = (_FIXTURES / "fulltext_jats.xml").read_text()
_S1 = (_FIXTURES / "supplemental_file_1_embryo_metadata.txt").read_bytes()
_S2 = (_FIXTURES / "supplemental_file_2_allrcode.docx").read_bytes()
_S3 = (_FIXTURES / "supplemental_file_3_siggenes.txt").read_bytes()


class TestTheManifestIsFreeAtReadTime:
    def test_every_supplement_the_paper_names_is_listed(self):
        """S1, S2 and S3 are named in the prose and nowhere else in the JATS. Missing them is how
        a paper with public code was reported as having none."""
        found = parse_jats_supplements(_JATS)
        assert {"Supplemental File S1", "Supplemental File S2", "Supplemental File S3"} <= {s["label"] for s in found}

    def test_a_named_reference_is_marked_unresolved(self):
        found = parse_jats_supplements(_JATS)
        s2 = next(s for s in found if s["label"] == "Supplemental File S2")
        assert s2["source"] == NAMED_IN_TEXT
        assert s2["filename"] is None
        assert s2["role"] == UNKNOWN_ROLE

    def test_an_attached_media_element_carries_its_filename(self):
        found = parse_jats_supplements(_JATS)
        attached = [s for s in found if s["filename"]]
        assert any(s["filename"] == "supp_29_10_1705__index.html" for s in attached)

    def test_the_same_supplement_is_listed_once_however_often_it_is_cited(self):
        found = parse_jats_supplements(_JATS)
        labels = [s["label"] for s in found]
        assert len(labels) == len(set(labels))

    def test_a_paper_with_no_supplements_yields_an_empty_inventory(self):
        assert parse_jats_supplements("<article><body><p>No attachments here.</p></body></article>") == []

    def test_unparseable_xml_is_empty_not_an_exception(self):
        """A malformed document is a discovery limitation. It must not fail the read."""
        assert parse_jats_supplements("<article><body>") == []


class TestReadingTheAuthorsDocx:
    def test_r_markdown_survives_extraction(self):
        """Supplemental File S2 is a DOCX carrying the paper's whole analysis. It was fetched and
        stored as an opaque blob, so the code inside it was never read."""
        text = extract_docx_text(_S2)
        assert "DESeq" in text
        assert "library(" in text
        assert text.count("<-") > 100

    def test_extraction_preserves_line_structure(self):
        text = extract_docx_text(_S2)
        assert text.count("\n") > 1000

    def test_a_blob_that_is_not_a_docx_yields_nothing(self):
        assert extract_docx_text(b"not a zip at all") is None


class TestClassifyingBySInspectedContent:
    def test_a_sample_metadata_table_is_recognised_by_its_columns(self):
        assert classify_supplement("supplemental_file_1_embryo_metadata.txt", _S1) == SAMPLE_METADATA

    def test_a_differential_results_table_is_recognised_by_its_statistics(self):
        """log2FoldChange, pvalue and padj are a results table. This is the one that must NEVER be
        taken for the expression matrix needed to rerun the analysis."""
        assert classify_supplement("supplemental_file_3_siggenes.txt", _S3) == RESULTS_TABLE

    def test_a_docx_of_r_code_is_code(self):
        assert classify_supplement("supplemental_file_2_allrcode.docx", _S2) == CODE

    def test_an_image_is_not_mistaken_for_data(self):
        assert classify_supplement("1705f01.jpg", b"\xff\xd8\xff\xe0JFIF") == UNKNOWN_ROLE

    def test_content_decides_over_the_filename(self):
        """A file called 'metadata' holding DESeq2 output is a results table. The name is the
        author's habit; the header is the evidence."""
        assert classify_supplement("embryo_metadata.txt", _S3) == RESULTS_TABLE


class TestARisTableIsNeverAnExpressionMatrix:
    def test_the_results_table_is_not_offered_as_a_reproduction_input(self):
        """194 rows of DESeq2 output cannot rerun DESeq2. Accepting it as the matrix would produce
        a 'reproduction' that reads its own answer back."""
        from app.services.supplement_inventory import EXPRESSION_MATRIX

        assert classify_supplement("supplemental_file_3_siggenes.txt", _S3) != EXPRESSION_MATRIX


class TestItNeverRaises:
    @pytest.mark.parametrize("blob", [b"", b"\x00\x01\x02", "not bytes".encode("utf-16")])
    def test_any_blob_classifies_without_raising(self, blob):
        assert classify_supplement("thing.txt", blob) in {
            SAMPLE_METADATA,
            RESULTS_TABLE,
            CODE,
            UNKNOWN_ROLE,
            "expression_matrix",
            "supporting_input",
        }
