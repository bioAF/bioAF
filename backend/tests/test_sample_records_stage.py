"""plan_8_5 sections 3.1 and 3.4: the assessment stage goes and gets the deposit's sample records.

The stage runs on every authorized study, whether reproduction is possible, blocked or unsupported,
so a documentary obligation that needs the deposit's own metadata has it in hand before any input is
acquired and before anyone approves compute. What it retrieves is persisted with where it came from,
and what it could not retrieve is persisted as bioAF's limitation.
"""

import pytest

from app.services.validation_assessment import run_assessment
from app.services.validation_study_service import ValidationStudyService

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


async def _study(session, admin_user, *, source_accession="", deposits=None):
    study = await ValidationStudyService.create_study(
        session,
        admin_user.organization_id,
        admin_user.id,
        source_accession=source_accession,
        intended_route="deposit",
    )
    study.state = "acquiring_processed"
    study.evidence_json = {"capabilities": {"deposits": deposits or []}}
    await session.flush()
    return study


def _serving(body: str | None):
    async def fetch(url: str) -> str:
        if body is None:
            raise RuntimeError("the matrix is unreachable")
        return body

    return fetch


class TestTheDepositsSampleRecordsAreHeldBeforeAnyInputIsAcquired:
    @pytest.mark.asyncio
    async def test_a_study_requested_by_doi_still_holds_the_series_the_paper_named(self, session, admin_user):
        study = await _study(
            session, admin_user, deposits=[{"accession": "GSE144396", "archive": "geo", "provenance": "extracted"}]
        )
        await run_assessment(session, study, fetcher=_serving(_MATRIX))
        held = study.evidence_json["sample_records"]
        assert held["deposits"][0]["accession"] == "GSE144396"
        assert [s["accession"] for s in held["deposits"][0]["samples"]] == ["GSM1", "GSM2"]

    @pytest.mark.asyncio
    async def test_a_deposit_that_could_not_be_opened_is_recorded_as_bioafs_limitation(self, session, admin_user):
        study = await _study(session, admin_user, deposits=[{"accession": "GSE1", "archive": "geo"}])
        await run_assessment(session, study, fetcher=_serving(None))
        held = study.evidence_json["sample_records"]
        assert held["deposits"] == []
        assert "could not be read" in held["limitations"][0]["reason"]

    @pytest.mark.asyncio
    async def test_records_already_held_for_the_same_deposits_are_not_fetched_again(self, session, admin_user):
        calls = []

        async def counting(url: str) -> str:
            calls.append(url)
            return _MATRIX

        study = await _study(session, admin_user, deposits=[{"accession": "GSE144396", "archive": "geo"}])
        await run_assessment(session, study, fetcher=counting)
        study.evidence_json = {**study.evidence_json, "assessment": None}
        await run_assessment(session, study, fetcher=counting)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_study_naming_no_deposit_records_nothing_and_fails_nothing(self, session, admin_user):
        study = await _study(session, admin_user)
        record = await run_assessment(session, study, fetcher=_serving(_MATRIX))
        assert record is not None
        assert (study.evidence_json.get("sample_records") or {"deposits": []})["deposits"] == []


class TestTheSpeciesCheckReadsWhatWasActuallyOpened:
    """plan_8_5 section 3.4: the read-time check compares the records bioAF retrieved.

    It used to build one URL from ``study.source_accession``. A paper submitted by DOI has none, so
    nothing was fetched, and the check reported "the deposit declares no organism to compare
    against" about a series nobody had opened. Absence of a lookup is not absence of an organism.
    """

    @pytest.mark.asyncio
    async def test_a_doi_submitted_study_compares_against_the_series_the_paper_names(self, session, admin_user):
        from app.services.validation_driver_service import ValidationDriverService

        study = await _study(
            session, admin_user, deposits=[{"accession": "GSE144396", "archive": "geo", "provenance": "extracted"}]
        )
        organisms, looked = await ValidationDriverService._deposit_organisms(study, fetcher=_serving(_MATRIX))
        assert organisms == ["Homo sapiens"]
        assert looked is True

    @pytest.mark.asyncio
    async def test_a_deposit_that_was_never_opened_says_that_rather_than_declaring_nothing(self, session, admin_user):
        from app.services.validation_driver_service import ValidationDriverService
        from app.services.validation_precompute_checks import UNKNOWN, check_species

        study = await _study(session, admin_user, deposits=[{"accession": "EGAS1", "archive": "ega"}])
        organisms, looked = await ValidationDriverService._deposit_organisms(study, fetcher=_serving(_MATRIX))
        assert organisms == []
        assert looked is False
        check = check_species("Homo sapiens", organisms, organism_source=looked)
        assert check["verdict"] == UNKNOWN
        assert "declares no organism" not in check["detail"]


class TestAFailedRetrievalIsNotAnAnswerToKeep:
    """plan_8_5 section 3.4, from the live run: a deposit that could not be reached stayed unread.

    The held records are reused when the study's deposits have not changed, which is what keeps a
    refresh free. But a retrieval FAILURE establishes nothing about the deposit, and caching it
    meant the next assessment never tried again: study 64's GSE144396 read as unreachable forever.
    An archive bioAF has no adapter for is different, and asking again would cost a request to learn
    what it already knows.
    """

    @pytest.mark.asyncio
    async def test_a_deposit_that_could_not_be_reached_is_tried_again(self, session, admin_user):
        runs = {"n": 0}

        async def flaky(url: str) -> str:
            # The first assessment cannot reach GEO at all: neither the combined matrix nor the
            # folder that would say which per-platform matrices exist.
            if runs["n"] == 0:
                raise RuntimeError("the matrix is unreachable")
            if url.endswith("/matrix/"):
                raise RuntimeError("no folder was asked for")
            return _MATRIX

        study = await _study(session, admin_user, deposits=[{"accession": "GSE1", "archive": "geo"}])
        await run_assessment(session, study, fetcher=flaky)
        assert study.evidence_json["sample_records"]["deposits"] == []
        runs["n"] = 1
        await run_assessment(session, study, fetcher=flaky)
        assert study.evidence_json["sample_records"]["deposits"][0]["sample_count"] == 2

    @pytest.mark.asyncio
    async def test_an_archive_with_no_adapter_is_not_asked_about_again(self, session, admin_user):
        calls = []

        async def counting(url: str) -> str:
            calls.append(url)
            return _MATRIX

        study = await _study(session, admin_user, deposits=[{"accession": "EGAS1", "archive": "ega"}])
        await run_assessment(session, study, fetcher=counting)
        await run_assessment(session, study, fetcher=counting)
        assert calls == [], "bioAF has no adapter for it, and asking again cannot change that"
