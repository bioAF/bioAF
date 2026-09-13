"""plan_8_1 section 2.1: the page can ask bioAF to group a study's claims again after its inventory failed.

``POST /api/validation-studies/{id}/inventory/retry`` runs a new inventory cycle over the committed
claims. A study read from pasted text answers 409 asking for the text again, unless the Library now holds
it; the request may carry the text.
"""

import pytest
import pytest_asyncio

from tests.test_two_step_read import _PAPER, _Client, llm, quiet_discovery  # noqa: F401 - fixtures

from app.services.llm_provider_clients import ProviderError
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _enable(session):
    from app.services import beta_features_service

    await beta_features_service.set_flag(session, "lit_validation", True)
    await session.commit()


async def _failed_from_pasted_text(session, user, llm):  # noqa: F811
    llm(_Client(inventory=[ProviderError("down", error_class="transport")]))
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    await session.commit()
    study = await ValidationDriverService.read_and_plan(session, study, _PAPER, user.organization_id, user.id)
    await session.commit()
    return study.id


async def test_a_study_read_from_pasted_text_is_asked_for_the_text(session, admin_user, client, admin_token, llm):  # noqa: F811
    study_id = await _failed_from_pasted_text(session, admin_user, llm)
    headers = {"Authorization": f"Bearer {admin_token}"}
    llm(_Client())
    r = await client.post(f"/api/validation-studies/{study_id}/inventory/retry", json={}, headers=headers)
    assert r.status_code == 409, r.text
    assert "Paste the paper's text again" in r.json()["detail"]


async def test_the_retry_with_the_text_groups_the_committed_claims(session, admin_user, client, admin_token, llm):  # noqa: F811
    study_id = await _failed_from_pasted_text(session, admin_user, llm)
    headers = {"Authorization": f"Bearer {admin_token}"}
    llm(_Client())
    r = await client.post(
        f"/api/validation-studies/{study_id}/inventory/retry", json={"full_text": _PAPER}, headers=headers
    )
    assert r.status_code == 200, r.text
    card = r.json()["report_summary"]["scorecard"]
    assert card["status"] != "not_established"
    assert card["inventory_retry"] is False
