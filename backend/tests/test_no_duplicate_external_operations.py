"""change_7.2 sections 2 and 8: one external operation per logical attempt, adopted after a restart.

Fencing a database write does not recall a Kubernetes job that has already launched. A worker can
dispatch a pipeline run and crash before recording its identifier, and its replacement must not
launch a second one: an assisted organization is asked whether to resume it, an autonomous one adopts
it and records the adoption. Neither may silently launch a duplicate.
"""

import pytest

from app.models.pipeline_run import PipelineRun
from app.services import validation_ownership as own
from app.services.pipeline_run_service import PipelineRunService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_autonomy import AUTONOMY_AUTONOMOUS
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService


class _LaunchSpy:
    def __init__(self):
        self.calls = []

    async def __call__(self, session, org_id, user_id, data, *, via_assistant=False):
        self.calls.append(data)
        run = PipelineRun(
            organization_id=org_id,
            experiment_id=data.experiment_id,
            pipeline_name=data.pipeline_key,
            pipeline_version="test",
            status="running",
            parameters_json=dict(data.parameters or {}),
            submitted_by_user_id=user_id,
        )
        session.add(run)
        await session.flush()
        return run


async def _experiment_id(session, user):
    from app.schemas.experiment import ExperimentCreate
    from app.services.experiment_service import ExperimentService

    exp = await ExperimentService.create_experiment(
        session, user.organization_id, user.id, ExperimentCreate(name="Reproduction: test")
    )
    return exp.id


async def _study(session, user, *, state="acquiring_data"):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, source_doi="10.1/abc")
    await ReproductionPlanService.create_plan(
        session, study, user.id, accessions=["SRR390728"], pipeline_key="nf-core/rnaseq", pipeline_version="3.14.0"
    )
    study.state = state
    study.experiment_id = await _experiment_id(session, user)
    study.evidence_json = {"assessment": {"at": "already run"}}
    await session.flush()
    return study


class TestTheIdentityExistsBeforeTheDispatch:
    @pytest.mark.asyncio
    async def test_a_launch_records_the_operation_it_dispatched(self, session, admin_user, monkeypatch):
        monkeypatch.setattr(PipelineRunService, "launch_run", _LaunchSpy())
        study = await _study(session, admin_user)
        await ValidationDriverService._launch_fetchngs(session, study)
        record = own.operation_of(study, "data_acquisition")
        assert record["status"] == own.OP_RUNNING
        assert record["external_id"] == study.data_run_id

    @pytest.mark.asyncio
    async def test_a_second_tick_does_not_launch_a_second_run(self, session, admin_user, monkeypatch):
        spy = _LaunchSpy()
        monkeypatch.setattr(PipelineRunService, "launch_run", spy)
        study = await _study(session, admin_user)
        await ValidationDriverService._launch_fetchngs(session, study)
        await ValidationDriverService._launch_fetchngs(session, study)
        assert len(spy.calls) == 1


class TestARestartAdoptsRatherThanRelaunches:
    @pytest.mark.asyncio
    async def test_an_autonomous_organization_adopts_the_running_operation(
        self, session, admin_user, monkeypatch
    ):
        """The crash window: dispatched, and the identifier never landed."""
        spy = _LaunchSpy()
        monkeypatch.setattr(PipelineRunService, "launch_run", spy)
        from app.models.organization import Organization

        organization = await session.get(Organization, admin_user.organization_id)
        organization.lit_validation_autonomy = AUTONOMY_AUTONOMOUS
        study = await _study(session, admin_user)
        await own.begin_operation(session, study, "data_acquisition", kind="nf-core/fetchngs")

        await ValidationDriverService._launch_fetchngs(session, study)

        assert spy.calls == []
        assert own.operation_of(study, "data_acquisition")["adopted_by"] == "driver"

    @pytest.mark.asyncio
    async def test_an_assisted_organization_is_asked_first(self, session, admin_user, monkeypatch):
        spy = _LaunchSpy()
        monkeypatch.setattr(PipelineRunService, "launch_run", spy)
        study = await _study(session, admin_user)
        await own.begin_operation(session, study, "data_acquisition", kind="nf-core/fetchngs")

        await ValidationDriverService._launch_fetchngs(session, study)

        assert spy.calls == []
        assert study.evidence_json["awaiting_adoption"]["action"]

    @pytest.mark.asyncio
    async def test_a_finished_operation_does_not_block_a_new_attempt(self, session, admin_user, monkeypatch):
        spy = _LaunchSpy()
        monkeypatch.setattr(PipelineRunService, "launch_run", spy)
        study = await _study(session, admin_user)
        await own.begin_operation(session, study, "data_acquisition", kind="nf-core/fetchngs")
        await own.finish_operation(session, study, "data_acquisition")

        await ValidationDriverService._launch_fetchngs(session, study)
        assert len(spy.calls) == 1


class TestOneSiblingPerAuthorization:
    @pytest.mark.asyncio
    async def test_a_retried_approval_creates_no_second_sibling(self, session, admin_user):
        """`both` spawns a study on the raw route. Nothing checked for one, so a retried approval
        would create a second, with its own copied plan and its own compute bill."""
        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_doi="10.1/abc"
        )
        study.state = "plan_ready"
        study.evidence_json = {"capabilities": {"preprocessed_data": {"value": "yes"}, "raw_data": {"value": "yes"}}}
        await session.flush()

        first = await ValidationStudyService._spawn_sibling(session, study, admin_user.id)
        study.evidence_json = {**(study.evidence_json or {}), "sibling_study_id": first.id}
        await session.flush()
        second = await ValidationStudyService._spawn_sibling(session, study, admin_user.id)
        assert second.id == first.id

    @pytest.mark.asyncio
    async def test_both_authorizes_each_leg_through_the_policy(self, session, admin_user):
        from app.services.validation_route_policy import NO_ADAPTER, decide_route

        decision = decide_route(
            route="both",
            capabilities={
                "raw_data": {"value": "yes"},
                "preprocessed_data": {"value": "yes"},
                "deposits": [
                    {
                        "archive": "ega",
                        "accession": "EGAS00001003667",
                        "access": "controlled",
                        "supported": "no",
                        "raw_data": "yes",
                        "preprocessed_data": "yes",
                    }
                ],
            },
        )
        assert decision.action == NO_ADAPTER
        assert len(decision.findings) == 2

    @pytest.mark.asyncio
    async def test_the_two_studies_conclude_independently(self, session, admin_user):
        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_doi="10.1/abc"
        )
        study.state = "plan_ready"
        await session.flush()
        sibling = await ValidationStudyService._spawn_sibling(session, study, admin_user.id)
        assert sibling.id != study.id
        assert sibling.evidence_json["route"] == "pipeline"
        assert sibling.state == "acquiring_data"
