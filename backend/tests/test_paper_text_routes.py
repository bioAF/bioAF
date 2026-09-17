"""plan_8_3 section 1.4: why one source failed, and which other routes are still open.

Study 49's read failed at Europe PMC and bioAF recorded it as having no text. The account of that one
source was accurate and it is not a fact about the paper: the Literature Library's stored text and
text supplied to `/read` are both existing routes, and neither was reported as available.

Four facts are kept apart because they have four remedies: bioAF could not reach the endpoint; the
API answered and holds no open full text for this paper; another route can still supply the text; and
what the paper's data actually is, which none of the above establishes.

And a text supplied by hand does not carry Europe PMC's supplement manifest. That is a limit of the
route, never evidence that the paper published no supplements.
"""

import httpx
import pytest

from app.services.validation_paper_text import (
    ENDPOINT_FAILED,
    LIBRARY,
    NOT_OPEN_ACCESS,
    NO_IDENTIFIER,
    PASTED,
    SUCCEEDED,
    acquisition_record,
)


class _Study:
    def __init__(self, doi=None, paper_id=None):
        self.id = 49
        self.source_doi = doi
        self.paper_id = paper_id


class TestWhatOneSourcesFailureEstablishes:
    def test_an_unreachable_endpoint_is_named_as_bioafs_reach_not_the_papers_absence(self):
        record = acquisition_record(
            [{"source": "europe_pmc", "outcome": ENDPOINT_FAILED, "detail": "HTTP 500"}], text_source=None
        )
        assert record["established"] is False
        assert record["attempts"][0]["outcome"] == ENDPOINT_FAILED
        assert "could not reach" in record["reason"]
        assert "does not establish" in record["reason"]

    def test_a_paper_that_is_not_open_access_says_so_and_names_the_routes_left(self):
        record = acquisition_record(
            [{"source": "europe_pmc", "outcome": NOT_OPEN_ACCESS, "detail": "isOpenAccess: N"}], text_source=None
        )
        assert record["established"] is False
        assert "open" in record["reason"]
        assert LIBRARY in record["routes_remaining"]
        assert PASTED in record["routes_remaining"]

    def test_a_study_with_no_identifier_to_look_up_is_its_own_outcome(self):
        record = acquisition_record(
            [{"source": "europe_pmc", "outcome": NO_IDENTIFIER, "detail": None}], text_source=None
        )
        assert record["attempts"][0]["outcome"] == NO_IDENTIFIER

    def test_a_route_that_answered_leaves_nothing_remaining(self):
        record = acquisition_record(
            [
                {"source": "europe_pmc", "outcome": ENDPOINT_FAILED, "detail": "HTTP 500"},
                {"source": "library", "outcome": SUCCEEDED, "detail": None},
            ],
            text_source=LIBRARY,
        )
        assert record["established"] is True
        assert record["routes_remaining"] == []
        assert record["source"] == LIBRARY

    def test_an_exhausted_set_of_routes_says_which_were_tried(self):
        record = acquisition_record(
            [
                {"source": "europe_pmc", "outcome": NOT_OPEN_ACCESS, "detail": None},
                {"source": "library", "outcome": "no_stored_text", "detail": None},
            ],
            text_source=None,
        )
        assert record["routes_remaining"] == [PASTED]
        assert "Europe PMC" in record["reason"]


class TestTheEuropePmcAttemptSaysWhichFailureItWas(object):
    @pytest.mark.asyncio
    async def test_a_server_error_is_an_endpoint_failure(self, monkeypatch):
        from app.services import validation_paper_text as text_routes

        async def _fetch(**kw):
            raise httpx.HTTPStatusError(
                "500",
                request=httpx.Request("GET", "https://x"),
                response=httpx.Response(500, request=httpx.Request("GET", "https://x")),
            )

        monkeypatch.setattr(text_routes.FullTextFetchService, "fetch", _fetch)
        found, attempt = await text_routes.try_europe_pmc(_Study(doi="10.1/x"))
        assert found is None
        assert attempt["outcome"] == ENDPOINT_FAILED
        assert "500" in str(attempt["detail"])

    @pytest.mark.asyncio
    async def test_no_open_full_text_is_not_an_endpoint_failure(self, monkeypatch):
        from app.services import validation_paper_text as text_routes

        async def _fetch(**kw):
            return None

        monkeypatch.setattr(text_routes.FullTextFetchService, "fetch", _fetch)
        found, attempt = await text_routes.try_europe_pmc(_Study(doi="10.1/x"))
        assert found is None
        assert attempt["outcome"] == NOT_OPEN_ACCESS

    @pytest.mark.asyncio
    async def test_a_study_with_no_doi_never_calls_the_api(self, monkeypatch):
        from app.services import validation_paper_text as text_routes

        calls = []

        async def _fetch(**kw):
            calls.append(kw)
            return None

        monkeypatch.setattr(text_routes.FullTextFetchService, "fetch", _fetch)
        found, attempt = await text_routes.try_europe_pmc(_Study())
        assert attempt["outcome"] == NO_IDENTIFIER
        assert calls == []


class TestAManuallySuppliedTextCarriesNoSupplementManifest:
    def test_europe_pmcs_text_establishes_the_manifest(self):
        from app.services.validation_paper_text import EUROPE_PMC, PaperText

        found = PaperText(text="t", source=EUROPE_PMC, supplements=[{"label": "S1"}])
        assert found.supplements_established is True

    @pytest.mark.parametrize("source", [LIBRARY, PASTED])
    def test_another_route_does_not_and_says_so(self, source):
        from app.services.validation_paper_text import PaperText

        found = PaperText(text="t", source=source, supplements=[])
        assert found.supplements_established is False
        assert "does not" in found.supplement_limitation

    def test_the_record_carries_it_so_no_reader_treats_an_empty_list_as_an_absence(self):
        from app.services.validation_paper_text import PaperText

        record = PaperText(text="t", source=PASTED, supplements=[]).record()
        assert record["supplements_established"] is False
        assert record["supplement_limitation"]
