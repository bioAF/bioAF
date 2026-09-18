"""plan_8_3 section 0.3: a check that refuses correct evidence is a defect of the same standing.

Stage 3's treatment check and stage 5's identity check both refused evidence the build before them
accepted, and both reached the demo. Nothing in the plan's own gates catches that, because every gate
it states is about a check that accepts too much.

So a new or narrowed check ships with its refusal diff: the superseded rule and the current one, run
over the saved evidence of every study bioAF holds, row by row. Every input whose outcome moved is
recorded, and any input that moved from accepted to REFUSED must carry a justification of its own.
The diff is offline and deterministic; a live run on one paper does not satisfy it.
"""

import pytest

from tests.refusal_diff import CONDITION_CHECK, REFUSING, condition_diff, load_baseline, moved

_DIFFS = {CONDITION_CHECK: condition_diff}


class TestEveryCheckedInputStillReachesTheRecordedOutcome:
    @pytest.mark.parametrize("check", sorted(_DIFFS))
    def test_the_current_rule_reproduces_the_committed_diff(self, check):
        """The baseline is recomputed, never trusted. A later change to the check lands here first."""
        recorded = {entry["input"]: entry for entry in load_baseline(check)["inputs"]}
        computed = {entry["input"]: entry for entry in _DIFFS[check]()}
        assert sorted(computed) == sorted(recorded)
        for name, entry in computed.items():
            assert entry["deployed"] == recorded[name]["deployed"], name
            assert entry["current"] == recorded[name]["current"], name


class TestNoInputMovesFromAcceptedToRefusedWithoutAJustification:
    @pytest.mark.parametrize("check", sorted(_DIFFS))
    def test_every_moved_outcome_is_justified_one_by_one(self, check):
        for entry in load_baseline(check)["inputs"]:
            if moved(entry):
                assert str(entry.get("justification") or "").strip(), entry["input"]

    @pytest.mark.parametrize("check", sorted(_DIFFS))
    def test_an_input_the_previous_build_accepted_is_not_newly_refused(self, check):
        """The one direction this diff exists to block: the deployed build accepted the input and the
        new rule refuses it. Allowed only with a written reason for that input."""
        for entry in load_baseline(check)["inputs"]:
            if entry["deployed"] not in REFUSING and entry["current"] in REFUSING:
                assert str(entry.get("justification") or "").strip(), entry["input"]


class TestTheSamd1MappingIsAcceptedOnItsAttributes:
    """Section 3.1's acceptance: eight rows, both arms, on the evidence the live run already had."""

    def test_all_eight_rows_agree_with_their_arms(self):
        rows = [e for e in condition_diff() if e["input"].startswith("study 56")]
        assert len(rows) == 8
        assert {e["current"] for e in rows} == {"compatible"}
        assert {e["attribute"] for e in rows} == {"genotype"}
        assert {e["deployed"] for e in rows} == {"contradicted"}

    def test_study_50s_rows_are_unchanged(self):
        """The paper the check was written for keeps the outcome it had."""
        rows = [e for e in condition_diff() if e["input"].startswith("study 50")]
        assert len(rows) == 6
        assert {(e["deployed"], e["current"]) for e in rows} == {("compatible", "compatible")}
