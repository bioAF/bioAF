"""plan_7 step 17: what could explain the difference between the paper's result and ours?

One model call, AFTER an execution arm diverged, and deliberately hedged.

**The tool never concludes that authors got it wrong.** `authors_misinterpreted` was REMOVED from
the outcome vocabulary. Divergence is always "we could not reproduce".

**The assessment's candidate set always includes bioAF's own side.** Their code, run by us, on data
we selected and mounted, with arguments a model chose, in an environment we built: any of those can
produce a different number, and ours is usually the cheapest to check. Study 26 is the proof, and
its own reasoning is the standard this has to meet.

It does not fire on the pipeline route. That is a deliberate scope line: a pipeline-route divergence
like study 26's 7,389 vs 4,054 peaks still gets prose only, because only the execution arms give us a
like-for-like result to set against the paper's own.

**A second call stood here and plan_8_7 stage 4 removed it**, flagged rather than deleted quietly.
`assess_signal` asked whether the paper's result could be noise read as signal, which is a judgment
about whether the result supports what the paper concluded from it.
`validation_interpretation_review` owns that question now, asks it from the paper's own design rather
than from one pair of numbers, and does not wait for a divergence to exist before it can be asked at
all. Its tests are `test_plan_8_7_interpretation_review.py`; the consolidation itself is held in
`test_plan_8_7_consolidation.py`.
"""

import json

import pytest

from app.services.signal_assessment import assess_causes


class _Client:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls = 0
        self.payloads: list[str] = []
        self.prompts: list[str] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        self.calls += 1
        self.payloads.append(payload)
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


def _fenced(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


class TestTheCausalAssessment:
    """What could explain the observation, kept in its own key so it is never mistaken for the
    observation itself."""

    @pytest.mark.asyncio
    async def test_it_states_a_candidate_a_reason_and_a_confidence(self):
        client = _Client(
            _fenced(
                {
                    "candidate": "bioaf_input_mapping",
                    "reason": "we chose which deposited file to mount and how its columns map",
                    "confidence": 0.6,
                }
            )
        )
        result = await assess_causes(
            observation={"outcome": "ran_output_diverges", "paper_value": 7389, "our_value": 4054},
            method="authors_code",
            client=client,
            model="m",
            api_key=None,
        )
        assert result["candidate"] == "bioaf_input_mapping"
        assert result["reason"]
        assert result["confidence"] == 0.6
        assert result["model"] == "m"

    @pytest.mark.asyncio
    async def test_our_own_side_is_always_among_the_candidates_offered(self):
        """Study 26's lesson, encoded. Their code, run by us, on data we selected and mounted, with
        arguments a model chose, in an environment we built."""
        client = _Client(_fenced({"candidate": "bioaf_input_mapping", "reason": "x", "confidence": 0.5}))
        await assess_causes(
            observation={"outcome": "ran_output_diverges"},
            method="authors_code",
            client=client,
            model="m",
            api_key=None,
        )
        prompt = client.prompts[0]
        assert "bioaf_input_mapping" in prompt
        assert "bioaf_arguments" in prompt
        assert "bioaf_environment" in prompt

    @pytest.mark.asyncio
    async def test_a_candidate_outside_the_set_leaves_the_cause_unresolved(self):
        """Where the evidence does not reach a cause, the report says what was observed and lists
        the candidates, unresolved. It does not invent one."""
        client = _Client(_fenced({"candidate": "the authors are frauds", "reason": "x", "confidence": 1.0}))
        result = await assess_causes(
            observation={"outcome": "ran_output_diverges"},
            method="authors_code",
            client=client,
            model="m",
            api_key=None,
        )
        assert result["candidate"] is None
        assert result["reason"]

    @pytest.mark.asyncio
    async def test_an_established_outcome_is_not_sent_for_an_opinion(self):
        """`dependency_unresolvable` is established BY the transcript: the lockfile named a package
        that no longer resolves. Asking a model to speculate about it would put a guess beside a
        fact."""
        client = _Client(_fenced({"candidate": "code_defect", "reason": "x", "confidence": 0.9}))
        result = await assess_causes(
            observation={"outcome": "dependency_unresolvable"},
            method="authors_code",
            client=client,
            model="m",
            api_key=None,
        )
        assert result is None
        assert client.calls == 0
