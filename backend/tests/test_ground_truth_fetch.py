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
    assert geo_suppl_dir_url("GSE309060") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE309nnn/GSE309060/suppl/"
    )
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
