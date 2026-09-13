"""plan_8 section 2 at read time, as plan_8_1 section 2.2 changed it: the extraction proposes no inventory.

plan_8 asked the extraction call to propose the finding inventory. plan_8_1 moved it into its own call
(``validation_inventory_stage``) over the committed claims, so the extraction answer shrinks and a failed
inventory never takes the claims with it. The extraction now persists the inventory as pending; the
stage that follows establishes it, still before anything is measured.

Changed per plan_8_1 ("Existing tests expected to change"): these tests asserted the extraction prompt
asked for findings and that ``extract`` persisted the proposed inventory.
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
        async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
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


class TestTheExtractionNoLongerAsksForTheInventory:
    def test_the_extraction_prompt_asks_for_no_findings(self):
        system, _ = build_extraction_prompt("")
        assert '"findings"' not in system
        assert "importance category" not in system

    def test_the_rubric_is_stated_to_the_inventory_call_instead(self):
        from app.services.validation_inventory_stage import build_inventory_prompt

        system, _ = build_inventory_prompt("", claims=[], experiments=[], contrasts=[])
        assert "necessary to support a main conclusion of the paper" in system
        assert "supports, extends or qualifies the main conclusions" in system
        assert "does not itself establish a scientific finding" in system
        assert "Never give a numeric weight" in system
        assert "never its metric name" in system
        assert "exactly one finding" in system


class TestTheReadingIsParsed:
    def test_the_parsed_reading_carries_no_findings(self):
        assert "findings" not in parse_extraction(_READING, full_text=_PAPER)


class TestTheInventoryIsPendingOnThePlan:
    @pytest.mark.asyncio
    async def test_extract_persists_a_pending_inventory_even_when_the_reading_proposes_findings(
        self, session, admin_user, monkeypatch
    ):
        plan = await _extract(session, admin_user, monkeypatch, _READING)
        assert plan.finding_inventory_json["status"] == "pending"
        assert plan.finding_inventory_json["findings"] == []

    @pytest.mark.asyncio
    async def test_the_inventory_stage_sees_only_the_claims_that_were_kept(self, session, admin_user, monkeypatch):
        from app.services.validation_driver_service import ValidationDriverService
        from app.services.validation_inventory_stage import build_inventory_prompt

        plan = await _extract(session, admin_user, monkeypatch, _READING)
        claims = await ValidationDriverService._plan_claims(session, plan)
        _, payload = build_inventory_prompt(_PAPER, claims=claims, experiments=[], contrasts=[])
        # The reading's empty second claim was never kept, so the committed claims are three.
        assert "[0] genes up in KO" in payload
        assert "[1] genes down in KO" in payload
        assert "[2] reads per library" in payload
        assert "[3]" not in payload

    @pytest.mark.asyncio
    async def test_an_unparseable_reading_is_a_failed_read_with_no_inventory(self, session, admin_user, monkeypatch):
        """plan_8_1 section 1.3: an unparseable answer is a failed read. Its plan holds no inventory and
        one bioAF-limitation blocker; the scorecard names the limitation (test_read_failure_projection)."""
        plan = await _extract(session, admin_user, monkeypatch, "prose, no json")
        assert plan.finding_inventory_json is None
        assert plan.blocker_kinds_json == [{"text": plan.blockers_json[0], "kind": "bioaf_limitation"}]
