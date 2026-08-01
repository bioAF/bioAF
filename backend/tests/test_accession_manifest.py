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
        {"run_accession": "SRR1", "experiment_accession": "SRX1", "sample_accession": "SRS1", "sample_title": "Tumor 1"},
        {"run_accession": "SRR2", "experiment_accession": "SRX2", "sample_accession": "SRS2", "sample_title": "Normal 1"},
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
    '!series_matrix_table_begin\n'
    '"ID_REF"\t"GSM2055667"\t"GSM2055668"\t"GSM2055669"\n'
    '!series_matrix_table_end\n'
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
