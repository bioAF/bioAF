"""plan_7 step 1: list what a GEO study actually deposited.

The pipeline route reads a paper by re-running it from raw reads. The deposit route starts from the
pre-processed data the authors published, so the first thing it needs is an honest inventory of that
deposit: every supplementary file, at the series level AND per sample, with what it is.

The filenames and manifests asserted here are REAL, taken 2026-09-05 from the four studies this
project has already run (GSE273743, GSE274331, GSE157174, GSE213770). They are the reason the
classifier looks the way it does: csaw and narrowPeak and .xlsx are what GEO actually holds, not
what a token list invented in advance would have guessed.

The HTTP boundary is injected, as in ``test_ground_truth_fetch`` and ``test_accession_manifest``, so
these are deterministic and never touch the network.
"""

import pytest

from app.services.literature.deposit_inventory_service import (
    DepositInventory,
    classify_deposit_filename,
    filelist_url,
    list_deposit,
    parse_filelist,
    sample_suppl_url,
    series_suppl_url,
)


# ---- URL construction ----


def test_series_suppl_url_masks_last_three_digits():
    assert series_suppl_url("GSE309060") == "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE309nnn/GSE309060/suppl/"
    assert series_suppl_url("GSE52778") == "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE52nnn/GSE52778/suppl/"
    assert series_suppl_url("GSE12") == "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE12/suppl/"


def test_sample_suppl_url_masks_the_same_way():
    """Verified live 2026-09-05: this exact URL returns the GSE157174 per-sample narrowPeak."""
    assert sample_suppl_url("GSM4758351") == "https://ftp.ncbi.nlm.nih.gov/geo/samples/GSM4758nnn/GSM4758351/suppl/"
    assert sample_suppl_url("GSM1234") == "https://ftp.ncbi.nlm.nih.gov/geo/samples/GSM1nnn/GSM1234/suppl/"
    assert sample_suppl_url("GSM123") == "https://ftp.ncbi.nlm.nih.gov/geo/samples/GSMnnn/GSM123/suppl/"


def test_url_builders_reject_the_wrong_accession_type():
    assert series_suppl_url("GSM4758351") is None
    assert sample_suppl_url("GSE157174") is None
    assert series_suppl_url("SRP123456") is None
    assert series_suppl_url("") is None
    assert filelist_url("PRJNA1") is None


# ---- classification, against real deposited filenames ----


@pytest.mark.parametrize(
    "filename,expected",
    [
        # GSE273743. csaw's own output naming. The project already knows this file IS the paper's
        # differential-binding table (study 26 parsed 92 intervals from it), and the pre-plan_7
        # classifier called it "other" because neither "csaw" nor "dba" was an enumerated token.
        ("GSE273743_aTC_Klf4_KD_NKX2.2_ChIPseq_csaw.dba_window.set.csv.gz", "da_table"),
        # GSE274331. A TPM table, in Excel, which is the shape the bioinformaticians predicted.
        ("GSE274331_TPMs_H2AS40-KD.xlsx", "matrix_normalized"),
        ("GSE274331_RAW.tar", "raw"),
        ("filelist.txt", "raw"),
        # GSE157174. Per-sample ATAC peak calls: the pre-processed data for study 13's deposit,
        # which cost ~10 hours on the pipeline route and produced nothing.
        (
            "GSM4758351_L2_bio1_act_TKD180802547_HT3VLCCXY_L4_1.trim.merged.nodup.tn5.pval0.01.300K.bfilt.narrowPeak.gz",
            "peaks",
        ),
        # GSE213770. Differentially methylated regions: a differential result with coordinates.
        ("GSE213770_DMR_DMB_TET2Neu.xls.gz", "da_table"),
        # Coverage tracks are deposited pre-processed data but carry no per-feature values, so they
        # are never a reproduction input.
        ("GSM8447562_H2AS40Gc_MM231.bigwig", "coverage"),
        # The 10x triplet.
        ("GSM123_barcodes.tsv.gz", "barcodes"),
        ("GSM123_features.tsv.gz", "features"),
        ("GSM123_genes.tsv.gz", "features"),
        ("GSM123_matrix.mtx.gz", "matrix_counts"),
        # Count and normalized matrices.
        ("GSE1_raw_counts.tsv", "matrix_counts"),
        ("GSE1_gene_count_matrix.csv", "matrix_counts"),
        ("GSE1_UMI_counts.tsv.gz", "matrix_counts"),
        ("GSE1_FPKM_table.txt", "matrix_normalized"),
        ("GSE1_cpm_normalized.tsv", "matrix_normalized"),
        ("GSE1_vst_values.tsv", "matrix_normalized"),
        # Result tables.
        ("GSE309060_DEG_results.csv", "de_table"),
        ("GSE1_deseq2.annot.xls.gz", "de_table"),
        ("GSE1_diffbind_peaks.txt", "da_table"),
        ("GSE1_limma_results.tsv", "de_table"),
        # Sample metadata.
        ("GSE1_sample_metadata.tsv", "metadata"),
        ("GSE1_coldata.csv", "metadata"),
        ("GSE1_phenotype.txt", "metadata"),
        ("GSE1_readme.txt", "other"),
    ],
)
def test_classify_deposit_filename(filename, expected):
    assert classify_deposit_filename(filename) == expected


def test_a_result_table_beats_a_metadata_token():
    """`deseq2.annot.xls.gz` carries "annot", but it is a DE table. Result tables are checked first,
    so the metadata bucket cannot steal a file that carries statistics."""
    assert classify_deposit_filename("GSE1_deseq2.annot.xls.gz") == "de_table"


def test_the_depositors_type_beats_the_filename():
    """`filelist.txt` states a controlled Type per file. GEO's depositor said what it is, and that is
    better evidence than our token list, exactly as `library_strategy` beats the paper's prose."""
    assert classify_deposit_filename("GSE1_something_opaque.gz", deposited_type="NARROWPEAK") == "peaks"
    assert classify_deposit_filename("GSE1_something_opaque.gz", deposited_type="BIGWIG") == "coverage"
    assert classify_deposit_filename("GSE1_counts_like_name.gz", deposited_type="BIGWIG") == "coverage"
    assert classify_deposit_filename("GSE1_opaque.gz", deposited_type="MTX") == "matrix_counts"


def test_an_unknown_deposited_type_falls_back_to_the_filename():
    assert classify_deposit_filename("GSE1_raw_counts.tsv", deposited_type="SOMETHINGNEW") == "matrix_counts"


# ---- filelist.txt parsing ----

# Real GSE157174 filelist.txt, first rows, verbatim (tabs preserved).
_FILELIST = (
    "#Archive/File\tName\tTime\tSize\tType\n"
    "Archive\tGSE157174_RAW.tar\t02/01/2024 11:26:49\t50186240\tTAR\n"
    "File\tGSM4758351_L2_bio1_act.narrowPeak.gz\t08/30/2020 03:30:50\t4272247\tNARROWPEAK\n"
    "File\tGSM4758357_L2_bio1_con.narrowPeak.gz\t08/30/2020 03:29:40\t4589392\tNARROWPEAK\n"
)


def test_parse_filelist_returns_the_member_files_with_size_and_type():
    rows = parse_filelist(_FILELIST)
    assert [r["filename"] for r in rows] == [
        "GSM4758351_L2_bio1_act.narrowPeak.gz",
        "GSM4758357_L2_bio1_con.narrowPeak.gz",
    ]
    assert rows[0]["size_bytes"] == 4272247
    assert rows[0]["deposited_type"] == "NARROWPEAK"


def test_parse_filelist_skips_the_archive_row():
    """The Archive row is the _RAW.tar itself, not a file to reproduce from. Listing it as a
    candidate would offer a 50 MB tarball as a count matrix."""
    assert all(r["filename"] != "GSE157174_RAW.tar" for r in parse_filelist(_FILELIST))


def test_parse_filelist_attributes_each_file_to_its_gsm():
    rows = parse_filelist(_FILELIST)
    assert rows[0]["gsm"] == "GSM4758351"
    assert rows[1]["gsm"] == "GSM4758357"


def test_parse_filelist_tolerates_junk():
    assert parse_filelist("") == []
    assert parse_filelist("not a manifest at all") == []


# ---- orchestration (injected fetcher) ----


def _fetcher(pages: dict):
    """An async fetcher over a {url: body} map; anything unmapped raises, as a 404 would."""

    async def fetch(url: str) -> str:
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]

    return fetch


@pytest.mark.asyncio
async def test_list_deposit_prefers_the_filelist_over_per_sample_fetches():
    """GSE157174 has 12 samples. The filelist names every per-sample file in ONE request, so the
    inventory must not issue 12 more."""
    calls: list[str] = []

    async def counting(url: str) -> str:
        calls.append(url)
        if url == filelist_url("GSE157174"):
            return _FILELIST
        raise RuntimeError("404")

    inv = await list_deposit("GSE157174", fetcher=counting)
    assert {e.filename for e in inv.entries} == {
        "GSM4758351_L2_bio1_act.narrowPeak.gz",
        "GSM4758357_L2_bio1_con.narrowPeak.gz",
    }
    assert all(e.classification == "peaks" for e in inv.entries)
    assert all(e.level == "sample" for e in inv.entries)
    assert len([c for c in calls if "/samples/" in c]) == 0


@pytest.mark.asyncio
async def test_list_deposit_falls_back_to_the_html_listing_when_there_is_no_filelist():
    """GSE273743 and GSE213770 have no filelist.txt: no _RAW.tar, so GEO writes none. Verified
    2026-09-05."""
    pages = {
        series_suppl_url("GSE273743"): (
            '<a href="../">Parent</a><a href="GSE273743_aTC_Klf4_KD_NKX2.2_ChIPseq_csaw.dba_window.set.csv.gz">x</a>'
        )
    }
    inv = await list_deposit("GSE273743", fetcher=_fetcher(pages))
    assert len(inv.entries) == 1
    assert inv.entries[0].classification == "da_table"
    assert inv.entries[0].level == "series"
    assert inv.entries[0].url.endswith("GSE273743_aTC_Klf4_KD_NKX2.2_ChIPseq_csaw.dba_window.set.csv.gz")


@pytest.mark.asyncio
async def test_list_deposit_reports_an_unreachable_geo_instead_of_raising():
    inv = await list_deposit("GSE999999", fetcher=_fetcher({}))
    assert isinstance(inv, DepositInventory)
    assert inv.entries == []
    assert inv.unavailable_reason
    assert "GSE999999" in inv.unavailable_reason


@pytest.mark.asyncio
async def test_list_deposit_rejects_a_non_geo_accession():
    inv = await list_deposit("SRP123456", fetcher=_fetcher({}))
    assert inv.entries == []
    assert inv.unavailable_reason


@pytest.mark.asyncio
async def test_list_deposit_groups_a_10x_triplet_per_sample():
    """barcodes + features + matrix under one GSM is ONE reproducible input, not three files. The
    grouping is deterministic, so the model is never asked to do it."""
    pages = {
        filelist_url("GSE1"): (
            "#Archive/File\tName\tTime\tSize\tType\n"
            "File\tGSM1_barcodes.tsv.gz\t1\t10\tTSV\n"
            "File\tGSM1_features.tsv.gz\t1\t20\tTSV\n"
            "File\tGSM1_matrix.mtx.gz\t1\t30\tMTX\n"
            "File\tGSM2_barcodes.tsv.gz\t1\t10\tTSV\n"
            "File\tGSM2_features.tsv.gz\t1\t20\tTSV\n"
            "File\tGSM2_matrix.mtx.gz\t1\t30\tMTX\n"
        )
    }
    inv = await list_deposit("GSE1", fetcher=_fetcher(pages))
    assert len(inv.triplets) == 2
    t = next(t for t in inv.triplets if t["gsm"] == "GSM1")
    assert t["barcodes"].endswith("GSM1_barcodes.tsv.gz")
    assert t["features"].endswith("GSM1_features.tsv.gz")
    assert t["matrix"].endswith("GSM1_matrix.mtx.gz")


@pytest.mark.asyncio
async def test_an_incomplete_triplet_is_not_grouped():
    """Two of three parts cannot be read as a matrix. It stays as loose entries, so the gate shows
    what is actually there rather than a triplet that would fail at read time."""
    pages = {
        filelist_url("GSE1"): (
            "#Archive/File\tName\tTime\tSize\tType\n"
            "File\tGSM1_barcodes.tsv.gz\t1\t10\tTSV\n"
            "File\tGSM1_matrix.mtx.gz\t1\t30\tMTX\n"
        )
    }
    inv = await list_deposit("GSE1", fetcher=_fetcher(pages))
    assert inv.triplets == []
    assert len(inv.entries) == 2


@pytest.mark.asyncio
async def test_list_deposit_carries_size_from_the_filelist():
    """Size is what step 5's download cap is enforced against, so it has to survive the inventory."""
    inv = await list_deposit("GSE157174", fetcher=_fetcher({filelist_url("GSE157174"): _FILELIST}))
    assert next(e for e in inv.entries if "bio1_act" in e.filename).size_bytes == 4272247


@pytest.mark.asyncio
async def test_html_fallback_entries_have_no_size():
    """A directory listing states no sizes. None is honest; 0 would read as an empty file."""
    pages = {series_suppl_url("GSE2"): '<a href="GSE2_counts.tsv">x</a>'}
    inv = await list_deposit("GSE2", fetcher=_fetcher(pages))
    assert inv.entries[0].size_bytes is None


# ---- defects found by running step 1 against the real deposits (2026-09-05) ----


@pytest.mark.asyncio
async def test_the_filelist_does_not_hide_series_level_files():
    """`filelist.txt` describes the members of `_RAW.tar` ONLY. GSE274331 deposits its per-sample
    bigwigs inside the tar AND a series-level `GSE274331_TPMs_H2AS40-KD.xlsx` beside it, and the TPM
    table is the one file in that deposit worth reproducing from.

    Returning on the filelist alone dropped it. Both listings are read and merged; that is two
    requests for the whole deposit, still far short of one per sample.
    """
    pages = {
        filelist_url("GSE274331"): (
            "#Archive/File\tName\tTime\tSize\tType\n"
            "Archive\tGSE274331_RAW.tar\t1\t835317760\tTAR\n"
            "File\tGSM8447562_H2AS40Gc_MM231.bigwig\t1\t176283816\tBIGWIG\n"
        ),
        series_suppl_url("GSE274331"): (
            '<a href="../">Parent</a>'
            '<a href="GSE274331_RAW.tar">x</a>'
            '<a href="GSE274331_TPMs_H2AS40-KD.xlsx">x</a>'
            '<a href="filelist.txt">x</a>'
        ),
    }
    inv = await list_deposit("GSE274331", fetcher=_fetcher(pages))
    names = {e.filename for e in inv.entries}
    assert "GSE274331_TPMs_H2AS40-KD.xlsx" in names
    assert "GSM8447562_H2AS40Gc_MM231.bigwig" in names
    tpm = next(e for e in inv.entries if e.filename.endswith(".xlsx"))
    assert tpm.classification == "matrix_normalized"
    assert tpm.level == "series"


@pytest.mark.asyncio
async def test_a_merged_listing_keeps_the_filelist_size_and_type():
    """The directory listing states no size or type. When a file appears in both, the filelist's
    richer row must win, or step 5's download cap loses the number it enforces against."""
    pages = {
        filelist_url("GSE3"): ("#Archive/File\tName\tTime\tSize\tType\nFile\tGSM9_opaque.gz\t1\t4272247\tNARROWPEAK\n"),
        series_suppl_url("GSE3"): '<a href="GSM9_opaque.gz">x</a>',
    }
    inv = await list_deposit("GSE3", fetcher=_fetcher(pages))
    assert len(inv.entries) == 1
    assert inv.entries[0].size_bytes == 4272247
    assert inv.entries[0].classification == "peaks"


@pytest.mark.asyncio
async def test_the_raw_tar_and_the_manifest_are_not_offered_as_candidates():
    """Both appear in the directory listing. Neither is something to reproduce from."""
    pages = {
        series_suppl_url("GSE4"): (
            '<a href="GSE4_RAW.tar">x</a><a href="filelist.txt">x</a><a href="GSE4_counts.tsv">x</a>'
        )
    }
    inv = await list_deposit("GSE4", fetcher=_fetcher(pages))
    assert {e.classification for e in inv.entries if e.classification != "raw"} == {"matrix_counts"}
    assert {e.filename for e in inv.entries if e.classification == "raw"} == {"GSE4_RAW.tar", "filelist.txt"}


def test_a_differential_methylation_table_is_a_result_table():
    """GSE213770 deposits `GSE213770_DMR_DMB_TET2Neu.xls.gz`: differentially methylated regions,
    which is a differential result with coordinates. It classified as "other" because DMR/DMP were
    not tokens, the same omission csaw was."""
    assert classify_deposit_filename("GSE213770_DMR_DMB_TET2Neu.xls.gz") == "da_table"
    assert classify_deposit_filename("GSE1_dmp_results.csv") == "da_table"
    assert classify_deposit_filename("GSE1_summary.csv") == "other"
