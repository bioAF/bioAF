"""plan_8_5 section 3.4: going and getting the deposit's sample records, for every paper that has one.

What this replaces: one lookup, built from ``study.source_accession``. A paper submitted by DOI has
no source accession, so the URL was never built, nothing was fetched, and the species check reported
"the deposit declares no organism to compare against" about a deposit it had never opened.

Two rules hold this together. The accessions looked up are the ones the study RESOLVED, wherever
they came from. And a failure to retrieve is recorded as bioAF's limitation with its reason, never
as a statement about what the deposit holds.
"""

import pytest

from app.services.validation_sample_records import collect_sample_records

_MATRIX = "\n".join(
    [
        '!Sample_title\t"WT rep1"\t"KO rep1"',
        '!Sample_geo_accession\t"GSM1"\t"GSM2"',
        '!Sample_organism_ch1\t"Homo sapiens"\t"Homo sapiens"',
        '!Sample_source_name_ch1\t"HepG2 cells"\t"HepG2 cells"',
        '!Sample_characteristics_ch1\t"genotype: WT"\t"genotype: SAMD1 KO"',
        "",
    ]
)


def _fetcher(pages: dict):
    async def fetch(url: str) -> str:
        for fragment, body in pages.items():
            if fragment in url:
                return body
        raise RuntimeError(f"nothing serves {url}")

    return fetch


class TestWhichDepositsAreLookedUp:
    @pytest.mark.asyncio
    async def test_a_paper_submitted_by_doi_still_reaches_the_series_it_names(self):
        """The accession came from the reading, not from the request, and it is just as resolved."""
        held = await collect_sample_records(
            deposits=[{"accession": "GSE144396", "archive": "geo", "provenance": "extracted"}],
            fetcher=_fetcher({"GSE144396": _MATRIX}),
        )
        assert [d["accession"] for d in held["deposits"]] == ["GSE144396"]
        assert held["deposits"][0]["sample_count"] == 2
        assert held["deposits"][0]["samples"][0]["organism"] == "Homo sapiens"

    @pytest.mark.asyncio
    async def test_the_record_says_where_it_came_from(self):
        held = await collect_sample_records(
            deposits=[{"accession": "GSE144396", "archive": "geo"}], fetcher=_fetcher({"GSE144396": _MATRIX})
        )
        deposit = held["deposits"][0]
        assert deposit["source"].startswith("https://")
        assert deposit["at"]
        assert deposit["sha256"]

    @pytest.mark.asyncio
    async def test_one_accession_named_twice_is_fetched_once(self):
        calls = []

        async def counting(url: str) -> str:
            calls.append(url)
            return _MATRIX

        await collect_sample_records(
            deposits=[
                {"accession": "GSE144396", "archive": "geo", "provenance": "requested"},
                {"accession": "gse144396", "archive": "geo", "provenance": "extracted"},
            ],
            fetcher=counting,
        )
        assert len(calls) == 1


class TestAFailureIsBioafsAndSaysSo:
    @pytest.mark.asyncio
    async def test_an_archive_with_no_adapter_is_a_limitation_not_an_empty_deposit(self):
        held = await collect_sample_records(
            deposits=[{"accession": "EGAS00001004241", "archive": "ega"}], fetcher=_fetcher({})
        )
        assert held["deposits"] == []
        limitation = held["limitations"][0]
        assert limitation["accession"] == "EGAS00001004241"
        assert "EGA" in limitation["reason"]
        assert "declares no" not in limitation["reason"]

    @pytest.mark.asyncio
    async def test_an_unreachable_matrix_is_recorded_with_its_reason(self):
        held = await collect_sample_records(
            deposits=[{"accession": "GSE144396", "archive": "geo"}], fetcher=_fetcher({})
        )
        assert held["deposits"] == []
        assert "could not be read" in held["limitations"][0]["reason"]

    @pytest.mark.asyncio
    async def test_a_matrix_that_states_no_organism_is_still_a_record_that_was_read(self):
        """Read and found nothing is a fact about the deposit. Never having looked is not."""
        held = await collect_sample_records(
            deposits=[{"accession": "GSE1", "archive": "geo"}],
            fetcher=_fetcher({"GSE1": '!Sample_geo_accession\t"GSM9"\n'}),
        )
        assert held["deposits"][0]["samples"][0]["organism"] == ""
        assert held["limitations"] == []

    @pytest.mark.asyncio
    async def test_nothing_to_look_up_is_neither_a_record_nor_a_failure(self):
        held = await collect_sample_records(deposits=[], fetcher=_fetcher({}))
        assert held == {"deposits": [], "limitations": []}
