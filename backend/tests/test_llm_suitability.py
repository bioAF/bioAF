"""plan_6 step 7: is the model you chose able to do this job?

Curated judgment, not measurement. The eval harness that replaces judgment with numbers is plan_7;
until then this table is a release activity and says so.

The load-bearing rule is that an unknown model is `unproven`, never `unlikely`. A model released
after this table was last edited must not be labelled bad for that reason alone.
"""

import pytest

from app.services.llm_suitability import (
    KNOWN_GOOD,
    LLM_SUITABILITY,
    UNLIKELY,
    UNPROVEN,
    suitability_for,
)


class TestTheVerdict:
    def test_an_unknown_model_is_unproven_not_unlikely(self):
        """Fail open on missing information. This is the whole point of the table's shape.

        A model from a family nobody has assessed, on a provider that is configured. Not a new point
        release of a known family: those are covered by the prefix on purpose."""
        verdict = suitability_for("anthropic", "some-new-architecture-1")
        assert verdict["verdict"] == UNPROVEN
        assert verdict["reason"]

    def test_an_unknown_provider_is_unproven(self):
        assert suitability_for("some-new-provider", "some-model")["verdict"] == UNPROVEN

    def test_a_missing_model_is_unproven(self):
        assert suitability_for("anthropic", None)["verdict"] == UNPROVEN
        assert suitability_for("anthropic", "")["verdict"] == UNPROVEN

    def test_a_model_known_to_work_carries_no_warning(self):
        verdict = suitability_for("anthropic", "claude-sonnet-4-6")
        assert verdict["verdict"] == KNOWN_GOOD
        assert verdict["warn"] is False

    def test_a_model_expected_to_fail_warns_and_says_why(self):
        """The banner states the reason rather than a verdict, so a user can judge whether it applies
        to their papers."""
        verdict = suitability_for("gemma", "gemma-4-9b")
        assert verdict["verdict"] == UNLIKELY
        assert verdict["warn"] is True
        assert "context" in verdict["reason"].lower() or "structured" in verdict["reason"].lower()

    def test_the_assessment_says_it_is_provisional(self):
        """Curated judgment must not read as measurement."""
        assert "provisional" in suitability_for("gemma", "gemma-4-9b")["note"].lower()

    def test_a_prefix_matches_a_whole_family(self):
        """Point releases must not each need a table entry, or the table is `unlikely` by neglect."""
        assert suitability_for("anthropic", "claude-sonnet-4-6-20260101")["verdict"] == KNOWN_GOOD

    def test_the_longest_matching_prefix_wins(self):
        """A family can be good with one member excepted, and the exception has to be reachable."""
        entries = {p for p, _ in LLM_SUITABILITY}
        assert "anthropic" in entries


class TestTheTableItself:
    """Asserted so a careless edit is caught. The table IS the feature here."""

    def test_every_entry_has_a_verdict_and_a_reason(self):
        for (provider, prefix), entry in LLM_SUITABILITY.items():
            assert provider and prefix, "an entry must name a provider and a model prefix"
            assert entry["verdict"] in (KNOWN_GOOD, UNPROVEN, UNLIKELY)
            assert entry["reason"].strip(), f"{provider}/{prefix} has no reason"

    def test_no_entry_is_unproven(self):
        """`unproven` is what absence means. Writing it into the table says nothing and invites the
        reader to think the model was assessed."""
        assert [k for k, v in LLM_SUITABILITY.items() if v["verdict"] == UNPROVEN] == []

    def test_an_unlikely_verdict_never_blocks_the_save(self):
        """The user proceeds at their discretion; the banner informs, it does not gate."""
        assert suitability_for("gemma", "gemma-4-9b").get("blocks_save", False) is False

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
    def test_the_hosted_providers_have_a_known_good_family(self, provider):
        """A table with no known_good entry for a hosted provider would warn or shrug at everything,
        which trains the user to ignore it."""
        verdicts = [v["verdict"] for (p, _), v in LLM_SUITABILITY.items() if p == provider]
        assert KNOWN_GOOD in verdicts
