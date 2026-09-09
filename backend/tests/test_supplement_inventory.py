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


def _bundle() -> bytes:
    """The Europe PMC supplementary bundle's real shape: one zip, publisher-mangled filenames."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("supp_gr.252981.119_Supplemental_File_1_embryo_metadata.txt", _S1)
        zf.writestr("supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx", _S2)
        zf.writestr("supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt", _S3)
        zf.writestr("1705f01.jpg", b"\xff\xd8\xff\xe0JFIF")
    return buffer.getvalue()


class TestResolvingAReferenceToAFile:
    @pytest.mark.asyncio
    async def test_the_prose_identifier_finds_the_publisher_mangled_filename(self):
        """'Supplemental File S2' and 'supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx'
        are the same artifact. Nothing connected them, so the code was never found."""
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(_url):
            return _bundle()

        resolved = await resolve_supplements("PMC6771404", parse_jats_supplements(_JATS), fetcher=_fetch)
        s2 = next(s for s in resolved if s["label"] == "Supplemental File S2")
        assert s2["filename"].endswith("AllRCode_Review.docx")
        assert s2["resolved"] is True

    @pytest.mark.asyncio
    async def test_each_file_gets_the_role_its_content_shows(self):
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(_url):
            return _bundle()

        resolved = await resolve_supplements("PMC6771404", parse_jats_supplements(_JATS), fetcher=_fetch)
        by_label = {s["label"]: s["role"] for s in resolved}
        assert by_label["Supplemental File S1"] == SAMPLE_METADATA
        assert by_label["Supplemental File S2"] == CODE
        assert by_label["Supplemental File S3"] == RESULTS_TABLE

    @pytest.mark.asyncio
    async def test_a_download_failure_leaves_references_unresolved_not_absent(self):
        """'We could not fetch it' is not 'the authors did not publish it'."""
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(_url):
            raise RuntimeError("504 gateway timeout")

        resolved = await resolve_supplements("PMC6771404", parse_jats_supplements(_JATS), fetcher=_fetch)
        assert all(s["resolved"] is False for s in resolved)
        assert any(s.get("failure_reason") for s in resolved)

    @pytest.mark.asyncio
    async def test_a_file_nobody_referenced_is_still_inventoried(self):
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(_url):
            return _bundle()

        resolved = await resolve_supplements("PMC6771404", [], fetcher=_fetch)
        assert {s["filename"] for s in resolved} >= {"1705f01.jpg"}


class TestWhatResolutionMeasures:
    @pytest.mark.asyncio
    async def test_a_results_table_reports_its_rows_and_the_claimed_threshold(self):
        """194 rows, 88 above the cutoff THE PAPER CLAIMS. The cutoff is an input now: a fixed pair
        would measure Groff's numbers on every paper and the claimed one on none."""
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(_url):
            return _bundle()

        resolved = await resolve_supplements(
            "PMC6771404", parse_jats_supplements(_JATS), fetcher=_fetch, thresholds=[2.0]
        )
        s3 = next(s for s in resolved if s["label"] == "Supplemental File S3")
        assert s3["row_count"] == 194
        assert s3["threshold_splits"]["abs_log2fc>2"] == 88
        assert "log2FoldChange" in s3["columns"]

    @pytest.mark.asyncio
    async def test_the_sex_linked_count_is_available_from_the_chromosome_column(self):
        """Groff's 146 sex-linked genes. No fold-change threshold produces that number; only
        reading the chromosome column can."""
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(_url):
            return _bundle()

        resolved = await resolve_supplements("PMC6771404", parse_jats_supplements(_JATS), fetcher=_fetch)
        s3 = next(s for s in resolved if s["label"] == "Supplemental File S3")
        chromosomes = s3["category_counts"]["chr"]
        assert chromosomes.get("chrX", 0) + chromosomes.get("chrY", 0) == 146

    @pytest.mark.asyncio
    async def test_a_metadata_table_reports_its_rows_and_columns(self):
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(_url):
            return _bundle()

        resolved = await resolve_supplements("PMC6771404", parse_jats_supplements(_JATS), fetcher=_fetch)
        s1 = next(s for s in resolved if s["label"] == "Supplemental File S1")
        assert s1["row_count"] == 54
        assert "Sampletype" in s1["columns"]

    @pytest.mark.asyncio
    async def test_the_rows_themselves_are_never_kept(self):
        """The digest goes into a prompt. Row counts are evidence; 194 rows of gene IDs are cost."""
        from app.services.supplement_inventory import resolve_supplements

        async def _fetch(_url):
            return _bundle()

        resolved = await resolve_supplements("PMC6771404", parse_jats_supplements(_JATS), fetcher=_fetch)
        assert all("rows" not in s for s in resolved)
        assert all("ENSG" not in str(s) for s in resolved)
