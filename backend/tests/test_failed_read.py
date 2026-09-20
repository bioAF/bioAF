"""plan_8_1 section 1.3: a failed read is not a fact about the paper.

Studies 42 and 43 were cut off at the model's output limit, and their reports then said the paper had
thin methods, no deposit, no stated reference and no code, and classified each as ``missing_data``. None
of it came from a read.

When a read ends with no usable plan (recovery exhausted, unparseable, refused, unreachable):

- the plan carries ONE blocker, of kind ``bioaf_limitation``, and nothing derived from missing fields;
- parts not read stay null; rows that depend on the read say the paper was not read;
- rows from discovery over the identifiers scanned from the text stay as they are;
- the study ends in ``error``, never in an early-exit classification;
- Retry returns it to ``requested``, and the driver reads it again.
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

_PAPER = "We deposited the data under GSE144396. Reads were aligned to mm9 with GENCODE M23."

_TRUNCATED = ProviderError("cut off", error_class="truncated", text='{"accessions": ["GSE1', output_tokens=16000)

_ABSENCE_STATEMENTS = (
    "insufficient method detail",
    "no data accession found",
    "does not state its reference",
    "could not parse",
    "no code source",
)


def _complete(**overrides) -> str:
    body = {
        "accessions": ["GSE144396"],
        "sample_structure": {"organism": "Mus musculus", "sample_count": 6},
        "method": {"assay": "bulk RNA-seq", "tools": [], "reference_build": "GRCm38"},
        "reported_experiments": [],
        "resources": [],
        "differential_design": {"contrasts": []},
        "claims": [{"metric_key": "", "claim_text": "Hundreds of genes changed.", "value": 300}],
        "significance_ambiguities": [],
        "data_availability": "deposited",
        "code_availability": [],
        "blockers": [],
    }
    body.update(overrides)
    for key in [k for k, v in overrides.items() if v is None]:
        del body[key]
    return "```json\n" + json.dumps(body) + "\n```"


class _Client:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    async def submit(self, prompt, payload, model, api_key, attachments=None, max_tokens=None):
        self.calls += 1
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def llm(monkeypatch):
    """Patch the provider lookup and the client; returns a setter for the scripted answers."""
    monkeypatch.setattr(
        read_budget,
        "load_record",
        lambda call: {"fingerprint": "f", "chosen_budget": 16000, "models": {"claude-opus-4-8": {"papers": []}}},
    )

    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    holder = {}

    def _set(*script):
        holder["client"] = _Client(*script)
        monkeypatch.setattr(ext, "get_client", lambda p: holder["client"])
        return holder["client"]

    return _set


@pytest.fixture
def quiet_discovery(monkeypatch):
    """Discovery and the pre-compute checks without the network; records what discovery was asked."""
    seen: dict = {}

    async def _discover(session, study, plan_like, *, has_full_text, fetcher=None, text_source=None):
        from app.services.validation_driver_service import _named_accessions

        seen["accessions"] = [a["accession"] for a in _named_accessions(study, plan_like, for_discovery=True)]
        seen["code_not_read_reason"] = getattr(plan_like, "code_not_read_reason", None)
        return {}

    async def _no_entries(study, *, fetcher=None):
        return []

    monkeypatch.setattr(ValidationDriverService, "_discover_capabilities", staticmethod(_discover))
    # plan_8_5 section 3.4: the lookup says whether it opened a deposit, so "nothing declared"
    # and "nothing was opened" are not the same answer.
    async def _no_organisms(study, *, fetcher=None):
        return [], False

    monkeypatch.setattr(ValidationDriverService, "_deposit_organisms", staticmethod(_no_organisms))
    monkeypatch.setattr(ValidationDriverService, "_deposit_entries", staticmethod(_no_entries))
    return seen


async def _read(session, admin_user, *, text=_PAPER, route=None, doi=None):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, intended_route=route, source_doi=doi
    )
    await session.flush()
    study = await ValidationDriverService.read_and_plan(session, study, text, admin_user.organization_id, admin_user.id)
    await session.commit()
    plan = (
        await session.execute(select(ReproductionPlan).where(ReproductionPlan.id == study.reproduction_plan_id))
    ).scalar_one()
    return study, plan


class TestAFailedReadIsABioafLimitation:
    @pytest.mark.asyncio
    async def test_two_cutoffs_end_in_error_with_one_limitation_blocker(
        self, session, admin_user, llm, quiet_discovery
    ):
        llm(_TRUNCATED, _TRUNCATED)
        study, plan = await _read(session, admin_user)
        assert study.state == "error"
        assert study.classification is None
        (blocker,) = plan.blockers_json
        assert blocker.startswith("bioAF could not read the paper: ")
        assert blocker.endswith("This is a bioAF limitation, not a finding about the paper.")
        assert plan.blocker_kinds_json == [{"text": blocker, "kind": "bioaf_limitation"}]
        assert study.failure_reason == blocker

    @pytest.mark.asyncio
    async def test_no_absence_statement_is_derived_from_the_missing_fields(
        self, session, admin_user, llm, quiet_discovery
    ):
        llm(_TRUNCATED, _TRUNCATED)
        study, plan = await _read(session, admin_user)
        said = " ".join([*(plan.blockers_json or []), study.failure_reason or ""]).lower()
        for statement in _ABSENCE_STATEMENTS:
            assert statement not in said

    @pytest.mark.asyncio
    async def test_parts_not_read_stay_null(self, session, admin_user, llm, quiet_discovery):
        llm(_TRUNCATED, _TRUNCATED)
        study, plan = await _read(session, admin_user)
        assert plan.code_availability_json is None
        assert plan.sample_sheet_json is None
        assert plan.finding_inventory_json is None
        assert plan.reference_build is None
        targets = (
            (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
            .scalars()
            .all()
        )
        assert targets == []
        assert quiet_discovery["code_not_read_reason"] == "The paper was not read."

    @pytest.mark.asyncio
    async def test_rows_that_depend_on_the_read_say_the_paper_was_not_read(
        self, session, admin_user, llm, quiet_discovery
    ):
        llm(_TRUNCATED, _TRUNCATED)
        study, _plan = await _read(session, admin_user)
        checks = study.evidence_json["precompute_checks"]
        assert checks["species_matches"]["detail"] == "The paper was not read."
        assert checks["sample_data_matches_paper"]["detail"] == "The paper was not read."

    @pytest.mark.asyncio
    async def test_discovery_over_scanned_identifiers_is_unchanged(self, session, admin_user, llm, quiet_discovery):
        llm(_TRUNCATED, _TRUNCATED)
        await _read(session, admin_user)
        assert "GSE144396" in quiet_discovery["accessions"]

    @pytest.mark.asyncio
    async def test_a_terminal_refusal_is_a_failed_read_too(self, session, admin_user, llm, quiet_discovery):
        client = llm(ProviderError("no", error_class="refusal"))
        study, plan = await _read(session, admin_user)
        assert client.calls == 1
        assert study.state == "error"
        assert "declined" in plan.blockers_json[0]

    @pytest.mark.asyncio
    async def test_an_unparseable_answer_is_a_failed_read_not_an_early_exit(
        self, session, admin_user, llm, quiet_discovery
    ):
        llm("prose, no JSON")
        study, _plan = await _read(session, admin_user)
        assert study.state == "error"
        assert study.classification is None

    @pytest.mark.asyncio
    async def test_the_extraction_cycle_is_on_the_record(self, session, admin_user, llm, quiet_discovery):
        llm(_TRUNCATED, _TRUNCATED)
        study, _plan = await _read(session, admin_user)
        cycle = study.evidence_json["extraction"]
        assert cycle["status"] == "failed"
        assert [a["outcome"] for a in cycle["attempts"]] == ["truncated", "truncated"]
        assert cycle["text"]["source"] == "pasted"
        assert cycle["text"]["sha256"]


class TestAnOtherwiseUsableAnswer:
    @pytest.mark.asyncio
    async def test_missing_code_availability_is_not_read_and_the_plan_stands(
        self, session, admin_user, llm, quiet_discovery
    ):
        llm(_complete(code_availability=None))
        study, plan = await _read(session, admin_user)
        assert plan.code_availability_json is None
        assert quiet_discovery["code_not_read_reason"] == "Not read: the reading omitted the paper's code availability."
        assert plan.pipeline_key == "nf-core/rnaseq"
        assert study.state in ("plan_ready", "classified")

    @pytest.mark.asyncio
    async def test_an_omitted_sample_count_is_not_read_in_the_check(self, session, admin_user, llm, quiet_discovery):
        llm(_complete(sample_structure={"organism": "Mus musculus"}))
        study, _plan = await _read(session, admin_user)
        assert study.evidence_json["precompute_checks"]["sample_data_matches_paper"]["detail"] == (
            "Not read: the reading omitted how many samples the paper used."
        )

    @pytest.mark.asyncio
    async def test_an_omitted_reference_is_not_read_never_unstated(self, session, admin_user, llm, quiet_discovery):
        llm(_complete(method={"assay": "bulk RNA-seq", "tools": []}))
        _study, plan = await _read(session, admin_user)
        joined = " ".join(plan.blockers_json or [])
        assert "does not state its reference" not in joined
        assert "reference was not read" in joined

    @pytest.mark.asyncio
    async def test_an_explicitly_empty_reference_is_unstated(self, session, admin_user, llm, quiet_discovery):
        llm(_complete(method={"assay": "bulk RNA-seq", "tools": [], "reference_build": ""}))
        _study, plan = await _read(session, admin_user)
        assert "does not state its reference" in " ".join(plan.blockers_json or [])

    @pytest.mark.asyncio
    async def test_an_experiment_omitting_its_annotation_is_not_read_while_an_empty_one_is_unstated(
        self, session, admin_user, llm, quiet_discovery
    ):
        experiments = [
            {"id": "e1", "assay": "bulk RNA-seq", "reference": {"assembly": "GRCm38"}, "claim_indices": [0]},
        ]
        llm(_complete(reported_experiments=experiments))
        _study, plan = await _read(session, admin_user)
        (experiment,) = plan.reported_experiments_json
        assert experiment["reference"]["annotation"]["status"] == "not_read"

        stated_empty = [
            {
                "id": "e1",
                "assay": "bulk RNA-seq",
                "reference": {"assembly": "GRCm38", "annotation": ""},
                "claim_indices": [0],
            }
        ]
        llm(_complete(reported_experiments=stated_empty))
        _study, plan = await _read(session, admin_user)
        (experiment,) = plan.reported_experiments_json
        assert experiment["reference"]["annotation"]["status"] != "not_read"


class TestRetryReadsAgain:
    @pytest.mark.asyncio
    async def test_retry_on_a_failed_read_returns_to_requested_and_the_driver_reads_it(
        self, session, admin_user, llm, quiet_discovery, monkeypatch
    ):
        llm(_TRUNCATED, _TRUNCATED)
        study, first_plan = await _read(session, admin_user, route="deposit", doi="10.1/retry")
        assert study.state == "error"

        study = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)
        await session.commit()
        assert study.state == "requested"

        # The driver reads a requested study whose route was chosen; the paper text comes from its DOI.
        from app.services.literature.fulltext_service import FullTextFetchService, FullTextResult

        async def _fetch(*, doi=None, pmid=None, pmcid=None):
            return FullTextResult(text=_PAPER, source="europepmc", external_id="PMC1")

        monkeypatch.setattr(FullTextFetchService, "fetch", _fetch)

        async def _stay(session, study):  # keep the tick from approving onward
            return False

        monkeypatch.setattr(ValidationDriverService, "_handle_plan_ready", staticmethod(_stay))
        llm(_complete())
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
        assert study.state == "plan_ready"
        assert study.reproduction_plan_id != first_plan.id
        await session.refresh(first_plan)
        assert first_plan.superseded_at is not None
        assert study.evidence_json["extraction"]["status"] == "succeeded"
        assert study.evidence_json["extraction_history"][0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_retry_on_any_other_failure_behaves_as_before(self, session, admin_user):
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        await session.flush()
        for state in ("acquiring_text", "reading", "plan_ready", "acquiring_data"):
            await ValidationStudyService.transition(session, study.id, admin_user.organization_id, admin_user.id, state)
        study.evidence_json = {"extraction": {"cycle": 1, "status": "succeeded", "attempts": []}}
        await ValidationStudyService.transition(
            session, study.id, admin_user.organization_id, admin_user.id, "error", failure_reason="a node died"
        )
        await session.commit()
        study = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)
        assert study.state == "plan_ready"
