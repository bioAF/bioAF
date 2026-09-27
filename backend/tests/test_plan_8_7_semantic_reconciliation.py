"""plan_8_7: related propositions are reconciled by reading them, not by comparing citation sets.

The owner agreed the correction on 2026-09-27:

    "Reconciliation examines meaning. The selected model assesses related propositions and evidence
    before publication. Citation and scope overlap identify review candidates; they do not decide
    contradiction or duplicate failures. Preserve compatible positives/negatives and distinct
    defects. Keep genuine unresolved conflicts visible."

The counterexample is M1.A verified for described processing beside M3.B failed for inappropriate
handling of repeated observations, on the same citation and the same named analysis. Both
propositions are true: a method can be fully described and still be the wrong method. The overlap
pass converted M1.A to undetermined.
"""

import pytest

from app.services.validation_finding_overlap import candidate_groups, reconcile_findings
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED
from app.services.validation_semantic_reconciliation import (
    COMPATIBLE,
    CONTRADICTION,
    DUPLICATE,
    RECONCILIATION_VERSION,
    reconcile_semantically,
)
from tests.support.fake_llm import FakeClient

_ANALYSIS = "the bulk RNA-seq differential expression of treated versus control"


def _judgment(outcome, rationale, *, scope, citations, **extra):
    return {
        "outcome": outcome,
        "rationale": rationale,
        "scope": scope,
        "finding_scope": scope if outcome == FAILED else None,
        "method": "model_assisted",
        "evidence": {"citations": citations},
        **extra,
    }


_COMPATIBLE_PAIR = {
    "M1.A": _judgment(
        VERIFIED, "the paper states the trimming, alignment and quantification steps", scope=_ANALYSIS, citations=["p1"]
    ),
    "M3.B": _judgment(
        FAILED,
        "the fitted model treats three harvests of one participant as independent observations",
        scope=_ANALYSIS,
        citations=["p1"],
        impact="the reported significance is overstated",
    ),
}

_DUPLICATE_PAIR = {
    "M1.B": _judgment(
        FAILED, "the deposited sample count disagrees with the methods", scope=_ANALYSIS, citations=["p1", "p2"]
    ),
    "M5.B": _judgment(FAILED, "the sample count in the methods is not the deposited one", scope=_ANALYSIS, citations=["p1"]),
}

_PASSAGES = {"p1": "Libraries were trimmed, aligned and quantified; every harvest entered one fitted model."}


def _answer(relation, **extra):
    return {
        "pairs": [{"a": "M1.A", "b": "M3.B", "relation": relation, "reason": "read from the passage", **extra}],
    }


class TestOverlapOnlyFindsCandidates:
    def test_it_no_longer_demotes_a_positive_that_shares_a_citation(self):
        found = reconcile_findings(dict(_COMPATIBLE_PAIR))
        assert found["M1.A"]["outcome"] == VERIFIED
        assert found["M3.B"]["outcome"] == FAILED

    def test_it_no_longer_merges_two_negatives_on_its_own(self):
        found = reconcile_findings(dict(_DUPLICATE_PAIR))
        assert found["M1.B"]["outcome"] == FAILED
        assert found["M5.B"]["outcome"] == FAILED

    def test_the_pair_is_offered_as_a_candidate_for_review(self):
        groups = candidate_groups(_COMPATIBLE_PAIR)
        assert [sorted(g) for g in groups] == [["M1.A", "M3.B"]]

    def test_findings_sharing_nothing_are_not_a_candidate(self):
        apart = {
            "M1.A": _judgment(VERIFIED, "stated", scope="the ATAC-seq peaks", citations=["p9"]),
            "M3.B": _judgment(FAILED, "wrong", scope=_ANALYSIS, citations=["p1"], impact="x"),
        }
        assert candidate_groups(apart) == []


@pytest.mark.asyncio
class TestTheModelDecidesTheRelation:
    async def test_compatible_propositions_both_stand(self):
        client = FakeClient([_answer(COMPATIBLE)])
        found = await reconcile_semantically(
            judgments=dict(_COMPATIBLE_PAIR), passages=_PASSAGES, client=client, model="m", api_key=None
        )
        assert found["judgments"]["M1.A"]["outcome"] == VERIFIED
        assert found["judgments"]["M3.B"]["outcome"] == FAILED
        assert found["status"] == "performed"

    async def test_a_compatible_pair_records_that_it_was_examined(self):
        client = FakeClient([_answer(COMPATIBLE)])
        found = await reconcile_semantically(
            judgments=dict(_COMPATIBLE_PAIR), passages=_PASSAGES, client=client, model="m", api_key=None
        )
        assert found["examined"] == 1
        assert found["reconciliation_version"] == RECONCILIATION_VERSION

    async def test_one_demonstrated_failure_stated_twice_deducts_once(self):
        client = FakeClient([{"pairs": [{"a": "M1.B", "b": "M5.B", "relation": DUPLICATE, "reason": "same mismatch"}]}])
        found = await reconcile_semantically(
            judgments=dict(_DUPLICATE_PAIR), passages=_PASSAGES, client=client, model="m", api_key=None
        )
        outcomes = {leaf: j["outcome"] for leaf, j in found["judgments"].items()}
        assert sorted(outcomes.values()) == [FAILED, UNDETERMINED]
        demoted = next(j for j in found["judgments"].values() if j["outcome"] == UNDETERMINED)
        assert demoted["same_finding_as"] in ("M1.B", "M5.B")
        assert demoted["withheld"]["outcome"] == FAILED

    async def test_a_genuine_contradiction_leaves_the_affected_proposition_unresolved(self):
        client = FakeClient([_answer(CONTRADICTION, resolve="M1.A")])
        found = await reconcile_semantically(
            judgments=dict(_COMPATIBLE_PAIR), passages=_PASSAGES, client=client, model="m", api_key=None
        )
        assert found["judgments"]["M1.A"]["outcome"] == UNDETERMINED
        assert found["judgments"]["M1.A"]["contradicted_by"] == "M3.B"
        assert found["judgments"]["M1.A"]["withheld"]["outcome"] == VERIFIED
        assert found["judgments"]["M3.B"]["outcome"] == FAILED

    async def test_a_contradiction_it_cannot_resolve_stays_visible_without_demoting_anything(self):
        client = FakeClient([_answer(CONTRADICTION)])
        found = await reconcile_semantically(
            judgments=dict(_COMPATIBLE_PAIR), passages=_PASSAGES, client=client, model="m", api_key=None
        )
        assert found["judgments"]["M1.A"]["outcome"] == VERIFIED
        assert found["judgments"]["M1.A"]["tension_with"] == "M3.B"
        assert found["unresolved"]


@pytest.mark.asyncio
class TestFailureCannotCertifyConsistency:
    async def test_no_model_leaves_every_judgment_alone_and_says_the_pass_did_not_run(self):
        found = await reconcile_semantically(
            judgments=dict(_COMPATIBLE_PAIR), passages=_PASSAGES, client=None, model="", api_key=None
        )
        assert found["judgments"] == _COMPATIBLE_PAIR
        assert found["status"] == "not_performed"
        assert found["reason"]

    async def test_an_unusable_answer_leaves_the_candidates_unreconciled(self):
        client = FakeClient([{"pairs": [{"a": "M1.A", "b": "ZZ.Q", "relation": COMPATIBLE, "reason": "x"}]}])
        found = await reconcile_semantically(
            judgments=dict(_COMPATIBLE_PAIR), passages=_PASSAGES, client=client, model="m", api_key=None
        )
        assert found["judgments"]["M1.A"]["outcome"] == VERIFIED
        assert found["unreconciled"] == [["M1.A", "M3.B"]]

    async def test_nothing_related_costs_no_call(self):
        client = FakeClient([])
        found = await reconcile_semantically(
            judgments={"M1.A": _COMPATIBLE_PAIR["M1.A"]}, passages=_PASSAGES, client=client, model="m", api_key=None
        )
        assert found["status"] == "nothing_to_reconcile"
        assert client.calls == 0

    async def test_a_measured_outcome_is_never_rewritten(self):
        """plan_8_7 section 5: models cannot rewrite measured values."""
        measured = {
            "M1.A": {**_COMPATIBLE_PAIR["M1.A"], "method": "measurement"},
            "M3.B": _COMPATIBLE_PAIR["M3.B"],
        }
        client = FakeClient([_answer(CONTRADICTION, resolve="M1.A")])
        found = await reconcile_semantically(
            judgments=measured, passages=_PASSAGES, client=client, model="m", api_key=None
        )
        assert found["judgments"]["M1.A"]["outcome"] == VERIFIED
