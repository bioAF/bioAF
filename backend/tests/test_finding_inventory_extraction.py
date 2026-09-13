"""plan_8 section 2 at read time: the reading proposes the finding inventory before anything is measured.

The grouping, the importance categories and the criteria are established at extraction, so no result
can move them. The proposal is validated against the fixed rubric and persisted on the plan, and a
reading that proposes none, or cannot be parsed, persists an unestablished inventory rather than no
inventory: "Scope not established" is a fact about this study, not a historical gap.
"""

from types import SimpleNamespace

import pytest

from app.services import validation_extraction_service as ext
from app.services.validation_extraction_service import (
    ValidationExtractionService,
    build_extraction_prompt,
    parse_extraction,
)
from app.services.validation_study_service import ValidationStudyService

_PAPER = (
    "Knockout of the regulator deregulates hundreds of genes in stem cells. This is the central finding "
    "of the study. Differentiated cells show a smaller response, which extends the central finding. "
    "Libraries were sequenced deeply."
)

_READING = """```json
{"accessions": ["GSE900001"],
 "method": {"assay": "bulk RNA-seq", "tools": [], "reference_build": "GRCm38"},
 "differential_design": {"contrasts": [
   {"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT", "test_samples": [], "reference_samples": []}]},
 "claims": [
   {"metric_key": "de_genes_up", "claim_text": "genes up in KO", "value": 100, "unit": "genes", "source_locator": "Fig. 2", "contrast": "KO vs WT"},
   {"metric_key": "", "claim_text": "", "value": null},
   {"metric_key": "de_genes_down", "claim_text": "genes down in KO", "value": 200, "unit": "genes", "source_locator": "Fig. 2", "contrast": "KO vs WT"},
   {"metric_key": "total_sequences", "claim_text": "reads per library", "value": 30000000, "unit": "reads", "source_locator": "Methods"}
 ],
 "findings": [
   {"description": "Knockout deregulates hundreds of genes", "claim_indices": [0, 2], "importance": "primary",
    "rationale": "The paper states it as its central finding.", "quote": "This is the central finding of the study."},
   {"description": "Sequencing depth", "claim_indices": [3], "importance": "technical",
    "rationale": "It enables the differential analysis.", "prerequisite_for": [0]}
 ],
 "data_availability": "deposited", "blockers": []}
```"""


def _patch_llm(monkeypatch, response):
    class _Client:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return response

    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="a-model", api_key=None)

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _Client())


async def _extract(session, admin_user, monkeypatch, response, text=_PAPER):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, response)
    plan = await ValidationExtractionService.extract(session, study, text, admin_user.organization_id, admin_user.id)
    await session.commit()
    return plan


class TestThePromptAsksForTheInventory:
    def test_the_schema_asks_for_findings_with_their_claims_importance_rationale_and_quote(self):
        system, _ = build_extraction_prompt("")
        assert '"findings": [' in system
        for field in ('"claim_indices"', '"importance": "primary | supporting | technical"', '"rationale"', '"quote"'):
            assert field in system

    def test_the_rules_state_the_rubric_and_refuse_a_numeric_weight(self):
        system, _ = build_extraction_prompt("")
        assert "necessary to support a main conclusion of the paper" in system
        assert "supports, extends or qualifies the main conclusions" in system
        assert "does not itself establish a scientific finding" in system
        assert "Never give a numeric weight" in system
        assert "never its metric name" in system

    def test_the_rules_say_one_finding_is_one_opportunity(self):
        system, _ = build_extraction_prompt("")
        assert "Every claim belongs to exactly one finding" in system


class TestTheReadingIsParsed:
    def test_the_proposed_findings_are_carried(self):
        parsed = parse_extraction(_READING, full_text=_PAPER)
        assert [f["description"] for f in parsed["findings"]] == [
            "Knockout deregulates hundreds of genes",
            "Sequencing depth",
        ]

    def test_a_reading_that_proposed_none_carries_none(self):
        parsed = parse_extraction('```json\n{"claims": []}\n```')
        assert parsed["findings"] is None

    def test_an_unparseable_reading_carries_none(self):
        assert parse_extraction("no json here")["findings"] is None


class TestTheInventoryIsPersistedOnThePlan:
    @pytest.mark.asyncio
    async def test_a_valid_proposal_is_established_on_the_kept_claims(self, session, admin_user, monkeypatch):
        plan = await _extract(session, admin_user, monkeypatch, _READING)
        inventory = plan.finding_inventory_json
        assert inventory["status"] == "established"
        assert inventory["revision"] == 1
        # The reading's empty second claim was never kept, so its positions shift onto the targets.
        assert [f["claim_indices"] for f in inventory["findings"]] == [[0, 1], [2]]
        primary, technical = inventory["findings"]
        assert (primary["id"], primary["importance"]["category"], primary["importance"]["weight"]) == (
            "F1",
            "primary",
            2,
        )
        assert technical["prerequisite_for"] == ["F1"]
        assert primary["importance"]["model"] == "a-model"
        assert primary["contrast_index"] == 0

    @pytest.mark.asyncio
    async def test_a_reading_without_findings_is_persisted_as_not_established(self, session, admin_user, monkeypatch):
        reading = _READING.replace('"findings"', '"not_findings"')
        plan = await _extract(session, admin_user, monkeypatch, reading)
        inventory = plan.finding_inventory_json
        assert inventory is not None
        assert inventory["status"] == "unresolved"
        assert inventory["reason"]

    @pytest.mark.asyncio
    async def test_a_quote_the_paper_does_not_contain_is_not_established(self, session, admin_user, monkeypatch):
        plan = await _extract(session, admin_user, monkeypatch, _READING, text="A different paper entirely.")
        assert plan.finding_inventory_json["status"] == "unresolved"
        assert "quote" in plan.finding_inventory_json["reason"]

    @pytest.mark.asyncio
    async def test_an_unparseable_reading_is_persisted_as_not_established(self, session, admin_user, monkeypatch):
        plan = await _extract(session, admin_user, monkeypatch, "prose, no json")
        assert plan.finding_inventory_json["status"] == "unresolved"
        assert "could not be read" in plan.finding_inventory_json["reason"]
