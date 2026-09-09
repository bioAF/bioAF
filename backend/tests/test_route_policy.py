"""change_7.2 section 1: one route policy, four independent axes, six distinguishable actions.

Study 33 was approved by hand onto a deposit route that could never be taken. `_handle_plan_ready`
had a feasibility check and `approve_plan` had no equivalent, so which door a study came through
decided whether its route was validated at all.

The four questions the policy answers are independent and must stay that way. A missing adapter, a
deposit holding nothing usable and an organization with no authorization are three different
refusals with three different remedies, and one wording for all three tells a reader nothing about
which of them to act on.
"""

import pytest

from app.services.validation_route_policy import (
    ACTIONS,
    CONTESTED,
    NO_ADAPTER,
    NO_INPUT,
    NOT_AUTHORIZED,
    PROCEED,
    UNDETERMINED,
    decide_route,
)

_EGA_DEPOSIT = {
    "archive": "ega",
    "accession": "EGAS00001003667",
    "exists": "yes",
    "access": "controlled",
    "supported": "no",
    "raw_data": "yes",
    "preprocessed_data": "no",
}

# A supported archive holding controlled data: the only shape that can reach `not_authorized`
# today, and the shape section 9's EGA adapter turns the deposit above into.
_CONTROLLED_BUT_SUPPORTED = {
    "archive": "geo",
    "accession": "GSE000001",
    "exists": "yes",
    "access": "controlled",
    "supported": "yes",
    "raw_data": "yes",
    "preprocessed_data": "yes",
}

_OPEN_GEO = {
    "archive": "geo",
    "accession": "GSE309060",
    "exists": "yes",
    "access": "open",
    "supported": "yes",
    "raw_data": "yes",
    "preprocessed_data": "yes",
}


def _caps(deposits, **answers):
    caps = {"deposits": deposits}
    caps.update({k: {"value": v} for k, v in answers.items()})
    return caps


class TestTheSixActions:
    def test_every_action_is_declared(self):
        assert set(ACTIONS) == {PROCEED, UNDETERMINED, CONTESTED, NO_ADAPTER, NO_INPUT, NOT_AUTHORIZED}

    def test_an_acquirable_deposit_proceeds(self):
        decision = decide_route(route="deposit", capabilities=_caps([_OPEN_GEO], preprocessed_data="yes"))
        assert decision.action == PROCEED
        assert decision.authorizes_execution is True

    def test_a_discovery_failure_is_undetermined_and_still_authorizes_the_attempt(self):
        """An unknown is a conclusion about evidence, not a licence to refuse. Bounded discovery
        follows; section 3 bounds it."""
        decision = decide_route(route="deposit", capabilities=_caps([], preprocessed_data="unknown"))
        assert decision.action == UNDETERMINED
        assert decision.authorizes_execution is True

    def test_an_established_absence_is_no_input(self):
        decision = decide_route(route="deposit", capabilities=_caps([_EGA_DEPOSIT], preprocessed_data="no"))
        assert decision.action == NO_INPUT
        assert decision.authorizes_execution is False

    def test_an_archive_bioaf_cannot_read_is_no_adapter(self):
        decision = decide_route(route="pipeline", capabilities=_caps([_EGA_DEPOSIT], raw_data="yes"))
        assert decision.action == NO_ADAPTER

    def test_a_supported_archive_holding_controlled_data_is_not_authorized(self):
        decision = decide_route(route="pipeline", capabilities=_caps([_CONTROLLED_BUT_SUPPORTED], raw_data="yes"))
        assert decision.action == NOT_AUTHORIZED

    def test_a_disputed_scientific_judgment_is_contested_and_overridable(self):
        decision = decide_route(
            route="deposit",
            capabilities=_caps([_OPEN_GEO], preprocessed_data="yes"),
            conflict={"message": "the deposit declares Bisulfite-Seq"},
        )
        assert decision.action == CONTESTED
        assert decision.overridable is True
        assert decision.authorizes_execution is False

    def test_an_override_answers_the_contest(self):
        decision = decide_route(
            route="deposit",
            capabilities=_caps([_OPEN_GEO], preprocessed_data="yes"),
            conflict={"message": "the deposit declares Bisulfite-Seq"},
            deposit_override=True,
        )
        assert decision.action == PROCEED

    def test_a_species_mismatch_is_contested_too(self):
        decision = decide_route(
            route="deposit",
            capabilities=_caps([_OPEN_GEO], preprocessed_data="yes"),
            species_hold="the plan names Mus musculus and the deposit declares Homo sapiens",
        )
        assert decision.action == CONTESTED

    def test_a_species_override_answers_it(self):
        decision = decide_route(
            route="deposit",
            capabilities=_caps([_OPEN_GEO], preprocessed_data="yes"),
            species_hold="the plan names Mus musculus",
            species_override=True,
        )
        assert decision.action == PROCEED

    @pytest.mark.parametrize("action", [NO_ADAPTER, NO_INPUT, NOT_AUTHORIZED])
    def test_a_terminal_refusal_can_never_be_overridden(self, action):
        """Owner decision 1: no adapter is a hard refusal, and the other two are facts about the
        resource and the organization that an override cannot change either."""
        from app.services.validation_route_policy import OVERRIDABLE_ACTIONS

        assert action not in OVERRIDABLE_ACTIONS


class TestTheReasonsStayApart:
    def test_a_missing_adapter_is_never_reported_as_the_paper_missing_something(self):
        decision = decide_route(route="pipeline", capabilities=_caps([_EGA_DEPOSIT], raw_data="yes"))
        reason = decision.reason.lower()
        assert "bioaf" in reason
        assert "no raw sequencing reads" not in reason
        assert "published" in reason  # the paper DID publish them

    def test_a_missing_adapter_names_the_archive_and_the_accession(self):
        decision = decide_route(route="pipeline", capabilities=_caps([_EGA_DEPOSIT], raw_data="yes"))
        assert "EGA" in decision.reason
        assert "EGAS00001003667" in decision.reason

    def test_an_authorization_failure_is_never_reported_as_a_missing_feature(self):
        decision = decide_route(route="pipeline", capabilities=_caps([_CONTROLLED_BUT_SUPPORTED], raw_data="yes"))
        reason = decision.reason.lower()
        assert "cannot acquire" not in reason
        assert "no adapter" not in reason
        assert "authoris" in reason or "authoriz" in reason

    def test_an_authorization_failure_says_what_would_unblock_it(self):
        decision = decide_route(route="pipeline", capabilities=_caps([_CONTROLLED_BUT_SUPPORTED], raw_data="yes"))
        assert "GSE000001" in decision.reason

    def test_the_three_terminal_reasons_are_distinguishable(self):
        wordings = {
            decide_route(route="pipeline", capabilities=_caps([_EGA_DEPOSIT], raw_data="yes")).reason,
            decide_route(route="deposit", capabilities=_caps([_EGA_DEPOSIT], preprocessed_data="no")).reason,
            decide_route(
                route="pipeline", capabilities=_caps([_CONTROLLED_BUT_SUPPORTED], raw_data="yes")
            ).reason,
        }
        assert len(wordings) == 3

    def test_a_missing_input_is_scoped_to_the_deposits_not_to_the_paper(self):
        decision = decide_route(route="deposit", capabilities=_caps([_EGA_DEPOSIT], preprocessed_data="no"))
        assert "for this paper" not in decision.reason
        assert "EGAS00001003667" in decision.reason

    def test_no_refusal_invents_an_archive_the_paper_never_used(self):
        for route in ("deposit", "pipeline"):
            decision = decide_route(
                route=route,
                capabilities=_caps([_EGA_DEPOSIT], preprocessed_data="no", raw_data="yes"),
            )
            assert "GEO" not in decision.reason


class TestBothLegs:
    def test_both_refuses_when_either_leg_cannot_run(self):
        decision = decide_route(
            route="both", capabilities=_caps([_EGA_DEPOSIT], raw_data="yes", preprocessed_data="no")
        )
        assert decision.authorizes_execution is False

    def test_both_reports_a_finding_for_each_leg(self):
        decision = decide_route(
            route="both", capabilities=_caps([_EGA_DEPOSIT], raw_data="yes", preprocessed_data="no")
        )
        assert {f.leg for f in decision.findings} == {"deposit", "pipeline"}
        assert {f.action for f in decision.findings} == {NO_ADAPTER, NO_INPUT}

    def test_both_proceeds_only_when_both_legs_can(self):
        decision = decide_route(
            route="both", capabilities=_caps([_OPEN_GEO], raw_data="yes", preprocessed_data="yes")
        )
        assert decision.action == PROCEED


class TestTheAxesStayIndependent:
    def test_support_is_a_fact_about_the_registry_not_about_the_organization(self):
        """`supported` answers only "does bioAF have an adapter". An organization with no
        credentials must not make a supported archive look unsupported."""
        decision = decide_route(route="pipeline", capabilities=_caps([_CONTROLLED_BUT_SUPPORTED], raw_data="yes"))
        assert decision.action != NO_ADAPTER

    def test_an_adapter_gap_outranks_an_authorization_gap(self):
        """Both are true of an EGA controlled dataset today. The adapter is the axis bioAF owns, and
        telling a lab to negotiate data access for a capability gap sends it to the wrong remedy."""
        decision = decide_route(route="pipeline", capabilities=_caps([_EGA_DEPOSIT], raw_data="yes"))
        assert decision.action == NO_ADAPTER

    def test_a_failure_to_look_is_not_an_absence(self):
        decision = decide_route(route="pipeline", capabilities=_caps([], raw_data="unknown"))
        assert decision.action != NO_INPUT
        assert decision.action == UNDETERMINED

    def test_an_undetermined_leg_records_that_nothing_was_established(self):
        decision = decide_route(
            route="deposit",
            capabilities={"deposits": [], "preprocessed_data": {"value": "unknown", "failure_reason": "GEO timed out"}},
        )
        assert "GEO timed out" in decision.reason
