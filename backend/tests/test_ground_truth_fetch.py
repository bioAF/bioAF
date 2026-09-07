"""B4 auto-fetch ASSIST (lit_validation Level-3, ADR-069 / spec-08).

Best-effort acquisition of a paper's deposited result set from GEO supplementary files, to pre-fill
the human confirm at C1. Never unattended ground truth (spike-03: DE tables are in GEO suppl only
~3.8% of the time and DA ~never, so this is assist; journal-SI acquisition is gated + publisher-
specific and stays a human-supply path). The HTTP boundary is injected so these are deterministic.
"""

import pytest

from app.services.literature.ground_truth_fetch_service import (
    GroundTruthFetchService,
    classify_supplementary_filename,
    geo_suppl_dir_url,
    parse_dir_listing,
)


# ---- pure helpers ----


def test_geo_suppl_dir_url_builds_ftp_path():
    assert geo_suppl_dir_url("GSE309060") == ("https://ftp.ncbi.nlm.nih.gov/geo/series/GSE309nnn/GSE309060/suppl/")
    assert geo_suppl_dir_url("GSE52778") == "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE52nnn/GSE52778/suppl/"
    assert geo_suppl_dir_url("GSE12") == "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE12/suppl/"


def test_geo_suppl_dir_url_rejects_non_gse():
    assert geo_suppl_dir_url("SRP123456") is None
    assert geo_suppl_dir_url("PRJNA1") is None
    assert geo_suppl_dir_url("") is None


def test_classify_supplementary_filename():
    assert classify_supplementary_filename("GSE309060_DEG_results.csv") == "de_table"
    assert classify_supplementary_filename("GSE1_deseq2.annot.xls.gz") == "de_table"
    assert classify_supplementary_filename("GSE1_diffbind_peaks.txt") == "da_table"
    assert classify_supplementary_filename("GSE1_raw_counts.tsv") == "counts"
    assert classify_supplementary_filename("GSE1_RAW.tar") == "raw"
    assert classify_supplementary_filename("GSE1_readme.txt") == "other"


def test_parse_dir_listing_extracts_filenames():
    html = (
        '<html><body><a href="../">Parent</a>'
        '<a href="GSE1_DEG.csv">GSE1_DEG.csv</a>'
        '<a href="GSE1_RAW.tar">GSE1_RAW.tar</a></body></html>'
    )
    names = parse_dir_listing(html)
    assert "GSE1_DEG.csv" in names
    assert "GSE1_RAW.tar" in names
    assert "../" not in names  # parent link excluded


# ---- orchestration (injected fetcher) ----

_DIR_HTML = (
    '<a href="GSE1_DEG_results.csv">GSE1_DEG_results.csv</a>'
    '<a href="GSE1_raw_counts.tsv">GSE1_raw_counts.tsv</a>'
    '<a href="GSE1_RAW.tar">GSE1_RAW.tar</a>'
)
_DE_CSV = "gene,log2FoldChange,padj\nA1BG,2.5,0.001\nTP53,-1.8,0.01\nGAPDH,0.1,0.9\n"


def _fake_fetcher(pages: dict):
    async def fetch(url: str) -> str:
        if url in pages:
            return pages[url]
        raise RuntimeError(f"404 {url}")

    return fetch


@pytest.mark.asyncio
async def test_fetch_geo_candidates_returns_parsed_de_table():
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    pages = {base: _DIR_HTML, base + "GSE1_DEG_results.csv": _DE_CSV}
    cands = await GroundTruthFetchService.fetch_geo_candidates("GSE1", kind="gene", fetcher=_fake_fetcher(pages))

    assert len(cands) == 1
    c = cands[0]
    assert c["source"] == "geo_supplementary"
    assert c["filename"] == "GSE1_DEG_results.csv"
    assert c["finding_set"]["n_sig"] == 2  # A1BG up, TP53 down (GAPDH excluded)
    assert c["table_text"] == _DE_CSV  # raw table carried through for the human to review/confirm
    # the counts + RAW files are NOT offered as DE candidates
    assert all("counts" not in x["filename"].lower() for x in cands)


@pytest.mark.asyncio
async def test_fetch_geo_candidates_empty_on_no_de_table():
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
    pages = {base: '<a href="GSE1_RAW.tar">GSE1_RAW.tar</a>'}
    cands = await GroundTruthFetchService.fetch_geo_candidates("GSE1", kind="gene", fetcher=_fake_fetcher(pages))
    assert cands == []


@pytest.mark.asyncio
async def test_fetch_geo_candidates_swallows_fetch_errors():
    # Acquisition is assist, never a gate: a listing fetch failure yields [], not an exception.
    async def _boom(_url):
        raise RuntimeError("network down")

    cands = await GroundTruthFetchService.fetch_geo_candidates("GSE1", kind="gene", fetcher=_boom)
    assert cands == []


@pytest.mark.asyncio
async def test_fetch_geo_candidates_non_gse_is_empty():
    cands = await GroundTruthFetchService.fetch_geo_candidates("SRP999", kind="gene", fetcher=_fake_fetcher({}))
    assert cands == []


# ---- plan_7: one classifier, so a deposit recognised by the deposit route is recognised here ----


def test_csaw_and_dmr_tables_are_recognised_as_result_tables():
    """These are the two families this classifier used to miss, both found by running plan_7 step 1
    against real deposits.

    GSE273743's `..._csaw.dba_window.set.csv.gz` IS the differential-binding table study 26 scored
    its verdict against, and GSE213770's `_DMR_` file is differentially methylated regions. Both
    classified as "other", so `fetch_geo_candidates` never offered either, and the C1 gate asked a
    human to supply ground truth we could already reach.
    """
    assert (
        classify_supplementary_filename("GSE273743_aTC_Klf4_KD_NKX2.2_ChIPseq_csaw.dba_window.set.csv.gz") == "da_table"
    )
    assert classify_supplementary_filename("GSE213770_DMR_DMB_TET2Neu.xls.gz") == "da_table"


def test_the_assist_vocabulary_is_unchanged():
    """This function answers in FIVE buckets and `fetch_geo_candidates` filters on them. The deposit
    route's classifier is finer (it separates counts from TPM, barcodes from features), and that
    finer answer is mapped back here rather than leaked, so this caller's contract is untouched."""
    assert classify_supplementary_filename("GSE1_raw_counts.tsv") == "counts"
    assert classify_supplementary_filename("GSE1_TPMs.xlsx") == "counts"
    assert classify_supplementary_filename("GSM1_barcodes.tsv.gz") == "other"
    assert classify_supplementary_filename("GSM1_sample_metadata.tsv") == "other"
    assert classify_supplementary_filename("GSM1_x.bigwig") == "other"
    assert classify_supplementary_filename("GSM1_x.narrowPeak.gz") == "other"


@pytest.mark.asyncio
async def test_fetch_geo_candidates_now_offers_a_csaw_table():
    """End to end on a GSE273743-shaped listing: the paper's own deposited ground truth is offered
    at the C1 gate instead of being invisible.

    It parses to zero entities here, and that is correct rather than a shortfall: csaw names its
    coordinates `regions.seqnames`, which no alias list enumerates. Locating the file and reading it
    are two different jobs, and the second one already has its own seam (`column_resolution`, added
    in a55fe889). This test proves the first.
    """
    base = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE273nnn/GSE273743/suppl/"
    name = "GSE273743_aTC_Klf4_KD_NKX2.2_ChIPseq_csaw.dba_window.set.csv"
    table = "regions.seqnames,regions.start,regions.end,best.logFC,FDR\nchr1,100,200,2.5,0.001\n"
    pages = {base: f'<a href="{name}">{name}</a>', base + name: table}

    cands = await GroundTruthFetchService.fetch_geo_candidates(
        "GSE273743", kind="interval", fetcher=_fake_fetcher(pages)
    )
    assert [c["filename"] for c in cands] == [name]
    assert cands[0]["table_text"] == table


def test_the_absolute_parent_link_is_not_read_as_a_filename():
    """NCBI writes the parent link as an ABSOLUTE path, not as `../`, so the series accession itself
    came back as a supplementary filename.

    Measured on the real GSE273743 listing: the parser returned ['GSE273743', '..._csaw.csv.gz'].
    Latent rather than live, because the phantom classifies as `other` and is filtered before any
    fetch, but it is one classifier token away from sending a download at a directory URL.
    """
    html = (
        '<a href="/geo/series/GSE273nnn/GSE273743/">Parent Directory</a>'
        '<a href="GSE273743_csaw.dba_window.set.csv.gz">x</a>'
    )
    assert parse_dir_listing(html) == ["GSE273743_csaw.dba_window.set.csv.gz"]


def test_one_listing_parser_serves_both_routes():
    """The duplicate is what let the two drift: they were identical except for this exclusion. Same
    consolidation `classify_supplementary_filename` got in a0d90761."""
    from app.services.literature import deposit_inventory_service as deposit

    assert parse_dir_listing is deposit.parse_dir_listing
