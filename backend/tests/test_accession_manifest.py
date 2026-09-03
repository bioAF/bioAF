"""Sample-manifest fetch for the lit_validation Level-3 gate (sample selection by recognition).

At the C1 gate a scientist confirms which of a study's samples are the test vs the reference arm by
RECOGNIZING them (title + condition), never by typing accession tokens. This service resolves a
deposited accession (GEO series or ENA/SRA study/experiment/run) into that per-sample manifest.

Mirrors ground_truth_fetch_service: the HTTP boundary is injected so these are deterministic, and any
fetch failure yields an empty manifest + a human reason (never raises), so the gate can degrade to
free-text entry. See local/ui_rework_v2/plan-sample-selection-and-study-naming.md (block 1).
"""

import pytest

from app.services.literature.accession_manifest_service import (
    AccessionManifestService,
    dominant_library_strategy,
    geo_series_matrix_url,
    parse_ena_filereport,
    parse_series_matrix,
)


def _fake_fetcher(pages: dict):
    async def fetch(url: str) -> str:
        if url in pages:
            return pages[url]
        raise RuntimeError(f"404 {url}")

    return fetch


# ---- pure helpers ----


def test_geo_series_matrix_url_builds_ftp_path():
    assert geo_series_matrix_url("GSE52778") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE52nnn/GSE52778/matrix/GSE52778_series_matrix.txt.gz"
    )
    assert geo_series_matrix_url("GSE12") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE12/matrix/GSE12_series_matrix.txt.gz"
    )


def test_geo_series_matrix_url_rejects_non_gse():
    assert geo_series_matrix_url("SRP123456") is None
    assert geo_series_matrix_url("") is None


def test_parse_ena_filereport_maps_rows():
    tsv = (
        "run_accession\texperiment_accession\tsample_accession\tsample_title\n"
        "SRR1\tSRX1\tSRS1\tTumor 1\n"
        "SRR2\tSRX2\tSRS2\tNormal 1\n"
    )
    rows = parse_ena_filereport(tsv)
    assert rows == [
        {
            "run_accession": "SRR1",
            "experiment_accession": "SRX1",
            "sample_accession": "SRS1",
            "sample_title": "Tumor 1",
        },
        {
            "run_accession": "SRR2",
            "experiment_accession": "SRX2",
            "sample_accession": "SRS2",
            "sample_title": "Normal 1",
        },
    ]


def test_parse_series_matrix_reads_titles_conditions_relations():
    samples, series_sra = parse_series_matrix(_GEO_MATRIX)
    assert series_sra == "SRP071965"  # the SRA study relation, preferred over BioProject
    assert [s["title"] for s in samples] == ["Tumor rep 1", "Tumor rep 2", "Normal rep 1"]
    # both characteristics lines joined
    assert samples[0]["condition"] == "tissue: tumor; genotype: WT"
    assert samples[2]["condition"] == "tissue: normal; genotype: WT"
    assert samples[0]["experiment_accession"] == "SRX1596112"
    assert samples[0]["sample_accession"] == "SAMN04502591"


# ---- fetch_manifest orchestration (injected fetcher) ----

_ENA_TSV = (
    "run_accession\texperiment_accession\tsample_accession\tsample_title\texperiment_title\tlibrary_strategy\n"
    "SRR3192657\tSRX1596112\tSRS1329485\tTumor replicate 1\tGSM2055667\tRNA-Seq\n"
    "SRR3192658\tSRX1596113\tSRS1329486\tTumor replicate 2\tGSM2055668\tRNA-Seq\n"
    "SRR3192659\tSRX1596114\tSRS1329487\tNormal replicate 1\tGSM2055669\tRNA-Seq\n"
    # a second run of the SAME experiment (technical replicate) must collapse to one entry:
    "SRR9999999\tSRX1596112\tSRS1329485\tTumor replicate 1\tGSM2055667\tRNA-Seq\n"
)

_GEO_MATRIX = (
    '!Series_title\t"Great tumor study"\n'
    '!Series_relation\t"BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA315362"\n'
    '!Series_relation\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP071965"\n'
    '!Sample_title\t"Tumor rep 1"\t"Tumor rep 2"\t"Normal rep 1"\n'
    '!Sample_geo_accession\t"GSM2055667"\t"GSM2055668"\t"GSM2055669"\n'
    '!Sample_characteristics_ch1\t"tissue: tumor"\t"tissue: tumor"\t"tissue: normal"\n'
    '!Sample_characteristics_ch1\t"genotype: WT"\t"genotype: WT"\t"genotype: WT"\n'
    '!Sample_relation\t"BioSample: https://www.ncbi.nlm.nih.gov/biosample/SAMN04502591"'
    '\t"BioSample: https://www.ncbi.nlm.nih.gov/biosample/SAMN04502592"'
    '\t"BioSample: https://www.ncbi.nlm.nih.gov/biosample/SAMN04502593"\n'
    '!Sample_relation\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX1596112"'
    '\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX1596113"'
    '\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX1596114"\n'
    "!series_matrix_table_begin\n"
    '"ID_REF"\t"GSM2055667"\t"GSM2055668"\t"GSM2055669"\n'
    "!series_matrix_table_end\n"
)

_GEO_ENA_TSV = (
    "run_accession\texperiment_accession\tsample_accession\tsample_title\texperiment_title\tlibrary_strategy\n"
    "SRR3192657\tSRX1596112\tSRS1329485\tTumor rep 1\t\tRNA-Seq\n"
    "SRR3192658\tSRX1596113\tSRS1329486\tTumor rep 2\t\tRNA-Seq\n"
    "SRR3192659\tSRX1596114\tSRS1329487\tNormal rep 1\t\tRNA-Seq\n"
)


@pytest.mark.asyncio
async def test_fetch_manifest_ena_dedupes_by_experiment():
    from app.services.literature.accession_manifest_service import _ena_filereport_url

    pages = {_ena_filereport_url("SRP071965"): _ENA_TSV}
    result = await AccessionManifestService.fetch_manifest("SRP071965", fetcher=_fake_fetcher(pages))

    assert result.unavailable_reason is None
    assert [s["experiment_accession"] for s in result.samples] == ["SRX1596112", "SRX1596113", "SRX1596114"]
    first = result.samples[0]
    assert first["run_accession"] == "SRR3192657"
    assert first["sample_accession"] == "SRS1329485"
    assert first["title"] == "Tumor replicate 1"
    assert first["condition"] == ""  # ENA read_run carries no clean condition


@pytest.mark.asyncio
async def test_fetch_manifest_geo_parses_and_resolves_runs():
    from app.services.literature.accession_manifest_service import _ena_filereport_url

    pages = {
        geo_series_matrix_url("GSE71585"): _GEO_MATRIX,
        _ena_filereport_url("SRP071965"): _GEO_ENA_TSV,
    }
    result = await AccessionManifestService.fetch_manifest("GSE71585", fetcher=_fake_fetcher(pages))

    assert result.unavailable_reason is None
    assert [s["title"] for s in result.samples] == ["Tumor rep 1", "Tumor rep 2", "Normal rep 1"]
    first = result.samples[0]
    assert first["experiment_accession"] == "SRX1596112"
    assert first["sample_accession"] == "SAMN04502591"
    assert first["condition"] == "tissue: tumor; genotype: WT"
    # run_accession resolved by joining the series' SRA study on experiment_accession
    assert first["run_accession"] == "SRR3192657"


@pytest.mark.asyncio
async def test_fetch_manifest_geo_survives_missing_ena_enrichment():
    # Series matrix present but the SRA-study filereport fetch fails: still return the samples
    # (recognizable by title + condition), just without run accessions.
    pages = {geo_series_matrix_url("GSE71585"): _GEO_MATRIX}
    result = await AccessionManifestService.fetch_manifest("GSE71585", fetcher=_fake_fetcher(pages))

    assert result.unavailable_reason is None
    assert len(result.samples) == 3
    assert result.samples[0]["run_accession"] == ""
    assert result.samples[0]["experiment_accession"] == "SRX1596112"


@pytest.mark.asyncio
async def test_fetch_manifest_fetch_error_returns_empty_with_reason():
    async def _boom(_url):
        raise RuntimeError("network down")

    result = await AccessionManifestService.fetch_manifest("SRP071965", fetcher=_boom)
    assert result.samples == []
    assert result.unavailable_reason  # a human-readable reason, never an exception


@pytest.mark.asyncio
async def test_fetch_manifest_no_accession_is_unavailable():
    result = await AccessionManifestService.fetch_manifest("", fetcher=_fake_fetcher({}))
    assert result.samples == []
    assert result.unavailable_reason


@pytest.mark.asyncio
async def test_fetch_manifest_unknown_accession_type_is_unavailable():
    result = await AccessionManifestService.fetch_manifest("not-an-accession", fetcher=_fake_fetcher({}))
    assert result.samples == []
    assert result.unavailable_reason


# ---- the deposited data's own library strategy (plan_4 step 1) ----
#
# ENA has always been asked for `library_strategy` and the answer was thrown away. It is what tells
# a plan that a paper saying "RRBS and RNA-seq" deposited Bisulfite-Seq, so the run does not go to
# nf-core/rnaseq.

_MIXED_ENA_TSV = (
    "run_accession\texperiment_accession\tsample_accession\tsample_title\texperiment_title\tlibrary_strategy\n"
    "SRR1\tSRX1\tSRS1\tMeth 1\t\tBisulfite-Seq\n"
    "SRR2\tSRX2\tSRS2\tExpr 1\t\tRNA-Seq\n"
)

_BISULFITE_ENA_TSV = (
    "run_accession\texperiment_accession\tsample_accession\tsample_title\texperiment_title\tlibrary_strategy\n"
    "SRR1\tSRX1\tSRS1\tMeth 1\t\tBisulfite-Seq\n"
    "SRR2\tSRX2\tSRS2\tMeth 2\t\tBisulfite-Seq\n"
)


@pytest.mark.asyncio
async def test_ena_manifest_entries_carry_the_library_strategy():
    from app.services.literature.accession_manifest_service import _ena_filereport_url

    pages = {_ena_filereport_url("SRP071965"): _ENA_TSV}
    result = await AccessionManifestService.fetch_manifest("SRP071965", fetcher=_fake_fetcher(pages))

    assert [s["library_strategy"] for s in result.samples] == ["RNA-Seq", "RNA-Seq", "RNA-Seq"]


@pytest.mark.asyncio
async def test_geo_manifest_fills_the_library_strategy_from_ena():
    """A GEO series matrix does not carry the strategy; the ENA join that already fills run
    accessions is where it comes from."""
    from app.services.literature.accession_manifest_service import _ena_filereport_url

    pages = {
        geo_series_matrix_url("GSE71585"): _GEO_MATRIX,
        _ena_filereport_url("SRP071965"): _GEO_ENA_TSV,
    }
    result = await AccessionManifestService.fetch_manifest("GSE71585", fetcher=_fake_fetcher(pages))

    assert [s["library_strategy"] for s in result.samples] == ["RNA-Seq", "RNA-Seq", "RNA-Seq"]


@pytest.mark.asyncio
async def test_geo_manifest_without_ena_enrichment_has_no_strategy():
    pages = {geo_series_matrix_url("GSE71585"): _GEO_MATRIX}
    result = await AccessionManifestService.fetch_manifest("GSE71585", fetcher=_fake_fetcher(pages))

    assert [s["library_strategy"] for s in result.samples] == ["", "", ""]


def test_dominant_library_strategy_is_the_one_the_samples_agree_on():
    samples = [{"library_strategy": "Bisulfite-Seq"}, {"library_strategy": "Bisulfite-Seq"}]
    assert dominant_library_strategy(samples) == "Bisulfite-Seq"


def test_dominant_library_strategy_ignores_case_and_blanks():
    samples = [{"library_strategy": "bisulfite-seq"}, {"library_strategy": ""}, {"library_strategy": "Bisulfite-Seq"}]
    assert dominant_library_strategy(samples) == "bisulfite-seq"


def test_a_multi_assay_accession_has_no_dominant_strategy():
    """An accession carrying two assays is as compound as the prose it would be overriding. No
    answer is the honest one; the paper's own words decide."""
    assert dominant_library_strategy([{"library_strategy": "Bisulfite-Seq"}, {"library_strategy": "RNA-Seq"}]) is None


def test_no_samples_and_no_strategies_yield_nothing():
    assert dominant_library_strategy([]) is None
    assert dominant_library_strategy([{"library_strategy": ""}, {}]) is None


@pytest.mark.asyncio
async def test_a_single_assay_accession_reports_its_strategy_end_to_end():
    from app.services.literature.accession_manifest_service import _ena_filereport_url

    pages = {_ena_filereport_url("SRP0001"): _BISULFITE_ENA_TSV}
    result = await AccessionManifestService.fetch_manifest("SRP0001", fetcher=_fake_fetcher(pages))
    assert dominant_library_strategy(result.samples) == "Bisulfite-Seq"

    pages = {_ena_filereport_url("SRP0002"): _MIXED_ENA_TSV}
    mixed = await AccessionManifestService.fetch_manifest("SRP0002", fetcher=_fake_fetcher(pages))
    assert dominant_library_strategy(mixed.samples) is None


# ---- GEO splits a series matrix per PLATFORM, and then there is no combined file ----
#
# GEO publishes `GSE<n>_series_matrix.txt.gz` only when a series used ONE platform (GPL, the
# sequencing instrument). A series that ran on two instruments is published as one matrix per
# platform and NO combined file, so the single URL 404s and every such study lost its manifest.
#
# Measured on the demo 2026-09-02: of the 12 GEO series this instance references, 10 worked and 2
# failed, and both failures were multi-platform. GSE144396 (the SAMD1 ChIP-seq paper) serves
# GSE144396-GPL18480 and GSE144396-GPL21626; GSE118189 serves -GPL20301 and -GPL24676. The
# directory listing returned 200 from the VM in both cases, so GEO was reachable throughout and the
# error saying otherwise was ours.

_MATRIX_DIR_HTML = """<html><head><title>Index of /geo/series/GSE144nnn/GSE144396/matrix</title></head>
<body><h1>Index of /geo/series/GSE144nnn/GSE144396/matrix</h1>
<pre>Name                       Last modified      Size
<hr><a href="/geo/series/GSE144nnn/GSE144396/">Parent Directory</a>   -
<a href="GSE144396-GPL18480_series_matrix.txt.gz">GSE144396-GPL18480_series_matrix.txt.gz</a> 2026-06-11 01:27  3.7K
<a href="GSE144396-GPL21626_series_matrix.txt.gz">GSE144396-GPL21626_series_matrix.txt.gz</a> 2026-06-11 01:27  4.4K
<hr></pre></body></html>"""


def _matrix(titles, srx, samn, series_rel="SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP248037"):
    def row(tag, values):
        return tag + "\t" + "\t".join(f'"{v}"' for v in values) + "\n"

    return (
        f'!Series_relation\t"{series_rel}"\n'
        + row("!Sample_title", titles)
        + row("!Sample_relation", [f"SRA: https://www.ncbi.nlm.nih.gov/sra?term={x}" for x in srx])
        + row("!Sample_relation", [f"BioSample: https://www.ncbi.nlm.nih.gov/biosample/{x}" for x in samn])
        + "!series_matrix_table_begin\n!series_matrix_table_end\n"
    )


_DIR = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE144nnn/GSE144396/matrix/"
_COMBINED = _DIR + "GSE144396_series_matrix.txt.gz"
_P1 = _DIR + "GSE144396-GPL18480_series_matrix.txt.gz"
_P2 = _DIR + "GSE144396-GPL21626_series_matrix.txt.gz"


def test_geo_matrix_directory_url_is_the_series_matrix_folder():
    from app.services.literature.accession_manifest_service import geo_matrix_dir_url

    assert geo_matrix_dir_url("GSE144396") == _DIR
    assert geo_matrix_dir_url("GSE12") == "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE12/matrix/"
    assert geo_matrix_dir_url("SRP123456") is None


def test_parse_matrix_directory_finds_every_series_matrix_file():
    from app.services.literature.accession_manifest_service import parse_matrix_directory

    assert parse_matrix_directory(_MATRIX_DIR_HTML) == [
        "GSE144396-GPL18480_series_matrix.txt.gz",
        "GSE144396-GPL21626_series_matrix.txt.gz",
    ]


def test_parse_matrix_directory_ignores_everything_that_is_not_a_matrix():
    from app.services.literature.accession_manifest_service import parse_matrix_directory

    html = (
        '<a href="/geo/series/GSE144nnn/GSE144396/">Parent Directory</a>'
        '<a href="filelist.txt">filelist.txt</a>'
        '<a href="GSE144396_RAW.tar">GSE144396_RAW.tar</a>'
        '<a href="GSE144396_series_matrix.txt.gz">GSE144396_series_matrix.txt.gz</a>'
    )
    assert parse_matrix_directory(html) == ["GSE144396_series_matrix.txt.gz"]


@pytest.mark.asyncio
async def test_a_multi_platform_series_merges_every_platforms_samples():
    """The whole defect. Both platform files are read and their samples appear together, because a
    scientist scoping this study needs the ChIP samples AND the RNA-seq samples in one list."""
    pages = {
        _DIR: _MATRIX_DIR_HTML,
        _P1: _matrix(["SAMD1_WT_repl1", "IgG_WT"], ["SRX7660001", "SRX7660002"], ["SAMN14000001", "SAMN14000002"]),
        _P2: _matrix(["H3K27ac_WT", "L3MBTL3(NT)_WT"], ["SRX7660003", "SRX7660004"], ["SAMN14000003", "SAMN14000004"]),
    }
    result = await AccessionManifestService.fetch_manifest("GSE144396", fetcher=_fake_fetcher(pages))

    assert result.unavailable_reason is None
    assert [s["title"] for s in result.samples] == [
        "SAMD1_WT_repl1",
        "IgG_WT",
        "H3K27ac_WT",
        "L3MBTL3(NT)_WT",
    ]
    assert [s["experiment_accession"] for s in result.samples] == [
        "SRX7660001",
        "SRX7660002",
        "SRX7660003",
        "SRX7660004",
    ]


@pytest.mark.asyncio
async def test_a_single_platform_series_still_takes_the_combined_file_directly():
    """10 of the 12 series on the demo work today and must keep working, on the same one request:
    the combined file is tried first and the directory is never listed when it answers."""
    asked = []

    async def fetch(url: str) -> str:
        asked.append(url)
        if url == _COMBINED:
            return _matrix(["WT_1"], ["SRX1"], ["SAMN1"])
        raise RuntimeError(f"404 {url}")

    result = await AccessionManifestService.fetch_manifest("GSE144396", fetcher=fetch)

    assert [s["title"] for s in result.samples] == ["WT_1"]
    # The combined file answered, so the folder is never listed. The ENA join still runs: it fills
    # run accessions and is not what this test is about.
    assert _COMBINED in asked
    assert _DIR not in asked, "a working series must not pay for an extra directory listing"


@pytest.mark.asyncio
async def test_geo_genuinely_unreachable_still_says_so():
    """The message must keep meaning what it says. When the directory listing fails too, GEO really
    is unreachable and that is the honest reason."""

    async def boom(url: str) -> str:
        raise RuntimeError("connection reset")

    result = await AccessionManifestService.fetch_manifest("GSE144396", fetcher=boom)

    assert result.samples == []
    assert result.unavailable_reason == "Could not reach GEO to list this study's samples."


@pytest.mark.asyncio
async def test_a_directory_holding_no_matrix_is_not_reported_as_unreachable():
    """GEO answered. It simply has not published a series matrix for this study, which is a
    different thing from a network failure and must not be reported as one."""
    pages = {_DIR: '<a href="/geo/series/GSE144nnn/GSE144396/">Parent Directory</a>'}
    result = await AccessionManifestService.fetch_manifest("GSE144396", fetcher=_fake_fetcher(pages))

    assert result.samples == []
    assert "no series matrix" in (result.unavailable_reason or "").lower()
    assert "could not reach" not in (result.unavailable_reason or "").lower()


@pytest.mark.asyncio
async def test_one_unreadable_platform_file_does_not_lose_the_others():
    """A partial answer beats an empty one at a picker, but the scientist has to be told it is
    partial, or they will scope an arm from a list that is quietly missing samples."""
    pages = {
        _DIR: _MATRIX_DIR_HTML,
        _P1: _matrix(["SAMD1_WT_repl1"], ["SRX7660001"], ["SAMN14000001"]),
    }
    result = await AccessionManifestService.fetch_manifest("GSE144396", fetcher=_fake_fetcher(pages))

    assert [s["title"] for s in result.samples] == ["SAMD1_WT_repl1"]
    assert "1 of 2" in (result.unavailable_reason or "")


@pytest.mark.asyncio
async def test_the_multi_platform_path_still_fills_run_accessions_from_ena():
    """The ENA join is resolved once for the whole series, not per platform file."""
    ena_url = (
        "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=SRP248037&result=read_run"
        "&fields=run_accession,experiment_accession,sample_accession,sample_title,experiment_title,"
        "library_strategy&format=tsv&download=false"
    )
    pages = {
        _DIR: _MATRIX_DIR_HTML,
        _P1: _matrix(["SAMD1_WT_repl1"], ["SRX7660001"], ["SAMN14000001"]),
        _P2: _matrix(["H3K27ac_WT"], ["SRX7660003"], ["SAMN14000003"]),
        ena_url: (
            "run_accession\texperiment_accession\tsample_accession\tsample_title\texperiment_title\tlibrary_strategy\n"
            "SRR11000001\tSRX7660001\tSAMN14000001\tSAMD1_WT\tChIP\tChIP-Seq\n"
            "SRR11000003\tSRX7660003\tSAMN14000003\tH3K27ac_WT\tChIP\tChIP-Seq\n"
        ),
    }
    result = await AccessionManifestService.fetch_manifest("GSE144396", fetcher=_fake_fetcher(pages))

    assert [s["run_accession"] for s in result.samples] == ["SRR11000001", "SRR11000003"]
    assert {s["library_strategy"] for s in result.samples} == {"ChIP-Seq"}
