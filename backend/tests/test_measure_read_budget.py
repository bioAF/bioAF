"""plan_8_1 section 1.1: the command that measures a read's two calls and writes their records.

It runs where the organization's model is configured (on the demo, inside the backend container), reads
each paper once through the same prompts a read uses, and records each call's output tokens, stop reason
and elapsed time. The budget is set with recorded headroom above the largest complete answer, within the
model's documented maximum. A cut-off answer is not a measurement of a complete read, so it sets nothing.
"""

import pytest

from app.cli import measure_read_budget as measure
from app.services import validation_read_budget as budget
from app.services.llm_provider_clients import ModelAnswer, ProviderError


def _row(paper, output_tokens, elapsed=100.0, stop_reason="end_turn"):
    return {"paper": paper, "output_tokens": output_tokens, "elapsed_seconds": elapsed, "stop_reason": stop_reason}


class TestTheRecord:
    def test_the_budget_has_headroom_above_the_largest_answer(self):
        record = measure.record_from(
            budget.EXTRACTION,
            [_row("a", 9000), _row("b", 14000, elapsed=200.0), _row("c", 6000)],
            model="claude-opus-4-8",
            fingerprint="f",
            date="2026-09-13",
        )
        assert record["chosen_budget"] == 21000
        assert record["fingerprint"] == "f"
        assert record["status"] == "measured"
        assert record["date"] == "2026-09-13"
        assert "14000" in record["headroom"]
        model = record["models"]["claude-opus-4-8"]
        assert [p["paper"] for p in model["papers"]] == ["a", "b", "c"]
        # The slowest measured rate, which caps a recovery budget by the call's deadline.
        assert model["tokens_per_second"] == 60.0

    def test_the_budget_never_falls_below_the_starting_budget(self):
        record = measure.record_from(
            budget.INVENTORY, [_row("a", 2000), _row("b", 3000), _row("c", 1000)], model="m", fingerprint="f", date="d"
        )
        assert record["chosen_budget"] == 16000

    def test_the_budget_never_exceeds_the_models_maximum(self):
        record = measure.record_from(
            budget.EXTRACTION, [_row("a", 15000)], model="gpt-4o", fingerprint="f", date="d"
        )
        assert record["chosen_budget"] == budget.model_output_limit("gpt-4o")

    def test_a_cut_off_answer_sets_nothing(self):
        with pytest.raises(ValueError, match="cut off"):
            measure.record_from(
                budget.EXTRACTION,
                [_row("a", 16000, stop_reason="max_tokens")],
                model="m",
                fingerprint="f",
                date="d",
            )


_EXTRACTION = (
    '```json\n{"accessions": ["GSE1"], "method": {"assay": "bulk RNA-seq"}, '
    '"claims": [{"claim_text": "genes up", "metric_key": "de_genes"}], '
    '"reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0]}]}\n```'
)
_INVENTORY = (
    '```json\n{"findings": [{"description": "d", "claim_indices": [0], "importance": "primary", '
    '"rationale": "r", "quote": "q"}]}\n```'
)


class _Client:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        self.calls.append({"system": prompt, "payload": payload, "max_tokens": max_tokens})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _Cfg:
    provider = "anthropic"
    model = "claude-opus-4-8"
    api_key = "k"


class TestMeasuringAPaper:
    @pytest.mark.asyncio
    async def test_both_calls_are_measured_through_the_prompts_a_read_uses(self):
        client = _Client(
            [
                ModelAnswer(_EXTRACTION, output_tokens=9100, stop_reason="end_turn"),
                ModelAnswer(_INVENTORY, output_tokens=800, stop_reason="end_turn"),
            ]
        )
        rows = await measure.measure_paper("10.1/x", "the paper text", client=client, cfg=_Cfg(), max_tokens=32000)
        assert rows[budget.EXTRACTION]["output_tokens"] == 9100
        assert rows[budget.INVENTORY]["output_tokens"] == 800
        assert rows[budget.EXTRACTION]["paper"] == "10.1/x"
        assert rows[budget.EXTRACTION]["claims"] == 1
        extraction_call, inventory_call = client.calls
        assert budget.fingerprint(extraction_call["system"]) == budget.extraction_fingerprint()
        assert budget.fingerprint(inventory_call["system"]) == budget.inventory_fingerprint()
        assert "[0] genes up" in inventory_call["payload"]
        assert extraction_call["max_tokens"] == inventory_call["max_tokens"] == 32000

    @pytest.mark.asyncio
    async def test_a_cut_off_extraction_is_reported_and_the_inventory_is_not_measured(self):
        client = _Client(
            [ProviderError("cut off", error_class="truncated", text="{", output_tokens=32000, stop_reason="max_tokens")]
        )
        rows = await measure.measure_paper("10.1/x", "text", client=client, cfg=_Cfg(), max_tokens=32000)
        assert rows[budget.EXTRACTION]["stop_reason"] == "max_tokens"
        assert rows[budget.EXTRACTION]["output_tokens"] == 32000
        assert budget.INVENTORY not in rows
        assert len(client.calls) == 1
