"""plan_8_3 stage 6: a large claim set binds in batches, and no claim is silently dropped.

Study 50 asked one model call to bind 16 claims under the Anthropic client's 4,096-token default and
every binding failed after the answer was cut off. The budget (``validation_decision_budgets``) is the
first half of the repair; this is the second: claims travel in batches whose identifiers stay the same
in every batch, each batch's answer must hold exactly one decision per claim it was asked about, and a
batch that fails leaves the batches that succeeded intact.
"""

import json

import pytest

from app.services.validation_extraction_service import (
    BINDING_BATCH,
    BINDING_FAILED,
    bind_claims,
)


def _claims(n: int) -> list[dict]:
    return [
        {"metric_key": f"m{i}", "value": i, "unit": "genes", "claim_text": f"claim {i}", "source_locator": "Results"}
        for i in range(n)
    ]


def _answer(indexes, *, key="total_genes_detected", extra=()):
    rows = [{"claim_index": i, "bound_key": key, "reason": "it measures that", "confidence": 0.9} for i in indexes]
    rows.extend(extra)
    return "```json\n" + json.dumps({"bindings": rows}) + "\n```"


class _Client:
    """Answers each call from a script keyed by the claim indexes the payload names."""

    def __init__(self, reply):
        self.reply = reply
        self.payloads: list[str] = []
        self.systems: list[str] = []
        self.budgets: list = []

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        self.systems.append(prompt)
        self.payloads.append(payload)
        self.budgets.append(max_tokens)
        answer = self.reply(payload, len(self.payloads))
        if isinstance(answer, Exception):
            raise answer
        return answer


async def _bind(client, claims):
    return await bind_claims(claims, client=client, model="claude-opus-4-8", api_key=None)


def _indexes_named(payload: str) -> list[int]:
    return [int(line.split("]")[0][1:]) for line in payload.splitlines() if line.startswith("[")]


class TestBatching:
    @pytest.mark.asyncio
    async def test_a_claim_set_over_the_batch_size_is_asked_in_several_calls(self):
        claims = _claims(BINDING_BATCH + 3)
        client = _Client(lambda payload, n: _answer(_indexes_named(payload)))
        decisions = await _bind(client, claims)
        assert len(client.payloads) == 2
        assert len(decisions) == len(claims)
        assert [d["claim_index"] for d in decisions] == list(range(len(claims)))

    @pytest.mark.asyncio
    async def test_a_claims_identifier_is_its_place_in_the_paper_not_its_place_in_the_batch(self):
        claims = _claims(BINDING_BATCH + 3)
        client = _Client(lambda payload, n: _answer(_indexes_named(payload)))
        await _bind(client, claims)
        assert _indexes_named(client.payloads[0]) == list(range(BINDING_BATCH))
        assert _indexes_named(client.payloads[1]) == list(range(BINDING_BATCH, BINDING_BATCH + 3))

    @pytest.mark.asyncio
    async def test_every_batch_is_asked_the_same_question(self):
        client = _Client(lambda payload, n: _answer(_indexes_named(payload)))
        await _bind(client, _claims(BINDING_BATCH + 3))
        assert client.systems[0] == client.systems[1]

    @pytest.mark.asyncio
    async def test_a_set_that_fits_is_asked_once(self):
        client = _Client(lambda payload, n: _answer(_indexes_named(payload)))
        await _bind(client, _claims(3))
        assert len(client.payloads) == 1

    @pytest.mark.asyncio
    async def test_each_batch_runs_under_the_registered_budget(self):
        from app.services import validation_decision_budgets as budgets

        client = _Client(lambda payload, n: _answer(_indexes_named(payload)))
        await _bind(client, _claims(3))
        assert client.budgets == [budgets.DECISIONS[budgets.CLAIM_BINDING].max_tokens]


class TestExactlyOneDecisionPerClaim:
    @pytest.mark.asyncio
    async def test_a_batch_that_omits_a_claim_is_asked_again_naming_it(self):
        def reply(payload, n):
            asked = _indexes_named(payload)
            return _answer(asked if n > 1 else asked[:-1])

        client = _Client(reply)
        decisions = await _bind(client, _claims(3))
        assert len(client.payloads) == 2
        assert "2" in client.payloads[1].split("correcting exactly this:")[1]
        assert all(d["bound_key"] for d in decisions)

    @pytest.mark.asyncio
    async def test_a_claim_still_missing_after_the_second_ask_is_recorded_undecided_not_dropped(self):
        """The decisions that DID arrive stand: a model that answers fifteen of sixteen claims has
        bound fifteen, and the sixteenth is on the record as undecided rather than quietly absent."""
        claims = _claims(BINDING_BATCH + 3)

        def reply(payload, n):
            asked = _indexes_named(payload)
            return _answer(asked[:-1] if asked[0] == 0 else asked)

        client = _Client(reply)
        decisions = await _bind(client, claims)
        assert len(decisions) == len(claims)
        assert all(d["bound_key"] for d in decisions[: BINDING_BATCH - 1])
        undecided = decisions[BINDING_BATCH - 1]
        assert undecided["bound_key"] is None
        assert "no binding decision" in undecided["reason"]
        assert all(d["bound_key"] for d in decisions[BINDING_BATCH:])

    @pytest.mark.asyncio
    async def test_a_claim_answered_twice_after_the_second_ask_is_failed_not_resolved_by_order(self):
        def reply(payload, n):
            asked = _indexes_named(payload)
            return _answer(asked, extra=[{"claim_index": 0, "bound_key": "peak_count", "reason": "also"}])

        client = _Client(reply)
        decisions = await _bind(client, _claims(3))
        assert len(client.payloads) == 2
        assert decisions[0]["bound_key"] is None
        assert decisions[0]["bound_by"] == BINDING_FAILED
        assert [d["bound_key"] for d in decisions[1:]] == ["total_genes_detected", "total_genes_detected"]

    @pytest.mark.asyncio
    async def test_a_second_decision_for_one_claim_is_refused_rather_than_silently_taking_the_first(self):
        def reply(payload, n):
            asked = _indexes_named(payload)
            if n > 1:
                return _answer(asked)
            return _answer(asked, extra=[{"claim_index": 0, "bound_key": "peak_count", "reason": "also"}])

        client = _Client(reply)
        decisions = await _bind(client, _claims(3))
        assert len(client.payloads) == 2
        assert all(d["bound_key"] == "total_genes_detected" for d in decisions)

    @pytest.mark.asyncio
    async def test_a_decision_for_a_claim_the_batch_never_asked_about_is_refused(self):
        def reply(payload, n):
            asked = _indexes_named(payload)
            if n > 1:
                return _answer(asked)
            return _answer(asked, extra=[{"claim_index": 99, "bound_key": "peak_count", "reason": "stray"}])

        client = _Client(reply)
        await _bind(client, _claims(3))
        assert len(client.payloads) == 2
        assert "99" in client.payloads[1].split("correcting exactly this:")[1]


class TestOneBatchsFailureDoesNotCostTheOthers:
    @pytest.mark.asyncio
    async def test_an_unreachable_batch_leaves_the_other_batchs_bindings_standing(self):
        from app.services.llm_provider_clients import ProviderError

        claims = _claims(BINDING_BATCH + 3)

        def reply(payload, n):
            if _indexes_named(payload)[0] == 0:
                return ProviderError("down", error_class="transport")
            return _answer(_indexes_named(payload))

        client = _Client(reply)
        decisions = await _bind(client, claims)
        assert all(d.get("bound_by") == BINDING_FAILED for d in decisions[:BINDING_BATCH])
        assert all(d["bound_key"] for d in decisions[BINDING_BATCH:])

    @pytest.mark.asyncio
    async def test_a_cut_off_batch_is_asked_again_with_a_larger_budget_and_never_read_as_complete(self):
        from app.services.llm_provider_clients import ProviderError

        def reply(payload, n):
            if n == 1:
                error = ProviderError("cut off", error_class="truncated")
                error.text = '{"bindings": [{"claim_index": 0,'
                error.output_tokens = 16000
                return error
            return _answer(_indexes_named(payload))

        client = _Client(reply)
        decisions = await _bind(client, _claims(3))
        assert client.budgets[1] > client.budgets[0]
        assert all(d["bound_key"] for d in decisions)
