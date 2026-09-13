"""plan_8_1 section 1.1: a decision can carry an output budget, and says what the call used.

``decide`` passes a budget to the configured client only when the caller gives one, so every caller that
names none reaches its provider exactly as before. Every decision, answered or not, carries the output
tokens, the stop reason and the elapsed time the provider reported, so a read can put them on its
provenance.
"""

import json

import pytest

from app.services.llm_decision import OUTCOME_OK, OUTCOME_TIMED_OUT, OUTCOME_TRUNCATED, decide
from app.services.llm_provider_clients import ModelAnswer, ProviderError


class _LegacyClient:
    """A client whose ``submit`` predates the budget: it accepts no ``max_tokens``."""

    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.calls += 1
        return self.response


class _BudgetClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.budgets: list = []

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        self.budgets.append(max_tokens)
        if self.error is not None:
            raise self.error
        return self.response


async def _decide(client, **kw):
    return await decide(
        intent="reading the paper",
        system="read it",
        payload="the paper",
        client=client,
        model="claude-opus-4-8",
        api_key=None,
        **kw,
    )


_ANSWER = "```json\n" + json.dumps({"claims": []}) + "\n```"


class TestTheBudgetReachesTheClientOnlyWhenGiven:
    @pytest.mark.asyncio
    async def test_no_budget_calls_a_legacy_client_as_before(self):
        client = _LegacyClient(_ANSWER)
        decision = await _decide(client)
        assert decision.outcome == OUTCOME_OK
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_a_budget_is_passed_through(self):
        client = _BudgetClient(_ANSWER)
        decision = await _decide(client, max_tokens=16000)
        assert decision.ok
        assert client.budgets == [16000]
        assert decision.max_tokens == 16000


class TestADecisionSaysWhatTheCallUsed:
    @pytest.mark.asyncio
    async def test_an_answer_carries_its_output_tokens_stop_reason_and_elapsed_time(self):
        client = _BudgetClient(ModelAnswer(_ANSWER, output_tokens=5123, stop_reason="end_turn"))
        decision = await _decide(client, max_tokens=16000)
        assert (decision.output_tokens, decision.stop_reason) == (5123, "end_turn")
        assert decision.elapsed_seconds is not None and decision.elapsed_seconds >= 0

    @pytest.mark.asyncio
    async def test_a_plain_string_answer_reports_no_usage(self):
        decision = await _decide(_BudgetClient(_ANSWER))
        assert (decision.output_tokens, decision.stop_reason) == (None, None)

    @pytest.mark.asyncio
    async def test_a_truncated_answer_keeps_the_text_that_arrived_and_what_it_used(self):
        error = ProviderError(
            "cut off", error_class="truncated", text='{"claims": [', output_tokens=16000, stop_reason="max_tokens"
        )
        decision = await _decide(_BudgetClient(error=error), max_tokens=16000)
        assert decision.outcome == OUTCOME_TRUNCATED
        assert decision.text == '{"claims": ['
        assert (decision.output_tokens, decision.stop_reason, decision.max_tokens) == (16000, "max_tokens", 16000)

    @pytest.mark.asyncio
    async def test_a_timeout_is_its_own_outcome_and_is_named_as_a_timeout(self):
        error = ProviderError("did not finish within 900 seconds", error_class="timeout", text="{", output_tokens=800)
        decision = await _decide(_BudgetClient(error=error), max_tokens=32000)
        assert decision.outcome == OUTCOME_TIMED_OUT
        assert "time limit" in decision.reason
        assert "token limit" not in decision.reason
        assert decision.text == "{"
