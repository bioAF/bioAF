"""plan_8_1 section 2.1: the read commits in two steps, and a stage that succeeded survives a later failure.

1. Extraction, discovery and the pre-compute checks are committed. The study stays in ``reading`` and its
   inventory is ``pending``: the scorecard reads "In progress", "The findings are being established."
2. The inventory stage runs over the committed claims. The study then moves to ``plan_ready`` or its
   early exit.

A worker that stops between the two leaves a study the next tick resumes at the inventory, never
repeating the extraction. The text is re-read from the source the extraction recorded, and a text whose
hash differs is read again rather than regrouped. An inventory that fails keeps the claims.

D7: the read takes its text from Europe PMC by DOI first, then the Literature Library's stored text for the
study's paper, then pasted text, and records the source.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.comparison_target import ComparisonTarget
from app.models.reproduction_plan import ReproductionPlan
from app.services import validation_extraction_service as ext
from app.services import validation_read_budget as read_budget
from app.services.llm_provider_clients import ProviderError
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

_PAPER = "The knockout changes hundreds of genes. This is our main result. Depth was high. GSE144396."

_EXTRACTION = {
    "accessions": ["GSE144396"],
    "sample_structure": {"organism": "Mus musculus", "sample_count": 6},
    "method": {"assay": "bulk RNA-seq", "tools": [], "reference_build": "GRCm38"},
    "reported_experiments": [],
    "resources": [],
    "differential_design": {"contrasts": []},
    "claims": [
        {"metric_key": "", "claim_text": "genes up", "value": 100},
        {"metric_key": "", "claim_text": "genes down", "value": 200},
    ],
    "significance_ambiguities": [],
    "data_availability": "deposited",
    "code_availability": [],
    "blockers": [],
}
_FINDINGS = {
    "findings": [
        {
            "description": "The knockout changes hundreds of genes",
            "claim_indices": [0, 1],
            "importance": "primary",
            "rationale": "It is the main result.",
            "quote": "This is our main result.",
        }
    ]
}


def _fenced(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


class _Client:
    """Answers the extraction and the inventory calls from separate scripts, recording each call."""

    def __init__(self, *, extraction=None, inventory=None, log=None):
        self.extraction = list(extraction or [_fenced(_EXTRACTION)])
        self.inventory = list(inventory or [_fenced(_FINDINGS)])
        self.calls: list[str] = []
        self.log = log

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        if prompt.startswith("You are establishing the FINDINGS"):
            kind, script = "inventory", self.inventory
        elif prompt.startswith("You are a computational biology reproduction analyst"):
            kind, script = "extraction", self.extraction
        else:
            return _fenced({"bindings": [], "answer": "yes", "reason": "r", "confidence": 0.5})
        self.calls.append(kind)
        if self.log is not None:
            self.log.append(kind)
        step = script[min(self.calls.count(kind) - 1, len(script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(
        read_budget,
        "load_record",
        lambda call: {"fingerprint": "f", "chosen_budget": 16000, "models": {"claude-opus-4-8": {}}},
    )

    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)

    def _set(client):
        monkeypatch.setattr(ext, "get_client", lambda p: client)
        from app.services import validation_driver_service as driver

        monkeypatch.setattr(driver, "get_client", lambda p: client)
        return client

    return _set


@pytest.fixture(autouse=True)
def quiet_discovery(monkeypatch):
    async def _discover(session, study, plan_like, *, has_full_text, fetcher=None, text_source=None):
        return {}

    async def _nothing(study, *, fetcher=None):
        return []

    monkeypatch.setattr(ValidationDriverService, "_discover_capabilities", staticmethod(_discover))
    monkeypatch.setattr(ValidationDriverService, "_deposit_organisms", staticmethod(_nothing))
    monkeypatch.setattr(ValidationDriverService, "_deposit_entries", staticmethod(_nothing))


async def _study(session, user, **kwargs):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, **kwargs)
    await session.commit()
    return study


async def _plan(session, study) -> ReproductionPlan:
    return (
        await session.execute(select(ReproductionPlan).where(ReproductionPlan.id == study.reproduction_plan_id))
    ).scalar_one()


class TestTwoCommits:
    @pytest.mark.asyncio
    async def test_extraction_is_committed_with_the_inventory_pending_before_the_inventory_call(
        self, session, admin_user, llm, monkeypatch
    ):
        seen: dict = {}
        from app.services import validation_inventory_stage as stage

        real = stage.run_inventory_stage

        async def _observe(study, **kwargs):
            # A separate session sees what was committed before this call.
            from app.database import async_session_factory

            async with async_session_factory() as other:
                from app.models.validation_study import ValidationStudy

                row = (await other.execute(select(ValidationStudy).where(ValidationStudy.id == study.id))).scalar_one()
                plan = (
                    await other.execute(select(ReproductionPlan).where(ReproductionPlan.id == row.reproduction_plan_id))
                ).scalar_one()
                claims = (
                    (
                        await other.execute(
                            select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                seen.update(state=row.state, inventory=plan.finding_inventory_json["status"], claims=len(claims))
            return await real(study, **kwargs)

        from app.services import validation_driver_service as driver

        monkeypatch.setattr(driver, "run_inventory_stage", _observe)
        llm(_Client())
        study = await _study(session, admin_user)
        study = await ValidationDriverService.read_and_plan(
            session, study, _PAPER, admin_user.organization_id, admin_user.id
        )
        await session.commit()
        assert seen == {"state": "reading", "inventory": "pending", "claims": 2}
        assert study.state == "plan_ready"
        assert (await _plan(session, study)).finding_inventory_json["status"] == "established"

    @pytest.mark.asyncio
    async def test_a_pending_inventory_reads_in_progress_on_the_scorecard(self):
        from app.services.validation_inventory_stage import pending_inventory
        from app.services.validation_report_summary import scorecard_projection

        card = scorecard_projection(
            study={"state": "reading"},
            evidence={"extraction": {"status": "succeeded"}},
            plan={"finding_inventory": pending_inventory()},
            targets=[{"claim_text": "genes up"}],
        )
        assert card["in_progress"] is True
        assert card["in_progress_label"] == "In progress"
        assert card["reason"] == "The findings are being established."


class TestAnInterruptedInventoryResumes:
    @pytest.mark.asyncio
    async def test_the_driver_resumes_at_the_inventory_and_never_calls_the_extraction(
        self, session, admin_user, llm, monkeypatch
    ):
        from app.services import validation_driver_service as driver
        from app.services.literature.fulltext_service import FullTextFetchService, FullTextResult

        async def _fetch(*, doi=None, pmid=None, pmcid=None):
            return FullTextResult(text=_PAPER, source="europepmc", external_id="PMC1")

        monkeypatch.setattr(FullTextFetchService, "fetch", _fetch)

        async def _stop(study, **kwargs):
            raise RuntimeError("the worker stopped")

        monkeypatch.setattr(driver, "run_inventory_stage", _stop)
        llm(_Client())
        study = await _study(session, admin_user, source_doi="10.1/two-step", intended_route="deposit")
        study_id = study.id
        with pytest.raises(RuntimeError):
            await ValidationDriverService.read_and_plan(session, study, None, admin_user.organization_id, admin_user.id)
        await session.rollback()
        session.expire_all()
        from app.models.validation_study import ValidationStudy

        study = (await session.execute(select(ValidationStudy).where(ValidationStudy.id == study_id))).scalar_one()
        assert study.state == "reading"

        from app.services import validation_inventory_stage as stage

        monkeypatch.setattr(driver, "run_inventory_stage", stage.run_inventory_stage)

        async def _stay(session, study):
            return False

        monkeypatch.setattr(ValidationDriverService, "_handle_plan_ready", staticmethod(_stay))
        client = llm(_Client())
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert client.calls == ["inventory"]
        assert study.state == "plan_ready"
        assert (await _plan(session, study)).finding_inventory_json["status"] == "established"

    @pytest.mark.asyncio
    async def test_a_text_that_changed_is_read_again_not_regrouped(self, session, admin_user, llm, monkeypatch):
        from app.services import validation_driver_service as driver
        from app.services.literature.fulltext_service import FullTextFetchService, FullTextResult

        texts = iter([_PAPER, _PAPER + " A correction was published."])

        async def _fetch(*, doi=None, pmid=None, pmcid=None):
            return FullTextResult(text=next(texts), source="europepmc", external_id="PMC1")

        monkeypatch.setattr(FullTextFetchService, "fetch", _fetch)

        async def _stop(study, **kwargs):
            raise RuntimeError("the worker stopped")

        monkeypatch.setattr(driver, "run_inventory_stage", _stop)
        llm(_Client())
        study = await _study(session, admin_user, source_doi="10.1/changed", intended_route="deposit")
        study_id = study.id
        with pytest.raises(RuntimeError):
            await ValidationDriverService.read_and_plan(session, study, None, admin_user.organization_id, admin_user.id)
        await session.rollback()
        session.expire_all()
        from app.models.validation_study import ValidationStudy

        first_plan = (
            (await session.execute(select(ValidationStudy).where(ValidationStudy.id == study_id)))
            .scalar_one()
            .reproduction_plan_id
        )

        from app.services import validation_inventory_stage as stage

        monkeypatch.setattr(driver, "run_inventory_stage", stage.run_inventory_stage)

        async def _stay(session, study):
            return False

        monkeypatch.setattr(ValidationDriverService, "_handle_plan_ready", staticmethod(_stay))
        client = llm(_Client())
        await ValidationDriverService.advance_active_studies(session)
        session.expire_all()
        study = (await session.execute(select(ValidationStudy).where(ValidationStudy.id == study_id))).scalar_one()
        assert client.calls == ["extraction", "inventory"]
        assert study.reproduction_plan_id != first_plan


class TestAnInventoryFailureKeepsTheClaims:
    @pytest.mark.asyncio
    async def test_the_claims_stay_and_the_scorecard_says_bioaf_could_not_group_them(self, session, admin_user, llm):
        llm(_Client(inventory=[ProviderError("down", error_class="transport")]))
        study = await _study(session, admin_user)
        study = await ValidationDriverService.read_and_plan(
            session, study, _PAPER, admin_user.organization_id, admin_user.id
        )
        await session.commit()
        plan = await _plan(session, study)
        claims = (
            (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
            .scalars()
            .all()
        )
        assert len(claims) == 2
        assert study.state == "plan_ready"
        inventory = plan.finding_inventory_json
        assert inventory["failed"] is True
        assert inventory["reason"].startswith("bioAF could not group the paper's claims into findings: ")

        from app.services.validation_report_summary import report_summary_for

        summary = await report_summary_for(session, study, admin_user.organization_id)
        assert summary["scorecard"]["status_label"] == "Scope not established"
        assert summary["scorecard"]["reason"] == inventory["reason"]
        assert len(summary["claims"]) == 2


class TestWhereTheTextComesFrom:
    @pytest.mark.asyncio
    async def test_europe_pmc_comes_first_and_is_recorded(self, session, admin_user, llm, monkeypatch):
        from app.services.literature.fulltext_service import FullTextFetchService, FullTextResult

        async def _fetch(*, doi=None, pmid=None, pmcid=None):
            return FullTextResult(text=_PAPER, source="europepmc", external_id="PMC1")

        monkeypatch.setattr(FullTextFetchService, "fetch", _fetch)
        llm(_Client())
        study = await _study(session, admin_user, source_doi="10.1/epmc")
        study = await ValidationDriverService.read_and_plan(
            session, study, "pasted text that is not used", admin_user.organization_id, admin_user.id
        )
        assert study.evidence_json["extraction"]["text"]["source"] == "europe_pmc"

    @pytest.mark.asyncio
    async def test_a_paper_europe_pmc_lacks_is_read_from_the_library(self, session, admin_user, llm, monkeypatch):
        from app.models.literature import LiteraturePaper
        from app.services.literature.fulltext_service import FullTextFetchService

        async def _none(*, doi=None, pmid=None, pmcid=None):
            return None

        monkeypatch.setattr(FullTextFetchService, "fetch", _none)
        paper = LiteraturePaper(
            organization_id=admin_user.organization_id,
            title="A paper held in the library",
            title_normalized="a paper held in the library",
            provenance="manual",
            doi="10.1/library",
            has_full_text=True,
            extracted_text_uri="gs://literature/papers/1.txt",
        )
        session.add(paper)
        await session.flush()

        class _Storage:
            async def read_text(self, uri):
                assert uri == "gs://literature/papers/1.txt"
                return _PAPER

        from app.services import validation_paper_text

        monkeypatch.setattr(validation_paper_text, "get_storage_adapter", lambda: _Storage())
        llm(_Client())
        study = await _study(session, admin_user, paper_id=paper.id, source_doi="10.1/library")
        study = await ValidationDriverService.read_and_plan(
            session, study, None, admin_user.organization_id, admin_user.id
        )
        assert study.state == "plan_ready"
        assert study.evidence_json["extraction"]["text"]["source"] == "library"

    @pytest.mark.asyncio
    async def test_pasted_text_is_last(self, session, admin_user, llm, monkeypatch):
        from app.services.literature.fulltext_service import FullTextFetchService

        async def _none(*, doi=None, pmid=None, pmcid=None):
            return None

        monkeypatch.setattr(FullTextFetchService, "fetch", _none)
        llm(_Client())
        study = await _study(session, admin_user, source_doi="10.1/none")
        study = await ValidationDriverService.read_and_plan(
            session, study, _PAPER, admin_user.organization_id, admin_user.id
        )
        assert study.evidence_json["extraction"]["text"]["source"] == "pasted"


class TestRetryingAFailedInventory:
    async def _failed(self, session, admin_user, llm, **study_kwargs):
        llm(_Client(inventory=[ProviderError("down", error_class="transport")]))
        study = await _study(session, admin_user, **study_kwargs)
        study = await ValidationDriverService.read_and_plan(
            session, study, None, admin_user.organization_id, admin_user.id
        )
        await session.commit()
        return study

    @pytest.mark.asyncio
    async def test_a_retry_reads_the_recorded_source_and_uses_the_committed_claims(
        self, session, admin_user, llm, monkeypatch
    ):
        from app.services.literature.fulltext_service import FullTextFetchService, FullTextResult

        async def _fetch(*, doi=None, pmid=None, pmcid=None):
            return FullTextResult(text=_PAPER, source="europepmc", external_id="PMC1")

        monkeypatch.setattr(FullTextFetchService, "fetch", _fetch)
        study = await self._failed(session, admin_user, llm, source_doi="10.1/retry")
        plan = await _plan(session, study)
        claim_ids = [
            t.id
            for t in (
                await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id))
            ).scalars()
        ]
        client = llm(_Client())
        study = await ValidationDriverService.retry_inventory(session, study, admin_user.organization_id, admin_user.id)
        await session.commit()
        assert client.calls == ["inventory"]
        plan = await _plan(session, study)
        assert plan.finding_inventory_json["status"] == "established"
        assert study.reproduction_plan_id == plan.id
        assert [
            t.id
            for t in (
                await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id))
            ).scalars()
        ] == claim_ids
        history = study.evidence_json["inventory_stage_history"]
        assert history[-1]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_a_study_read_from_pasted_text_asks_for_the_text_first(self, session, admin_user, llm, monkeypatch):
        from app.services.literature.fulltext_service import FullTextFetchService

        async def _none(*, doi=None, pmid=None, pmcid=None):
            return None

        monkeypatch.setattr(FullTextFetchService, "fetch", _none)
        llm(_Client(inventory=[ProviderError("down", error_class="transport")]))
        study = await _study(session, admin_user)
        study = await ValidationDriverService.read_and_plan(
            session, study, _PAPER, admin_user.organization_id, admin_user.id
        )
        await session.commit()

        from app.services.validation_driver_service import PasteRequired

        with pytest.raises(PasteRequired):
            await ValidationDriverService.retry_inventory(session, study, admin_user.organization_id, admin_user.id)

        client = llm(_Client())
        study = await ValidationDriverService.retry_inventory(
            session, study, admin_user.organization_id, admin_user.id, full_text=_PAPER
        )
        assert client.calls == ["inventory"]
        assert (await _plan(session, study)).finding_inventory_json["status"] == "established"

    @pytest.mark.asyncio
    async def test_a_hash_mismatch_sends_the_study_to_re_read(self, session, admin_user, llm, monkeypatch):
        from app.services.literature.fulltext_service import FullTextFetchService

        async def _none(*, doi=None, pmid=None, pmcid=None):
            return None

        monkeypatch.setattr(FullTextFetchService, "fetch", _none)
        llm(_Client(inventory=[ProviderError("down", error_class="transport")]))
        study = await _study(session, admin_user)
        study = await ValidationDriverService.read_and_plan(
            session, study, _PAPER, admin_user.organization_id, admin_user.id
        )
        await session.commit()
        client = llm(_Client())
        study = await ValidationDriverService.retry_inventory(
            session, study, admin_user.organization_id, admin_user.id, full_text=_PAPER + " A different version."
        )
        assert client.calls == []
        assert study.state == "requested"

    @pytest.mark.asyncio
    async def test_only_a_failed_inventory_can_be_retried(self, session, admin_user, llm):
        llm(_Client())
        study = await _study(session, admin_user)
        study = await ValidationDriverService.read_and_plan(
            session, study, _PAPER, admin_user.organization_id, admin_user.id
        )
        await session.commit()
        from app.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await ValidationDriverService.retry_inventory(session, study, admin_user.organization_id, admin_user.id)
