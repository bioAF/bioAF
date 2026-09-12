"""change_7.2 section 3: acquisition has three outcomes, and every one of them has a way out.

Study 33's plan carried `EGAS00001003667`; `_choose_from_deposit` read `study.source_accession`,
which is NULL on a study requested by DOI. `list_deposit` received an empty string and returned "the
accession is not a GEO series id", which is the `or 'the accession'` fallback rather than a statement
about EGA. `_hold_deposit` recorded that reason, logged it, and returned False with no transition and
no backoff, and the study repeated the listing every 30 seconds until somebody stopped it by hand.

Study 29 has been in the same loop since 2026-09-07 with a GEO accession, which IS supported. It is
blocked by the empty-accession defect alone.
"""

import pytest
from fastapi import HTTPException

from app.services.validation_acquisition_outcome import (
    ACCESS_REFUSED,
    AWAITING_INPUT,
    BACKOFF_SECONDS,
    DESIGN_INCOMPATIBLE,
    INPUT_UNIDENTIFIED,
    INPUT_UNREADABLE,
    MAX_ATTEMPTS,
    NO_COMPATIBLE_CONTRAST,
    RESOURCE_LIMIT,
    RETRIEVAL_NOT_FOUND,
    RETRIEVAL_TRANSIENT,
    SAMPLE_MAPPING_UNRESOLVED,
    TERMINAL,
    TRANSIENT,
    UNSUPPORTED_PROCESSING,
    acquisition_accession,
    backoff_for,
    classify_hold,
    exhausted,
    exhaustion_detail,
    outcome_for,
    retrieval_cause,
)
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_route_policy import NO_ADAPTER, NO_INPUT, NOT_AUTHORIZED, UNDETERMINED
from app.services.validation_study_service import ValidationStudyService


class TestTheThreeSituationsAreToldapart:
    def test_a_timeout_is_transient(self):
        assert classify_hold("the request to GEO timed out").kind == TRANSIENT

    def test_a_5xx_is_transient(self):
        assert classify_hold("GEO returned 503 Service Unavailable").kind == TRANSIENT

    def test_an_unrecognised_failure_is_transient_not_an_absence(self):
        """Calling an outage an absence is a wrong and terminal verdict on the paper."""
        outcome = classify_hold("something nobody has seen before")
        assert outcome.kind == TRANSIENT
        assert outcome.action == UNDETERMINED

    def test_an_archive_with_no_adapter_is_terminal_whatever_the_wording_was(self):
        """The archive is a fact about bioAF's registry and does not depend on how a downstream
        error phrased itself."""
        outcome = classify_hold("the accession is not a GEO series id", archive="ega")
        assert outcome.kind == TERMINAL
        assert outcome.action == NO_ADAPTER

    def test_the_refusal_names_ega_rather_than_a_geo_pattern_test(self):
        outcome = classify_hold("the accession is not a GEO series id", archive="ega")
        assert "EGA" in outcome.reason
        assert "GEO" not in outcome.reason

    def test_a_supported_archive_is_not_refused_for_its_archive(self):
        assert classify_hold("the request timed out", archive="geo").kind == TRANSIENT

    def test_a_filename_classification_that_finds_nothing_selectable_is_unidentified_not_absent(self):
        """change_7.4 section 1.1 reverses this: a decision from filenames alone never establishes
        an absence. It was `no_input`, which classified the study `missing_data`."""
        outcome = classify_hold("GEO listed 9 supplementary file(s), none of which holds per-feature values")
        assert outcome.kind == TERMINAL
        assert outcome.cause == INPUT_UNIDENTIFIED
        assert outcome.action != NO_INPUT

    def test_a_403_is_access_refused_never_not_authorized(self):
        """change_7.4 section 1.1 reverses this: a refused automated request is not an archive's
        controlled-access model, and a status code never establishes authorization."""
        outcome = classify_hold("403 Forbidden: this account is not authorized for that dataset")
        assert outcome.kind == TERMINAL
        assert outcome.cause == ACCESS_REFUSED
        assert outcome.action != NOT_AUTHORIZED

    def test_a_person_choosing_is_a_visible_wait(self):
        assert classify_hold("a person must select a file at the gate").kind == AWAITING_INPUT


class TestRetriesAreBounded:
    def test_the_interval_is_not_a_fixed_thirty_seconds(self):
        assert 30 not in BACKOFF_SECONDS

    def test_the_interval_grows(self):
        assert list(BACKOFF_SECONDS) == sorted(BACKOFF_SECONDS)
        assert backoff_for(1) < backoff_for(2) < backoff_for(3)

    def test_attempts_run_out(self):
        assert exhausted(MAX_ATTEMPTS) is True
        assert exhausted(MAX_ATTEMPTS - 1) is False


_NON_RETRIEVAL_CAUSES = (
    ACCESS_REFUSED,
    RESOURCE_LIMIT,
    INPUT_UNREADABLE,
    UNSUPPORTED_PROCESSING,
    INPUT_UNIDENTIFIED,
    SAMPLE_MAPPING_UNRESOLVED,
    DESIGN_INCOMPATIBLE,
    NO_COMPATIBLE_CONTRAST,
    NO_INPUT,
    NO_ADAPTER,
    NOT_AUTHORIZED,
)


class TestTheCallerSaysWhatFailed:
    """change_7.4 section 1.1: study 37's design rewrite found an empty arm, the wording matched no
    signature, and it was retried three times and reported as "could not reach the deposit" about a
    file bioAF had downloaded and read. The kind of failure is known where it happens, so it is
    passed from there rather than inferred from the wording afterwards."""

    @pytest.mark.parametrize("cause", (RETRIEVAL_TRANSIENT, RETRIEVAL_NOT_FOUND))
    def test_only_a_failure_to_retrieve_is_retried(self, cause):
        assert outcome_for(cause, "x").kind == TRANSIENT

    @pytest.mark.parametrize("cause", _NON_RETRIEVAL_CAUSES)
    def test_nothing_that_failed_after_retrieval_is_retried(self, cause):
        assert outcome_for(cause, "x").kind == TERMINAL

    def test_a_person_s_turn_is_a_wait(self):
        assert outcome_for(AWAITING_INPUT, "x").kind == AWAITING_INPUT

    @pytest.mark.parametrize(
        "cause,kind",
        [
            (RETRIEVAL_TRANSIENT, "retrieval_failed"),
            (RETRIEVAL_NOT_FOUND, "retrieval_failed"),
            (ACCESS_REFUSED, "access_refused"),
            (RESOURCE_LIMIT, "resource_limit"),
            (INPUT_UNREADABLE, "input_unreadable"),
            (UNSUPPORTED_PROCESSING, "unsupported_processing"),
            (INPUT_UNIDENTIFIED, "input_unidentified"),
            (SAMPLE_MAPPING_UNRESOLVED, "sample_mapping_unresolved"),
            (DESIGN_INCOMPATIBLE, "design_incompatible"),
            (NO_COMPATIBLE_CONTRAST, "no_compatible_contrast"),
            (NO_INPUT, "missing_input"),
            (NO_ADAPTER, "unsupported_acquisition"),
            (NOT_AUTHORIZED, "controlled_access"),
        ],
    )
    def test_each_cause_names_its_own_limitation(self, cause, kind):
        assert outcome_for(cause, "x").limitation_kind == kind

    def test_only_an_established_absence_becomes_a_missing_input(self):
        """`no_input` is reserved for an absence established within a stated scope."""
        others = [c for c in (RETRIEVAL_TRANSIENT, RETRIEVAL_NOT_FOUND, *_NON_RETRIEVAL_CAUSES) if c != NO_INPUT]
        assert all(outcome_for(c, "x").limitation_kind != "missing_input" for c in others)

    def test_the_cause_is_kept_on_the_outcome(self):
        assert outcome_for(DESIGN_INCOMPATIBLE, "x").cause == DESIGN_INCOMPATIBLE

    def test_an_archive_with_no_adapter_is_named(self):
        outcome = outcome_for(NO_ADAPTER, "EGAS1 is deposited in EGA", archive="ega")
        assert outcome.action == NO_ADAPTER
        assert "EGA" in outcome.reason


class TestTheExhaustionSentenceComesFromTheCause:
    def test_could_not_reach_is_written_only_for_a_transient_failure(self):
        reached = exhaustion_detail(RETRIEVAL_TRANSIENT, attempts=3, resource="the deposit", reason="timed out")
        assert "could not reach" in reached
        missing = exhaustion_detail(
            RETRIEVAL_NOT_FOUND, attempts=3, resource="GSE1_counts.tsv.gz", location="https://x/GSE1_counts.tsv.gz"
        )
        assert "could not reach" not in missing

    def test_a_404_names_the_location_and_asserts_no_absence(self):
        detail = exhaustion_detail(
            RETRIEVAL_NOT_FOUND, attempts=3, resource="GSE1_counts.tsv.gz", location="https://x/GSE1_counts.tsv.gz"
        )
        assert "not found at https://x/GSE1_counts.tsv.gz" in detail
        assert "not established" in detail

    def test_the_attempts_are_counted(self):
        assert "3 attempts" in exhaustion_detail(RETRIEVAL_TRANSIENT, attempts=3, resource="GSE1", reason="x")


def _status_error(code: int):
    import httpx

    request = httpx.Request("GET", "https://ftp.ncbi.nlm.nih.gov/geo/series/x")
    return httpx.HTTPStatusError(f"{code}", request=request, response=httpx.Response(code, request=request))


class TestARetrievalErrorIsReadForWhatItWas:
    """Text classification survives only for the exception a retrieval call raised, and there
    "unrecognised means transient" still holds: calling an outage an absence is a wrong and terminal
    verdict."""

    @pytest.mark.parametrize("code", [404, 410])
    def test_not_found_at_that_location(self, code):
        assert retrieval_cause(_status_error(code)) == RETRIEVAL_NOT_FOUND

    @pytest.mark.parametrize("code", [401, 403, 407])
    def test_a_refused_request(self, code):
        assert retrieval_cause(_status_error(code)) == ACCESS_REFUSED

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_the_server_side_is_transient(self, code):
        assert retrieval_cause(_status_error(code)) == RETRIEVAL_TRANSIENT

    def test_a_timeout_is_transient(self):
        import httpx

        assert retrieval_cause(httpx.ReadTimeout("timed out")) == RETRIEVAL_TRANSIENT

    def test_an_unrecognised_error_is_transient_not_an_absence(self):
        assert retrieval_cause(RuntimeError("something nobody has seen before")) == RETRIEVAL_TRANSIENT

    def test_a_status_in_the_text_is_read_when_no_response_is_attached(self):
        assert retrieval_cause(RuntimeError("404 https://x/f.txt")) == RETRIEVAL_NOT_FOUND
        assert retrieval_cause(RuntimeError("403 Forbidden")) == ACCESS_REFUSED

    def test_a_file_over_the_limit_is_a_resource_limit(self):
        from app.services.deposit_acquisition import DepositTooLargeError

        assert retrieval_cause(DepositTooLargeError("over the 2.0 GB limit")) == RESOURCE_LIMIT


class TestTheAccessionIsResolvedTheWayDiscoveryDoes:
    def test_a_study_requested_by_doi_uses_the_extracted_accession(self):
        """The empty-string defect. Both studies 29 and 33 died on it."""
        target = acquisition_accession([{"accession": "EGAS00001003667", "provenance": "extracted"}])
        assert target["accession"] == "EGAS00001003667"
        assert target["archive"] == "ega"

    def test_the_requested_accession_keeps_its_authority(self):
        """ "The paper also deposited this" is evidence about the paper, not an instruction to fetch
        it instead of what was asked for."""
        target = acquisition_accession(
            [
                {"accession": "GSE309060", "provenance": "requested"},
                {"accession": "EGAS00001003667", "provenance": "extracted"},
            ]
        )
        assert target["accession"] == "GSE309060"

    def test_a_route_can_prefer_an_archive_among_extracted_accessions(self):
        target = acquisition_accession(
            [
                {"accession": "EGAS00001003667", "provenance": "extracted"},
                {"accession": "GSE309060", "provenance": "extracted"},
            ],
            prefer_archive="geo",
        )
        assert target["accession"] == "GSE309060"

    def test_a_sample_accession_is_not_something_a_route_can_be_pointed_at(self):
        assert acquisition_accession([{"accession": "GSM12345", "provenance": "extracted"}]) is None

    def test_nothing_named_resolves_to_nothing(self):
        assert acquisition_accession([]) is None
        assert acquisition_accession([{"accession": "", "provenance": "requested"}]) is None


async def _acquiring(session, admin_user, *, state="acquiring_processed", evidence=None, accession="GSE309060"):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession=accession, intended_route="deposit"
    )
    study.state = state
    study.evidence_json = evidence or {"capabilities": {"preprocessed_data": {"value": "yes"}, "deposits": []}}
    await session.flush()
    return study


class TestATransientFailureBacksOffAndThenReports:
    @pytest.mark.asyncio
    async def test_the_first_failure_schedules_a_retry_rather_than_looping(self, session, admin_user):
        study = await _acquiring(session, admin_user)
        evidence = dict(study.evidence_json)
        held = await ValidationDriverService._hold_deposit(session, study, evidence, "the request to GEO timed out")
        assert held is False
        assert study.state == "acquiring_processed"
        assert study.evidence_json["acquisition_retry_at"]

    @pytest.mark.asyncio
    async def test_the_attempts_run_out_and_the_study_stops_holding(self, session, admin_user):
        study = await _acquiring(session, admin_user)
        for _ in range(MAX_ATTEMPTS):
            await ValidationDriverService._hold_deposit(
                session, study, dict(study.evidence_json), "the request to GEO timed out"
            )
        assert study.state == "classified"

    @pytest.mark.asyncio
    async def test_an_exhausted_discovery_never_hardens_into_an_absence(self, session, admin_user):
        """Nothing was established, so nothing about the paper may be concluded."""
        study = await _acquiring(session, admin_user)
        for _ in range(MAX_ATTEMPTS):
            await ValidationDriverService._hold_deposit(
                session, study, dict(study.evidence_json), "the request to GEO timed out"
            )
        assert study.classification == "inconclusive"
        assert study.classification != "missing_data"

    @pytest.mark.asyncio
    async def test_it_says_a_further_attempt_needs_resumption(self, session, admin_user):
        study = await _acquiring(session, admin_user)
        for _ in range(MAX_ATTEMPTS):
            await ValidationDriverService._hold_deposit(
                session, study, dict(study.evidence_json), "the request to GEO timed out"
            )
        assert study.evidence_json["discovery_unresolved"]["resume"]

    @pytest.mark.asyncio
    async def test_the_driver_waits_out_the_backoff_instead_of_ticking(self, session, admin_user):
        study = await _acquiring(session, admin_user)
        await ValidationDriverService._hold_deposit(
            session, study, dict(study.evidence_json), "the request to GEO timed out"
        )
        assert await ValidationDriverService._handle_acquiring_processed(session, study) is False


class TestATerminalRefusalStatesWhichOneItIs:
    @pytest.mark.asyncio
    async def test_an_unsupported_archive_reaches_an_outcome_on_the_first_failure(self, session, admin_user):
        study = await _acquiring(session, admin_user, accession="EGAS00001003667")
        await ValidationDriverService._hold_deposit(
            session, study, dict(study.evidence_json), "the accession is not a GEO series id", archive="ega"
        )
        assert study.state == "classified"

    @pytest.mark.asyncio
    async def test_it_does_not_retry_something_that_can_never_work(self, session, admin_user):
        study = await _acquiring(session, admin_user, accession="EGAS00001003667")
        await ValidationDriverService._hold_deposit(
            session, study, dict(study.evidence_json), "the accession is not a GEO series id", archive="ega"
        )
        assert "acquisition_retry_at" not in study.evidence_json

    @pytest.mark.asyncio
    async def test_the_recorded_action_names_the_axis(self, session, admin_user):
        study = await _acquiring(session, admin_user, accession="EGAS00001003667")
        await ValidationDriverService._hold_deposit(
            session, study, dict(study.evidence_json), "the accession is not a GEO series id", archive="ega"
        )
        assert study.evidence_json["deposit_failed"]["action"] == NO_ADAPTER


class TestAWaitIsVisibleAndLoggedOnce:
    @pytest.mark.asyncio
    async def test_a_person_s_turn_is_recorded_with_the_action_that_resolves_it(self, session, admin_user):
        study = await _acquiring(session, admin_user)
        await ValidationDriverService._hold_deposit(
            session, study, dict(study.evidence_json), "a person must select a file at the gate"
        )
        waiting = study.evidence_json["awaiting_choice"]
        assert waiting["action"]
        assert study.state == "acquiring_processed"


class TestStoppingAndResumingNeverNeedTheDatabase:
    @pytest.mark.asyncio
    async def test_an_acquiring_study_can_be_cancelled(self, session, admin_user):
        study = await _acquiring(session, admin_user)
        cancelled = await ValidationStudyService.cancel_acquisition(
            session, study.id, admin_user.organization_id, admin_user.id, "wrong accession"
        )
        assert cancelled.state == "classified"
        assert cancelled.evidence_json["cancelled"]["reason"] == "wrong accession"

    @pytest.mark.asyncio
    async def test_a_study_inspecting_a_deposit_can_be_cancelled(self, session, admin_user):
        study = await _acquiring(session, admin_user, state="inspecting_deposit")
        cancelled = await ValidationStudyService.cancel_acquisition(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert cancelled.state == "classified"

    @pytest.mark.asyncio
    async def test_cancelling_invalidates_the_running_claim(self, session, admin_user):
        """A cancellation that leaves the claim valid can be overwritten by a late worker."""
        from app.services import validation_ownership as own

        study = await _acquiring(session, admin_user)
        await session.commit()
        claim = await own.acquire(session, study.id, holder="driver")
        await ValidationStudyService.cancel_acquisition(session, study.id, admin_user.organization_id, admin_user.id)
        with pytest.raises(own.ClaimLost):
            await own.assert_held(session, claim)

    @pytest.mark.asyncio
    async def test_a_stopped_study_resumes_at_the_gate(self, session, admin_user):
        study = await _acquiring(session, admin_user)
        await ValidationStudyService.cancel_acquisition(session, study.id, admin_user.organization_id, admin_user.id)
        resumed = await ValidationStudyService.resume_study(
            session, study.id, admin_user.organization_id, admin_user.id, "the accession was corrected"
        )
        assert resumed.state == "plan_ready"

    @pytest.mark.asyncio
    async def test_a_study_parked_in_error_resumes_the_same_way(self, session, admin_user):
        """Studies 29 and 33 were parked in `error` by hand and have to come back through the product."""
        study = await _acquiring(session, admin_user, state="error")
        resumed = await ValidationStudyService.resume_study(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert resumed.state == "plan_ready"

    @pytest.mark.asyncio
    async def test_resuming_clears_the_counters_that_would_refuse_the_new_attempt(self, session, admin_user):
        study = await _acquiring(
            session,
            admin_user,
            state="error",
            evidence={"acquisition_attempts": 3, "route_blocked": {"reason": "x"}},
        )
        resumed = await ValidationStudyService.resume_study(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert "acquisition_attempts" not in resumed.evidence_json
        assert "route_blocked" not in resumed.evidence_json

    @pytest.mark.asyncio
    async def test_the_resumption_is_on_the_record(self, session, admin_user):
        study = await _acquiring(session, admin_user, state="error")
        resumed = await ValidationStudyService.resume_study(
            session, study.id, admin_user.organization_id, admin_user.id, "credentials arrived"
        )
        assert resumed.evidence_json["resumed"]["reason"] == "credentials arrived"

    @pytest.mark.asyncio
    async def test_a_study_that_is_not_acquiring_cannot_be_cancelled(self, session, admin_user):
        study = await _acquiring(session, admin_user, state="plan_ready")
        with pytest.raises(HTTPException):
            await ValidationStudyService.cancel_acquisition(
                session, study.id, admin_user.organization_id, admin_user.id
            )
