"""A2 orchestration driver for the comprehension half (lit_validation).

read_and_plan advances a requested study through acquiring_text -> reading, runs the extractor, and
then either parks at plan_ready (for the C1 gate) or takes an early-exit terminal classification
(missing_data when no accession, not_reproducible when no nf-core equivalent).
"""

from types import SimpleNamespace

import pytest

from app.services import validation_extraction_service as ext
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

_GOOD = (
    '```json\n{"accessions": ["GSE52778"], "sample_structure": {"organism": "Homo sapiens"}, '
    '"method": {"assay": "bulk RNA-seq", "tools": ["TopHat"], "reference_build": "GRCh37"}, '
    '"claims": [{"metric_key": "alignment_rate", "value": 83.4, "unit": "%", "source_locator": "Results"}], '
    '"data_availability": "deposited", "blockers": []}\n```'
)
_NO_DATA = (
    '```json\n{"accessions": [], "method": {"assay": "bulk RNA-seq"}, "claims": [], '
    '"data_availability": "none", "blockers": []}\n```'
)
_UNMAPPABLE = (
    '```json\n{"accessions": ["GSE99999"], "method": {"assay": "bespoke spatial assay"}, '
    '"claims": [], "data_availability": "deposited", "blockers": []}\n```'
)


def _patch_llm(monkeypatch, response):
    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return response

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _C())


async def _requested(session, admin_user):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_read_and_plan_parks_at_plan_ready(session, admin_user, monkeypatch):
    _patch_llm(monkeypatch, _GOOD)
    study = await _requested(session, admin_user)
    study = await ValidationDriverService.read_and_plan(
        session, study, "full text", admin_user.organization_id, admin_user.id
    )
    await session.commit()
    assert study.state == "plan_ready"
    assert study.reproduction_plan_id is not None


@pytest.mark.asyncio
async def test_read_and_plan_early_exits_missing_data(session, admin_user, monkeypatch):
    _patch_llm(monkeypatch, _NO_DATA)
    study = await _requested(session, admin_user)
    study = await ValidationDriverService.read_and_plan(
        session, study, "full text", admin_user.organization_id, admin_user.id
    )
    await session.commit()
    assert study.state == "classified"
    assert study.classification == "missing_data"
    assert study.failure_reason  # the "why" is recorded


@pytest.mark.asyncio
async def test_read_and_plan_early_exits_not_reproducible(session, admin_user, monkeypatch):
    _patch_llm(monkeypatch, _UNMAPPABLE)
    study = await _requested(session, admin_user)
    study = await ValidationDriverService.read_and_plan(
        session, study, "full text", admin_user.organization_id, admin_user.id
    )
    await session.commit()
    assert study.state == "classified"
    assert study.classification == "not_reproducible"


@pytest.mark.asyncio
async def test_read_and_plan_requires_requested_state(session, admin_user, monkeypatch):
    _patch_llm(monkeypatch, _GOOD)
    study = await _requested(session, admin_user)
    # Push it out of 'requested' first.
    study = await ValidationStudyService.transition(
        session, study.id, admin_user.organization_id, admin_user.id, "acquiring_text"
    )
    with pytest.raises(Exception):
        await ValidationDriverService.read_and_plan(
            session, study, "full text", admin_user.organization_id, admin_user.id
        )
