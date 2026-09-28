"""Two defects the live reassessment of study 65 found, which every fixture had missed.

Recorded from the run on the demo, 2026-09-28, through `run_assessment` on a real paper:

    asked: {"requests": 59, "expansions": 14, "reconciliation_requests": 0}
    reconciliation: {"status": "not_performed", "examined": 0,
                     "unreconciled": [["C5.A", "C5.B", "E1.A#exp:e1", ... 30 leaves ...]]}
    followup: {"reason": "bioAF holds source for this paper and could attribute none of it to an
               implementation", "followups": []}

1. **The reconciliation group was the transitive closure of the whole report.** Findings on one paper
   share citations, and sharing is transitive, so thirty leaves collapsed into one group. `MAX_GROUPS`
   bounded the number of groups and nothing bounded the size of one, so the request asked about 435
   pairs and could not be answered at all. plan_8_7 section 6 says to avoid an unbounded all-pairs
   review, and this was one.

2. **The code follow-up found no implementations on a paper with nine source files.** Study 65 reports
   three experiments, so `analysis_units` returned three EXPERIMENT definitions, and the fallback that
   builds implementation rows only fired when the definitions were empty. Every definition was skipped
   for not being an implementation, and a paper with nine published scripts reported none.
"""

import pytest

from app.services.validation_code_followup import code_followup
from app.services.validation_finding_overlap import candidate_pairs
from app.services.validation_rubric_v3 import FAILED, VERIFIED
from app.services.validation_finding_overlap import MAX_PAIRS
from app.services.validation_semantic_reconciliation import MAX_PAIRS_PER_REQUEST, reconcile_semantically
from tests.support.fake_llm import FakeClient


def _judgment(outcome, leaf, citations):
    return {
        "outcome": outcome,
        "rationale": f"a finding about {leaf}",
        "scope": f"the analysis {leaf} is about",
        "finding_scope": f"the analysis {leaf} is about" if outcome == FAILED else None,
        "impact": "x" if outcome == FAILED else None,
        "method": "model_assisted",
        "evidence": {"citations": list(citations)},
    }


def _report(count, shared="p61"):
    """What a real paper looks like: many findings, most of them citing one methods paragraph."""
    return {
        f"X{i}.A": _judgment(FAILED if i % 3 == 0 else VERIFIED, f"X{i}.A", [shared, f"p{i}"])
        for i in range(1, count + 1)
    }


class _Answering:
    """A provider that answers about the pairs it was actually asked about, as a real one would."""

    def __init__(self):
        self.asked: list[dict] = []

    async def submit(self, *, prompt, payload, model=None, api_key=None, max_tokens=None, **kw):
        import json

        self.asked.append({"payload": payload, "system": prompt})
        named = payload.partition("Pairs to answer about: ")[2].partition(".\n")[0].rstrip(".")
        pairs = [part.split(" with ") for part in named.split(", ") if " with " in part]
        return (
            "```json\n"
            + json.dumps(
                {"pairs": [{"a": a, "b": b, "relation": "compatible", "reason": "different facts"} for a, b in pairs]}
            )
            + "\n```"
        )


class TestTheReconciliationIsBoundedByPairsNotByGroups:
    def test_thirty_related_findings_do_not_become_one_request(self):
        pairs = candidate_pairs(_report(30))
        assert len(pairs) <= MAX_PAIRS
        assert all(len(pair) == 2 for pair in pairs)

    def test_a_pair_is_two_findings_that_actually_share_evidence(self):
        pairs = candidate_pairs(_report(4))
        assert ("X1.A", "X2.A") in pairs

    def test_findings_sharing_nothing_are_not_a_pair(self):
        apart = {
            "A.A": {**_judgment(VERIFIED, "A.A", ["p1"]), "scope": "the bulk RNA-seq preprocessing"},
            "B.B": {
                **_judgment(FAILED, "B.B", ["p9"]),
                "scope": "the flow cytometry gating",
                "finding_scope": "the flow cytometry gating",
            },
        }
        assert candidate_pairs(apart) == []

    @pytest.mark.asyncio
    async def test_the_pass_actually_runs_on_a_real_sized_report(self):
        """The live failure: one request about 435 pairs, answered about none, status not_performed."""
        judgments = _report(30)
        client = _Answering()
        found = await reconcile_semantically(judgments=judgments, passages={}, client=client, model="m", api_key=None)
        assert found["status"] == "performed"
        assert found["examined"] >= 1
        assert found["unreconciled"] == []

    @pytest.mark.asyncio
    async def test_no_request_asks_about_more_pairs_than_it_can_answer(self):
        judgments = _report(30)
        client = FakeClient([{"pairs": [{"a": "X1.A", "b": "X2.A", "relation": "compatible", "reason": "x"}]}] * 40)
        await reconcile_semantically(judgments=judgments, passages={}, client=client, model="m", api_key=None)
        for asked in client.asked:
            named = asked["payload"].partition("Pairs to answer about: ")[2].partition(".")[0]
            assert named.count(" with ") <= MAX_PAIRS_PER_REQUEST, named

    @pytest.mark.asyncio
    async def test_an_unanswerable_request_leaves_only_its_own_pairs_unreconciled(self):
        judgments = _report(8)
        client = FakeClient([{"pairs": []}] * 40)
        found = await reconcile_semantically(judgments=judgments, passages={}, client=client, model="m", api_key=None)
        assert found["unreconciled"], "the pairs it could not settle are named"
        assert all(len(row) == 2 for row in found["unreconciled"])


class TestTheFollowUpFindsTheImplementationsBesideTheExperiments:
    def _evidence(self, scripts):
        return {
            "code_inspection": {
                "sources": [
                    {"path": f"s{i}.py", "language": "python", "text": f"import numpy\nprint({i})\n"}
                    for i in range(1, scripts + 1)
                ],
                "manifests": [{"path": "requirements.txt", "text": "numpy==1.26.4\n"}],
            },
            "code_resolution": {"outcome": "resolved", "url": "https://github.com/x/y", "commit_sha": "abc"},
            "capabilities": {"preprocessed_data": {"value": "yes"}},
        }

    _THREE_EXPERIMENTS = {
        "reported_experiments": [
            {"id": "e1", "assay": "bulk RNA-seq"},
            {"id": "e2", "assay": "scRNA-seq"},
            {"id": "e3", "assay": "barcode screen"},
        ]
    }

    def test_nine_scripts_beside_three_experiments_still_produce_follow_up(self):
        """The live case: three experiment units existed, so the implementation fallback never fired."""
        found = code_followup(evidence=self._evidence(9), plan=self._THREE_EXPERIMENTS, route="deposit")
        assert len(found["followups"]) == 9
        assert found["reason"] is None

    def test_each_script_is_its_own_row(self):
        found = code_followup(evidence=self._evidence(3), plan=self._THREE_EXPERIMENTS, route="deposit")
        assert sorted(row["unit"] for row in found["followups"]) == ["code:s1.py", "code:s2.py", "code:s3.py"]

    def test_two_scripts_that_became_units_are_used_rather_than_rebuilt(self):
        found = code_followup(evidence=self._evidence(2), plan={}, route="deposit")
        assert sorted(row["unit"] for row in found["followups"]) == ["code:s1.py", "code:s2.py"]
