"""plan_7 step 17: was this noise read as signal, and what else could explain the difference?

Two model calls, both AFTER an execution arm diverged, and both deliberately hedged.

**The tool never concludes that authors got it wrong.** `authors_misinterpreted` was REMOVED from
the outcome vocabulary. Divergence is always "we could not reproduce", and the possibility that the
paper read noise as signal is carried BESIDE the outcome as a flagged possible issue, with our
numbers shown next to theirs so the user makes their own judgement.

**The assessment's candidate set always includes bioAF's own side.** Their code, run by us, on data
we selected and mounted, with arguments a model chose, in an environment we built: any of those can
produce a different number, and ours is usually the cheapest to check. Study 26 is the proof, and
its own reasoning is the standard this has to meet.

Neither call fires on the pipeline route. That is a deliberate scope line: a pipeline-route
divergence like study 26's 7,389 vs 4,054 peaks still gets prose only, because only the execution
arms give us a like-for-like result to set against the paper's own.
"""

import json

import pytest

from app.services.signal_assessment import (
    LIKELY,
    NOT_LIKELY,
    assess_causes,
    assess_signal,
)


class _Client:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls = 0
        self.payloads: list[str] = []
        self.prompts: list[str] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.calls += 1
        self.payloads.append(payload)
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


def _fenced(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


async def _assess(**kw):
    return await assess_signal(
        method=kw.pop("method", "authors_code"),
        paper_value=kw.pop("paper_value", 7389),
        our_value=kw.pop("our_value", 4054),
        metric=kw.pop("metric", "peak_count"),
        context=kw.pop("context", "FRiP 0.008, NSC 1.02"),
        client=kw.pop(
            "client", _Client(_fenced({"verdict": "likely", "reason": "weak enrichment", "confidence": 0.7}))
        ),
        model=kw.pop("model", "claude-opus-4-8"),
        api_key=None,
        **kw,
    )


class TestWhenItFires:
    @pytest.mark.asyncio
    async def test_it_fires_after_an_execution_arm_diverged(self):
        result = await _assess()
        assert result is not None
        assert result["verdict"] == LIKELY

    @pytest.mark.asyncio
    async def test_it_does_not_fire_on_the_pipeline_route(self):
        """A deliberate scope line, not an oversight. Only the arms that ran code give a
        like-for-like result to set against the paper's own."""
        client = _Client(_fenced({"verdict": "likely", "reason": "x", "confidence": 0.9}))
        assert await _assess(method="deseq2", client=client) is None
        assert client.calls == 0

    @pytest.mark.asyncio
    async def test_it_does_not_fire_when_there_is_nothing_to_compare(self):
        client = _Client(_fenced({"verdict": "likely", "reason": "x", "confidence": 0.9}))
        assert await _assess(paper_value=None, client=client) is None
        assert await _assess(our_value=None, client=_Client()) is None

    @pytest.mark.asyncio
    async def test_it_fires_for_the_generated_arm_too(self):
        assert await _assess(method="llm_from_methods") is not None


class TestWhatItReturns:
    @pytest.mark.asyncio
    async def test_a_closed_set_with_a_reason_and_a_confidence(self):
        result = await _assess()
        assert result["verdict"] in (LIKELY, NOT_LIKELY)
        assert result["reason"]
        assert result["confidence"] == 0.7
        assert result["model"] == "claude-opus-4-8"
        assert result["assessed_at"]

    @pytest.mark.asyncio
    async def test_a_verdict_outside_the_set_is_refused_rather_than_coerced(self):
        client = _Client(_fenced({"verdict": "definitely wrong", "reason": "x", "confidence": 1.0}))
        assert await _assess(client=client) is None

    @pytest.mark.asyncio
    async def test_a_provider_failure_leaves_no_assessment(self):
        """No assessment is honest. An invented one would be an accusation nobody made."""
        from app.services.llm_provider_clients import ProviderError

        assert await _assess(client=_Client(error=ProviderError("down", error_class="server"))) is None

    @pytest.mark.asyncio
    async def test_both_numbers_are_put_in_front_of_the_model(self):
        client = _Client(_fenced({"verdict": "not likely", "reason": "x", "confidence": 0.4}))
        await _assess(client=client)
        assert "7389" in client.payloads[0]
        assert "4054" in client.payloads[0]

    @pytest.mark.asyncio
    async def test_the_prompt_forbids_concluding_the_authors_were_wrong(self):
        client = _Client(_fenced({"verdict": "not likely", "reason": "x", "confidence": 0.4}))
        await _assess(client=client)
        assert "hedge" in client.prompts[0].lower() or "not an accusation" in client.prompts[0].lower()


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
