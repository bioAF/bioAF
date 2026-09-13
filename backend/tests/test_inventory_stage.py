"""plan_8_1 section 2.2: the finding inventory is its own call, over the committed claims.

The extraction no longer asks for findings. One call proposes the inventory from the paper's text and the
committed claims (with their indices, experiments and contrasts), before anything is measured, under its
own measured output budget and its own prompt fingerprint. Validation is unchanged (plan_8's rubric).

One bounded retry in total: at most two submissions per inventory cycle, shared across truncation (a
larger budget) and a rejected proposal (the same budget, showing each problem and failed quote). A second
failure of any kind ends the cycle with its cause, and the committed claims are untouched.
"""

import json
from types import SimpleNamespace

import pytest

from app.services import validation_read_budget as read_budget
from app.services.llm_provider_clients import ModelAnswer, ProviderError
from app.services.validation_extraction_service import build_extraction_prompt
from app.services.validation_inventory_stage import (
    INVENTORY_INTENT,
    build_inventory_prompt,
    inventory_fingerprint,
    run_inventory_stage,
)
from app.services.validation_read_cycle import begin_cycle, current_cycle, start_attempt

_TEXT = "The knockout changes hundreds of genes. This is our main result. A smaller shift extends it. Depth was high."
_TARGETS = [
    {"claim_text": "genes up", "reported_experiment_id": "e1", "contrast_index": 0, "source_locator": "Fig. 2"},
    {"claim_text": "genes down", "reported_experiment_id": "e1", "contrast_index": 0, "source_locator": "Fig. 2"},
    {"claim_text": "a smaller shift", "reported_experiment_id": "e1", "contrast_index": 1, "source_locator": "Fig. 3"},
    {"claim_text": "reads per sample", "reported_experiment_id": "e1", "source_locator": "Methods"},
]
_EXPERIMENTS = [{"id": "e1", "assay": "bulk RNA-seq", "description": "RNA-seq of knockout and wild type"}]
_CONTRASTS = [{"name": "KO vs WT"}, {"name": "KO vs WT, day 7"}]
_CFG = SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)


def _findings(**changes):
    findings = [
        {
            "description": "The knockout changes hundreds of genes",
            "claim_indices": [0, 1],
            "importance": "primary",
            "rationale": "It is the main result.",
            "quote": "This is our main result.",
        },
        {
            "description": "A smaller shift later",
            "claim_indices": [2],
            "importance": "supporting",
            "rationale": "It extends the main result.",
            "quote": "A smaller shift extends it.",
        },
        {
            "description": "Sequencing depth",
            "claim_indices": [3],
            "importance": "technical",
            "rationale": "It enables the analysis.",
        },
    ]
    for index, fields in changes.items():
        findings[int(index[1:])].update(fields)
    return findings


def _answer(findings) -> str:
    return "```json\n" + json.dumps({"findings": findings}) + "\n```"


_TRUNCATED = ProviderError("cut off", error_class="truncated", text='{"findings": [', output_tokens=16000)


class _Client:
    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        self.calls.append({"prompt": prompt, "payload": payload, "max_tokens": max_tokens})
        step = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture(autouse=True)
def _measured(monkeypatch):
    records = {
        read_budget.EXTRACTION: {"fingerprint": "e", "chosen_budget": 16000, "models": {"claude-opus-4-8": {}}},
        read_budget.INVENTORY: {"fingerprint": "i", "chosen_budget": 8000, "models": {"claude-opus-4-8": {}}},
    }
    monkeypatch.setattr(read_budget, "load_record", lambda call: records[call])


async def _run(client, *, study=None, log=None):
    study = study or SimpleNamespace(id=1, evidence_json={})

    async def checkpoint():
        if log is not None:
            log.append("checkpoint")

    result = await run_inventory_stage(
        study,
        full_text=_TEXT,
        targets=_TARGETS,
        experiments=_EXPERIMENTS,
        contrasts=_CONTRASTS,
        client=client,
        cfg=_CFG,
        checkpoint=checkpoint,
    )
    return study, result


class TestTheExtractionNoLongerProposesTheInventory:
    def test_the_extraction_prompt_asks_for_no_findings(self):
        system, _ = build_extraction_prompt("")
        assert '"findings"' not in system
        assert "FINDINGS" not in system


class TestTheInventoryCall:
    def test_the_prompt_carries_the_committed_claims_with_their_indices_experiments_and_contrasts(self):
        system, payload = build_inventory_prompt(_TEXT, claims=_TARGETS, experiments=_EXPERIMENTS, contrasts=_CONTRASTS)
        assert "[0] genes up" in payload and "[3] reads per sample" in payload
        assert "e1" in payload and "KO vs WT" in payload
        assert _TEXT in payload
        assert "Never give a numeric weight" in system
        assert "exactly one finding" in system

    def test_the_fingerprint_is_the_system_prompt_s_and_does_not_depend_on_the_paper(self):
        a, _ = build_inventory_prompt("one paper", claims=[], experiments=[], contrasts=[])
        b, _ = build_inventory_prompt("another", claims=_TARGETS, experiments=_EXPERIMENTS, contrasts=_CONTRASTS)
        assert a == b
        assert inventory_fingerprint() == read_budget.fingerprint(a)

    @pytest.mark.asyncio
    async def test_it_passes_its_own_measured_budget_never_the_default(self):
        client = _Client(_answer(_findings()))
        study, result = await _run(client)
        assert client.calls[0]["max_tokens"] == 8000
        assert result.inventory["status"] == "established"
        cycle = current_cycle(study.evidence_json, "inventory_stage")
        assert cycle["budget"]["call"] == "inventory"
        assert cycle["budget"]["max_tokens"] == 8000

    @pytest.mark.asyncio
    async def test_each_attempt_records_its_usage(self):
        client = _Client(ModelAnswer(_answer(_findings()), output_tokens=1234, stop_reason="end_turn"))
        study, _ = await _run(client)
        (attempt,) = current_cycle(study.evidence_json, "inventory_stage")["attempts"]
        assert (attempt["output_tokens"], attempt["stop_reason"]) == (1234, "end_turn")
        assert attempt["elapsed_seconds"] is not None

    @pytest.mark.asyncio
    async def test_its_findings_reference_only_the_committed_claims(self):
        findings = _findings()
        findings[0]["claim_indices"] = [0, 1, 7]
        client = _Client(_answer(findings))
        _, result = await _run(client)
        assert result.inventory["findings"][0]["claim_indices"] == [0, 1]


class TestRecovery:
    @pytest.mark.asyncio
    async def test_a_cutoff_then_success_retries_once_with_a_larger_budget(self):
        client = _Client(_TRUNCATED, _answer(_findings()))
        study, result = await _run(client)
        assert [c["max_tokens"] for c in client.calls] == [8000, 16000]
        assert result.inventory["status"] == "established"
        assert not result.failed

    @pytest.mark.asyncio
    async def test_a_rejected_proposal_gets_one_retry_that_shows_its_problems(self):
        rejected = _findings(F1={"quote": "a sentence nowhere in the paper"})
        client = _Client(_answer(rejected), _answer(_findings()))
        study, result = await _run(client)
        assert [c["max_tokens"] for c in client.calls] == [8000, 8000]
        second = client.calls[1]["payload"]
        assert "a sentence nowhere in the paper" in second
        assert "its quote is not in the paper's text" in second
        assert result.inventory["status"] == "established"
        attempts = current_cycle(study.evidence_json, "inventory_stage")["attempts"]
        assert attempts[0]["outcome"] == "rejected" and attempts[0]["problems"]

    @pytest.mark.asyncio
    async def test_an_invalid_second_answer_is_unresolved_with_both_attempts_problems(self):
        unplaced = _findings()[:2]
        client = _Client(_answer(unplaced), _answer(unplaced))
        _, result = await _run(client)
        assert result.failed
        assert result.inventory["status"] == "unresolved"
        assert result.inventory["reason"].startswith("bioAF could not group the paper's claims into findings: ")
        assert result.inventory["reason"].count("belongs to no finding") == 2

    @pytest.mark.asyncio
    async def test_a_second_answer_with_only_importance_problems_is_provisional(self):
        weak = _findings(F1={"quote": "nowhere"})
        client = _Client(_answer(weak), _answer(weak))
        _, result = await _run(client)
        assert not result.failed
        assert result.inventory["status"] == "provisional"

    @pytest.mark.asyncio
    async def test_truncation_then_rejection_stops_at_two(self):
        client = _Client(_TRUNCATED, _answer(_findings()[:2]), _answer(_findings()))
        _, result = await _run(client)
        assert len(client.calls) == 2
        assert result.failed

    @pytest.mark.asyncio
    async def test_rejection_then_truncation_stops_at_two(self):
        client = _Client(_answer(_findings()[:2]), _TRUNCATED, _answer(_findings()))
        _, result = await _run(client)
        assert len(client.calls) == 2
        assert result.failed
        assert "cut off" in result.cause

    @pytest.mark.asyncio
    async def test_a_restart_does_not_grant_another_attempt(self):
        study = SimpleNamespace(id=1, evidence_json={})
        begin_cycle(study, "inventory_stage")
        start_attempt(study, "inventory_stage", max_tokens=8000)
        client = _Client(_answer(_findings()[:2]), _answer(_findings()))
        _, result = await _run(client, study=study)
        assert len(client.calls) == 1
        assert result.failed

    @pytest.mark.asyncio
    async def test_a_provider_failure_ends_the_cycle_with_its_cause(self):
        client = _Client(ProviderError("down", error_class="transport"))
        _, result = await _run(client)
        assert result.failed
        assert "could not reach" in result.cause
        assert result.issues[-1]["step"] == INVENTORY_INTENT
        assert result.issues[-1]["impact"] == "blocked"

    @pytest.mark.asyncio
    async def test_each_attempt_is_committed_before_it_is_submitted(self):
        log: list = []

        class _Logged(_Client):
            async def submit(self, *args, **kwargs):
                log.append("submit")
                return await super().submit(*args, **kwargs)

        await _run(_Logged(_TRUNCATED, _answer(_findings())), log=log)
        submits = [i for i, entry in enumerate(log) if entry == "submit"]
        assert len(submits) == 2 and all(log[i - 1] == "checkpoint" for i in submits)
