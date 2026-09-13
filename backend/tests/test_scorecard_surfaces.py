"""plan_8 sections 7 and 8: the report, the studies list, the JSON export and the markdown export show
the same Validation Scorecard, proved through their entry points.

The list carries the compact form (the two metrics and the primary indicators), computed from the same
records with the same builder and fetched in a fixed number of queries whatever the number of studies,
never one report per row.
"""

import json

import pytest
import pytest_asyncio
from sqlalchemy import event

from app.services.provenance.report_service import ProvenanceReportService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_finding_inventory import inventory_from_proposal
from app.services.validation_study_service import ValidationStudyService
from tests.test_report_scorecard import _SCALAR_CHECKS

_COMPACT = (
    "status",
    "status_label",
    "score",
    "display_score",
    "score_label",
    "assessed_count",
    "total_count",
    "scope_label",
    "primary_discrepancy_count",
    "primary_unassessed_count",
    "in_progress",
    "rubric_version",
    "inventory_revision",
)


async def _seed(session, user, *, verdicts=("agree", "agree", "agree", "agree", "diverge"), categories=None):
    """A classified study whose findings were each compared as a finding-tier metric."""
    categories = categories or ["supporting"] * (len(verdicts) - 1) + ["primary"]
    study = await ValidationStudyService.create_study(
        session, user.organization_id, user.id, source_doi="10.1/scorecard"
    )
    plan = await ReproductionPlanService.create_plan(session, study, user.id, accessions=[])
    targets = [
        {
            "metric_key": "peak_count",
            "claim_text": f"sample {i + 1} yielded many peaks",
            "claimed_value": 1000 + i,
            "unit": "peaks",
            "bound_key": "peak_count",
            "bound_by": "model",
            "checks": _SCALAR_CHECKS,
        }
        for i in range(len(verdicts))
    ]
    rows = await ReproductionPlanService.add_comparison_targets(session, plan, targets)
    proposal = [
        {
            "description": f"Binding at the sites of sample {i + 1}",
            "claim_indices": [i],
            "importance": category,
            "rationale": "Its role in the paper's conclusions.",
            "quote": targets[i]["claim_text"],
        }
        for i, category in enumerate(categories)
    ]
    plan.finding_inventory_json = inventory_from_proposal(
        proposal, targets=targets, full_text=" ".join(t["claim_text"] for t in targets), decided_by={"kind": "model"}
    )
    study.state = "classified"
    study.classification = "inconclusive"
    study.evidence_json = {
        "comparison_targets": [{"id": r.id, "claim_text": r.claim_text, "metric_key": r.metric_key} for r in rows],
        "classification_result": {
            "comparisons": [
                {"metric_key": "peak_count", "mapped_key": "peak_count", "verdict": v, "advisory": False}
                for v in verdicts
            ],
            "attribution": {"our_side": "cleared", "reasons": []},
            "divergence_attribution": {},
        },
    }
    await session.commit()
    return study


class TestEverySurfaceShowsTheSameScorecard:
    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    async def _surfaces(self, client, session, admin_user, admin_token, study):
        headers = {"Authorization": f"Bearer {admin_token}"}
        detail = (await client.get(f"/api/validation-studies/{study.id}", headers=headers)).json()
        rows = (await client.get("/api/validation-studies", headers=headers)).json()
        exported = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=study.id,
            org_id=admin_user.organization_id,
            user_email=admin_user.email,
            format="json",
        )
        body = json.loads(exported.content)
        entity = body["report"]["entity"] if "report" in body else body["entity"]
        markdown = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=study.id,
            org_id=admin_user.organization_id,
            user_email=admin_user.email,
            format="md",
        )
        text = markdown.content if isinstance(markdown.content, str) else markdown.content.decode()
        row = next(r for r in rows if r["id"] == study.id)
        return detail["report_summary"]["scorecard"], row["scorecard"], entity["report_summary"]["scorecard"], text

    @pytest.mark.asyncio
    async def test_the_report_and_the_json_export_carry_the_same_scorecard(
        self, client, session, admin_user, admin_token
    ):
        study = await _seed(session, admin_user)
        report, _, exported, _ = await self._surfaces(client, session, admin_user, admin_token, study)
        assert report == exported
        assert (report["score_label"], report["scope_label"]) == ("67 / 100", "5 / 5 assessed")

    @pytest.mark.asyncio
    async def test_the_list_carries_the_compact_form_of_the_same_scorecard(
        self, client, session, admin_user, admin_token
    ):
        study = await _seed(session, admin_user)
        report, row, _, _ = await self._surfaces(client, session, admin_user, admin_token, study)
        assert row == {key: report[key] for key in _COMPACT}

    @pytest.mark.asyncio
    async def test_the_markdown_states_the_metrics_weights_and_every_finding(
        self, client, session, admin_user, admin_token
    ):
        study = await _seed(session, admin_user)
        report, _, _, text = await self._surfaces(client, session, admin_user, admin_token, study)
        section = text.split("## Validation Scorecard", 1)[1].split("\n## ", 1)[0]
        assert "67 / 100" in section and "5 / 5 assessed" in section
        assert "weighted rubric version 1" in section
        assert f"{report['supported_weight']} of {report['assessed_weight']}" in section
        assert report["summary"] in section
        for message in report["messages"]:
            assert message["text"] in section
        for item in report["assessed_items"] + report["unassessed_items"]:
            assert item["description"] in section
            assert item["status_label"] in section
        # The scorecard leads the report.
        assert text.index("## Validation Scorecard") < text.index("## Verdict")

    @pytest.mark.asyncio
    async def test_a_study_read_before_the_inventory_says_so_on_every_surface(
        self, client, session, admin_user, admin_token
    ):
        study = await _seed(session, admin_user)
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        plan.finding_inventory_json = None
        await session.commit()
        report, row, exported, text = await self._surfaces(client, session, admin_user, admin_token, study)
        assert report["status"] == row["status"] == exported["status"] == "unavailable"
        assert row["score"] is None and row["total_count"] is None
        assert "Score unavailable for this historical report." in text


class TestTheListIsOneBatch:
    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    @pytest.mark.asyncio
    async def test_the_number_of_queries_does_not_grow_with_the_number_of_studies(
        self, client, session, admin_user, admin_token, db_engine
    ):
        headers = {"Authorization": f"Bearer {admin_token}"}
        # One request first, so what is cached per process (flags, permissions) is not counted.
        await client.get("/api/validation-studies", headers=headers)
        counts = []
        for _ in range(2):
            for _ in range(3):
                await _seed(session, admin_user)
            statements: list[str] = []

            def _count(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            event.listen(db_engine.sync_engine, "before_cursor_execute", _count)
            try:
                response = await client.get("/api/validation-studies", headers=headers)
            finally:
                event.remove(db_engine.sync_engine, "before_cursor_execute", _count)
            assert response.status_code == 200
            counts.append(len(statements))
        assert counts[0] == counts[1]
