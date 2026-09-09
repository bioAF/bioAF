"""change_7.1 section 4: a study that cannot run reaches an outcome, not an indefinite hold.

`_handle_plan_ready` wrote `evidence["route_blocked"]` and returned without transitioning, so the
study sat at `plan_ready` for ever. Groff et al. is still sitting there. To a reader that state is
indistinguishable from a study waiting for someone to approve it, and the driver re-examined it
every 30 seconds without ever changing anything.

**Controlled access is not missing data.** The authors deposited 54 samples and 108 FASTQ files.
Classifying that as `missing_data` states something false about the paper; `access_restricted`
states the fact and puts the limitation where it belongs, on bioAF.

**The route explanation has to come from evidence.** The stored messages named GEO on a paper with
no GEO deposit, and recommended "the remaining route" without ever checking whether that route
could run either.
"""

from app.models.validation_study import (
    VALIDATION_STUDY_CLASSIFICATIONS,
    VALIDATION_STUDY_TRANSITIONS,
)
from app.services.validation_driver_service import _route_unavailable_reason

ACCESS_RESTRICTED = "access_restricted"

_EGA_CAPS = {
    "deposit_exists": {"value": "yes", "evidence": "EGA dataset EGAD00001005044", "failure_reason": None},
    "raw_data": {"value": "yes", "evidence": "EGA lists 108 fastq.gz file(s)", "failure_reason": None},
    "preprocessed_data": {"value": "no", "evidence": "no processed matrix", "failure_reason": None},
    "deposits": [
        {
            "archive": "ega",
            "accession": "EGAS00001003667",
            "scoped": True,
            "exists": "yes",
            "access": "controlled",
            "supported": "no",
            "raw_data": "yes",
            "preprocessed_data": "no",
            "evidence": "EGA dataset EGAD00001005044 is controlled access",
            "failure_reason": None,
        }
    ],
}


class TestTheOutcomeExists:
    def test_access_restricted_is_a_classification(self):
        assert ACCESS_RESTRICTED in VALIDATION_STUDY_CLASSIFICATIONS

    def test_it_is_not_missing_data(self):
        """A paper that deposited 108 FASTQ files did not omit its data."""
        assert ACCESS_RESTRICTED != "missing_data"

    def test_a_blocked_study_can_leave_plan_ready(self):
        assert "classified" in VALIDATION_STUDY_TRANSITIONS["plan_ready"]

    def test_a_classified_study_can_be_resumed_when_access_arrives(self):
        """Credentials arriving later must reopen the study without erasing what was found."""
        assert "plan_ready" in VALIDATION_STUDY_TRANSITIONS["classified"]


class TestTheExplanationComesFromEvidence:
    def test_a_route_needing_unacquirable_data_is_refused(self):
        """`raw_data` is YES: the reads exist. They are controlled, and bioAF has no EGA client, so
        offering the pipeline route would approve a run that cannot start."""
        assert _route_unavailable_reason("pipeline", _EGA_CAPS) is not None

    def test_the_refusal_names_the_real_obstacle(self):
        reason = _route_unavailable_reason("pipeline", _EGA_CAPS)
        assert "controlled" in reason.lower()
        assert "EGAS00001003667" in reason or "EGA" in reason

    def test_it_never_claims_a_geo_deposit_a_paper_does_not_have(self):
        for route in ("deposit", "pipeline"):
            reason = _route_unavailable_reason(route, _EGA_CAPS) or ""
            assert "GEO" not in reason

    def test_it_does_not_recommend_a_route_it_has_not_checked(self):
        """The stored message ended 'Validating from the raw reads is the remaining route.' on a
        paper whose raw reads are controlled. Recommending a route without establishing that it can
        run is how a blocked study was told to try something that also cannot run."""
        reason = _route_unavailable_reason("deposit", _EGA_CAPS) or ""
        assert "remaining route" not in reason

    def test_an_unknown_answer_still_never_refuses_a_route(self):
        """plan_7 amendment 3 stands: a route is refused on an ESTABLISHED absence, never on a
        discovery failure."""
        caps = {
            "raw_data": {"value": "unknown", "evidence": None, "failure_reason": "ENA timed out"},
            "preprocessed_data": {"value": "unknown", "evidence": None, "failure_reason": "GEO timed out"},
            "deposits": [],
        }
        assert _route_unavailable_reason("pipeline", caps) is None
        assert _route_unavailable_reason("deposit", caps) is None

    def test_an_acquirable_geo_deposit_is_still_offered(self):
        caps = {
            "raw_data": {"value": "yes", "evidence": "ENA publishes FASTQ for 6 of 6 run(s)"},
            "preprocessed_data": {"value": "yes", "evidence": "1 of 3 file(s) could serve"},
            "deposits": [
                {
                    "archive": "geo",
                    "accession": "GSE274331",
                    "scoped": True,
                    "exists": "yes",
                    "access": "public",
                    "supported": "yes",
                    "raw_data": "yes",
                    "preprocessed_data": "yes",
                    "evidence": "GEO published a series record",
                    "failure_reason": None,
                }
            ],
        }
        assert _route_unavailable_reason("pipeline", caps) is None
        assert _route_unavailable_reason("deposit", caps) is None


class TestPubliclyCheckableWorkStillHappens:
    def test_a_blocked_route_does_not_skip_the_checks_that_need_no_compute(self):
        """The assessment is what merges, not the reproduction. Reading the metadata, inspecting
        the supplied code and checking the results table need no cluster and no credentials."""
        from app.services.validation_assessment import independent_checks_outstanding

        study_evidence = {
            "capabilities": _EGA_CAPS,
            "supplements": [{"label": "Supplemental File S1", "resolved": False, "role": "unknown"}],
        }
        assert independent_checks_outstanding(study_evidence) is True

    def test_nothing_is_outstanding_once_the_supplements_are_inspected(self):
        from app.services.validation_assessment import independent_checks_outstanding

        study_evidence = {
            "capabilities": _EGA_CAPS,
            "supplements": [{"label": "Supplemental File S1", "resolved": True, "role": "sample_metadata"}],
        }
        assert independent_checks_outstanding(study_evidence) is False
