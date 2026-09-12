"""change_7.5 section 4.3: the page, the JSON export and the markdown agree, proved through their entry
points. Each renders every claim's experiment, predicate, four checks and consistency from the one
projection, `validation_report_summary.summarize`.
"""

import pytest
import pytest_asyncio

from app.services.provenance.data_gatherer import ProvenanceDataGatherer
from app.services.provenance.json_renderer import JsonRenderer
from app.services.provenance.markdown_renderer import MarkdownRenderer
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService
from tests.test_report_claim_checks import EVIDENCE, PLAN, TARGETS

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _enable_lit_validation(session):
    from app.services import beta_features_service

    await beta_features_service.set_flag(session, "lit_validation", True)
    await session.commit()


async def _seed(session, user) -> int:
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, source_accession="GSE555001")
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=["GSE555001"],
        pipeline_key="nf-core/rnaseq",
        differential_design=PLAN["differential_design"],
        reported_experiments=PLAN["reported_experiments"],
        analysis_selection=PLAN["analysis_selection"],
    )
    await ReproductionPlanService.add_comparison_targets(session, plan, [dict(t, metric_key="") for t in TARGETS])
    study.state = "comparing"
    study.evidence_json = dict(EVIDENCE)
    await session.commit()
    return study.id


async def test_the_page_the_json_and_the_markdown_state_the_same_claims(session, admin_user, client, admin_token):
    study_id = await _seed(session, admin_user)

    # The page reads the API's projection.
    r = await client.get(f"/api/validation-studies/{study_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    page = r.json()["report_summary"]["claims"]

    # The JSON export carries the same projection.
    data = await ProvenanceDataGatherer.gather_validation_study(session, study_id, admin_user.organization_id)
    report = JsonRenderer.render("validation_study", data, "admin@test.com")
    exported = report["entity"]["report_summary"]["claims"] if "entity" in report else report["report_summary"]["claims"]

    # The markdown renders from it.
    markdown = MarkdownRenderer.render("validation_study", report)

    for page_claim, json_claim in zip(page, exported):
        assert page_claim["predicate"] == json_claim["predicate"]
        assert page_claim["checks"] == json_claim["checks"]
        assert page_claim["experiment"] == json_claim["experiment"]
        assert page_claim["consistency"] == json_claim["consistency"]
        if page_claim["predicate"]:
            assert page_claim["predicate"] in markdown
        for check in page_claim["checks"]:
            assert f"{check['label']}: {check['status_label']}" in markdown
    assert page[1]["consistency"]["label"] in markdown
