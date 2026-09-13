"""plan_8_1 section 1.1: the paper read's output budget is measured, and a stale measurement is caught.

The extraction's budget comes from a measurement record kept with the application: the prompt and schema
fingerprint it was measured under, the model, each paper's output tokens and elapsed time, the chosen
budget and the date. A test pins the fingerprint of the current prompt and schema to that record, so any
change to either, even one of equal length, fails here until someone measures again.

The guard detects a stale measurement. It cannot guarantee that every paper fits; bounded recovery
(section 1.2) covers the rest.
"""

import pytest

from app.services import validation_read_budget as budget
from app.services.validation_extraction_service import build_extraction_prompt


def _record() -> dict:
    return budget.load_record(budget.EXTRACTION)


class TestTheGuard:
    def test_the_record_was_measured_under_the_current_prompt_and_schema(self):
        """Fails whenever the extraction's system prompt or schema changes. Re-measure (the script
        `backend/scripts/measure_read_budget.py`, run where the organisation's model is configured),
        then update `app/services/read_measurements/extraction.json`."""
        assert _record()["fingerprint"] == budget.extraction_fingerprint()

    def test_an_equal_length_change_to_the_prompt_changes_the_fingerprint(self):
        system, _ = build_extraction_prompt("")
        swapped = system.replace("claims", "claimz", 1)
        assert len(swapped) == len(system)
        assert budget.fingerprint(swapped) != budget.fingerprint(system)

    def test_the_fingerprint_covers_the_schema(self):
        system, _ = build_extraction_prompt("")
        assert budget.extraction_fingerprint() == budget.fingerprint(system)

    def test_the_record_names_its_budget_and_how_it_was_chosen(self):
        record = _record()
        assert isinstance(record["chosen_budget"], int) and record["chosen_budget"] >= 16000
        assert record["headroom"]
        assert "date" in record

    @pytest.mark.xfail(
        strict=True,
        reason="plan_8_1 section 1.1: the three-paper measurement runs on the demo against the organisation's "
        "configured model after this build deploys; remove this mark when the record holds it",
    )
    def test_the_record_holds_three_measured_papers_within_the_budget(self):
        record = _record()
        measured = [m for model in record["models"].values() for m in model["papers"]]
        assert len({m["paper"] for m in measured}) >= 3
        assert all(m["output_tokens"] < record["chosen_budget"] for m in measured)


class TestTheBudgetForAModel:
    def test_a_measured_model_gets_the_chosen_budget_and_no_note(self, monkeypatch):
        monkeypatch.setattr(
            budget,
            "load_record",
            lambda call: {
                "fingerprint": "f",
                "chosen_budget": 20000,
                "models": {"claude-opus-4-8": {"papers": [], "tokens_per_second": 60.0}},
            },
        )
        chosen = budget.budget_for(budget.EXTRACTION, "claude-opus-4-8")
        assert chosen.max_tokens == 20000
        assert chosen.measured is True
        assert chosen.note is None

    def test_a_model_outside_the_record_still_reads_and_says_its_budget_was_not_measured(self, monkeypatch):
        monkeypatch.setattr(
            budget,
            "load_record",
            lambda call: {"fingerprint": "f", "chosen_budget": 20000, "models": {"claude-opus-4-8": {"papers": []}}},
        )
        chosen = budget.budget_for(budget.EXTRACTION, "claude-sonnet-5")
        assert chosen.max_tokens == 20000
        assert chosen.measured is False
        assert chosen.note == "budget not measured for this model"

    def test_the_budget_never_exceeds_the_models_maximum(self, monkeypatch):
        monkeypatch.setattr(
            budget, "load_record", lambda call: {"fingerprint": "f", "chosen_budget": 20000, "models": {}}
        )
        assert budget.budget_for(budget.EXTRACTION, "gpt-4o").max_tokens == 16384


class TestTheRecoveryBudget:
    def test_it_is_a_fixed_multiple(self):
        assert budget.recovery_budget(16000, model="claude-opus-4-8", provider="anthropic", record={}) == 32000

    def test_it_is_capped_by_the_models_maximum(self):
        assert budget.recovery_budget(100000, model="claude-opus-4-8", provider="anthropic", record={}) == 128000

    def test_it_is_capped_by_what_the_measured_rate_can_produce_before_the_deadline(self):
        record = {"models": {"claude-opus-4-8": {"tokens_per_second": 30.0}}}
        capped = budget.recovery_budget(16000, model="claude-opus-4-8", provider="anthropic", record=record)
        from app.services.llm_provider_clients import anthropic_client

        assert capped == int(30.0 * anthropic_client.STREAM_DEADLINE_SECONDS * budget.TIME_SAFETY)
        assert 16000 < capped < 32000

    def test_no_larger_budget_is_no_recovery(self):
        """A model already at its maximum cannot be given more room, so the read does not spend its
        second attempt on the same budget."""
        assert budget.recovery_budget(128000, model="claude-opus-4-8", provider="anthropic", record={}) is None

    def test_an_unknown_model_is_capped_conservatively(self):
        assert budget.model_output_limit("some-future-model") == budget.DEFAULT_OUTPUT_LIMIT
