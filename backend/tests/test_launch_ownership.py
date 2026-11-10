"""Organization ownership at the launch and preflight boundary.

Preflight answered from whatever experiment id it was handed, so another
organization's samples, file URIs and saved design could be read back through
it. Launch checked the experiment but stored a project and a resume parent by
bare id, and fed a sample every linked file whatever organization owned it.

The agreed behavior: preflight answers 404 for a missing or foreign experiment,
project or resume parent, and 200 with ``can_launch=false`` for a sample
selection that is not wholly inside the authorized experiment. Launch keeps its
400 refusals, and creates and submits nothing when it refuses. A supplied id is
looked up even when it is zero.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.adapters.compute.kubernetes import _job_submit_result_from_dict
from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_input_file import PipelineRunInputFile
from app.models.project import Project
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.services.auth_service import AuthService
from tests.test_pipeline_run_preview_api import _schema

PREFLIGHT = "/api/pipeline-runs/preflight"
LAUNCH = "/api/pipeline-runs"


async def _sample_with_files(session, org_id, exp, external_id, prefix, *, file_org_id=None):
    sample = Sample(experiment_id=exp.id, external_id=external_id, organism="Homo sapiens")
    session.add(sample)
    await session.flush()
    for mate in ("R1", "R2"):
        f = File(
            organization_id=file_org_id or org_id,
            experiment_id=exp.id,
            gcs_uri=f"gs://{prefix}/{external_id}_{mate}_001.fastq.gz",
            filename=f"{external_id}_{mate}_001.fastq.gz",
            file_type="fastq",
            source_type="upload",
        )
        session.add(f)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    return sample


@pytest_asyncio.fixture
async def world(session, admin_user):
    """Two organizations. Ours holds an experiment, two projects and a run; theirs the same."""
    org_id = admin_user.organization_id
    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    other_user = User(
        email="other@test.com",
        password_hash=AuthService.hash_password("otherpass123"),
        role_id=admin_user.role_id,
        organization_id=other.id,
        status="active",
    )
    session.add(other_user)
    await session.flush()

    for oid in (org_id, other.id):
        for key, defaults, schema in [
            ("nf-core/demo", {}, _schema("demo")),
            ("nf-core/rnaseq", {"strandedness": "reverse"}, None),
        ]:
            session.add(
                PipelineCatalogEntry(
                    organization_id=oid,
                    pipeline_key=key,
                    name=key,
                    source_type="nf-core",
                    source_url=f"https://github.com/{key}",
                    version="1.0.0",
                    default_params_json=defaults,
                    input_schema_json=schema if schema is not None else {"absent": True},
                    input_schema_version="1.0.0",
                    enabled=True,
                )
            )

    project = Project(organization_id=org_id, name="Our Project")
    second_project = Project(organization_id=org_id, name="Our Other Project")
    foreign_project = Project(organization_id=other.id, name="THEIR SECRET PROJECT")
    session.add_all([project, second_project, foreign_project])
    await session.flush()

    exp = Experiment(
        name="Our Exp",
        organization_id=org_id,
        project_id=project.id,
        status="fastq_uploaded",
        owner_user_id=admin_user.id,
    )
    foreign_exp = Experiment(
        name="Their Exp", organization_id=other.id, status="fastq_uploaded", owner_user_id=other_user.id
    )
    session.add_all([exp, foreign_exp])
    await session.flush()

    ours = await _sample_with_files(session, org_id, exp, "OURS_A", "ours")
    theirs = await _sample_with_files(session, other.id, foreign_exp, "THEIRS_SECRET", "theirs")
    # A sample of theirs with no files, so a preflight of their experiment would be blocked.
    bare = Sample(experiment_id=foreign_exp.id, external_id="THEIRS_BARE", organism="Homo sapiens")
    session.add(bare)

    our_run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/demo",
        status="completed",
        work_dir="/tmp/ours",
    )
    their_run = PipelineRun(
        organization_id=other.id,
        experiment_id=foreign_exp.id,
        submitted_by_user_id=other_user.id,
        pipeline_name="THEIR PIPELINE",
        status="completed",
        work_dir="/tmp/theirs",
    )
    session.add_all([our_run, their_run])
    await session.flush()
    await session.commit()
    return {
        "org_id": org_id,
        "other": other,
        "other_user": other_user,
        "exp": exp,
        "foreign_exp": foreign_exp,
        "project": project,
        "second_project": second_project,
        "foreign_project": foreign_project,
        "ours": ours,
        "theirs": theirs,
        "our_run": our_run,
        "their_run": their_run,
    }


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _run_count(session) -> int:
    return (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one()


def _capturing_adapter(captured: list):
    async def submit(job_spec):
        captured.append(job_spec)
        return _job_submit_result_from_dict({"job_id": "bioaf-pipeline-test", "namespace": "ns", "status": "queued"})

    adapter = MagicMock()
    adapter.submit_job = AsyncMock(side_effect=submit)
    return adapter


async def _launch(client, token, body, captured: list):
    with (
        patch("app.services.pipeline_run_service.get_compute_adapter", return_value=_capturing_adapter(captured)),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        return await client.post(LAUNCH, json=body, headers=_auth(token))


class TestPreflightRefusesForeignExperiments:
    @pytest.mark.asyncio
    async def test_a_foreign_experiment_is_not_found(self, client, admin_token, world):
        r = await client.post(
            PREFLIGHT,
            json={"pipeline_key": "nf-core/demo", "experiment_id": world["foreign_exp"].id},
            headers=_auth(admin_token),
        )

        assert r.status_code == 404
        assert r.json()["code"] == "not_found"
        assert "THEIRS" not in r.text
        assert "gs://theirs" not in r.text

    @pytest.mark.asyncio
    async def test_a_missing_experiment_reads_the_same_as_a_foreign_one(self, client, admin_token, world):
        foreign = await client.post(
            PREFLIGHT,
            json={"pipeline_key": "nf-core/demo", "experiment_id": world["foreign_exp"].id},
            headers=_auth(admin_token),
        )
        missing = await client.post(
            PREFLIGHT,
            json={"pipeline_key": "nf-core/demo", "experiment_id": 999999},
            headers=_auth(admin_token),
        )

        assert missing.status_code == 404
        assert foreign.json()["detail"].replace(str(world["foreign_exp"].id), "N") == missing.json()["detail"].replace(
            "999999", "N"
        )

    @pytest.mark.asyncio
    async def test_a_foreign_experiment_that_would_be_blocked_still_reveals_nothing(self, client, admin_token, world):
        """A blocked preview used to be returned whole, naming the samples that lacked files."""
        r = await client.post(
            PREFLIGHT,
            json={
                "pipeline_key": "nf-core/demo",
                "experiment_id": world["foreign_exp"].id,
                "sample_ids": [world["theirs"].id],
                "drop_samples_without_files": False,
            },
            headers=_auth(admin_token),
        )

        assert r.status_code == 404
        assert "THEIRS" not in r.text


class TestPreflightRefusesForeignReferences:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["foreign", "missing", "zero"])
    async def test_an_explicit_project_must_be_ours(self, client, admin_token, world, which):
        project_id = {"foreign": world["foreign_project"].id, "missing": 999999, "zero": 0}[which]
        r = await client.post(
            PREFLIGHT,
            json={"pipeline_key": "nf-core/demo", "experiment_id": world["exp"].id, "project_id": project_id},
            headers=_auth(admin_token),
        )

        assert r.status_code == 404
        assert r.json()["code"] == "not_found"
        assert "samplesheet" not in r.json()
        assert "THEIR SECRET PROJECT" not in r.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["foreign", "missing", "zero"])
    async def test_an_explicit_resume_parent_must_be_ours(self, client, admin_token, world, which):
        run_id = {"foreign": world["their_run"].id, "missing": 999999, "zero": 0}[which]
        r = await client.post(
            PREFLIGHT,
            json={"pipeline_key": "nf-core/demo", "experiment_id": world["exp"].id, "resume_from_run_id": run_id},
            headers=_auth(admin_token),
        )

        assert r.status_code == 404
        assert r.json()["code"] == "not_found"
        assert "THEIR PIPELINE" not in r.text

    @pytest.mark.asyncio
    async def test_our_own_references_are_accepted_even_across_projects(self, client, admin_token, world):
        """The experiment sits in one project; a run may still be filed under another of ours."""
        r = await client.post(
            PREFLIGHT,
            json={
                "pipeline_key": "nf-core/demo",
                "experiment_id": world["exp"].id,
                "project_id": world["second_project"].id,
                "resume_from_run_id": world["our_run"].id,
            },
            headers=_auth(admin_token),
        )

        assert r.status_code == 200
        assert r.json()["can_launch"] is True


class TestPreflightSampleSelection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["foreign", "missing"])
    async def test_a_selection_outside_the_experiment_is_refused_without_a_partial_preview(
        self, client, admin_token, world, which
    ):
        stray = world["theirs"].id if which == "foreign" else 999999
        r = await client.post(
            PREFLIGHT,
            json={
                "pipeline_key": "nf-core/demo",
                "experiment_id": world["exp"].id,
                "sample_ids": [world["ours"].id, stray],
            },
            headers=_auth(admin_token),
        )

        assert r.status_code == 200
        body = r.json()
        assert body["can_launch"] is False
        assert body["code"] == "validation_error"
        assert body["reason"]
        # Not a preview of the valid subset dressed up as the whole selection.
        assert body["samplesheet"]["rows"] == []
        assert body["samplesheet"]["columns"] == []
        assert body["samplesheet"]["csv"] == ""
        assert "OURS_A" not in r.text
        assert "THEIRS" not in r.text


class TestLinkedFilesMustBeOurs:
    @pytest_asyncio.fixture
    async def stale_link(self, session, world):
        """A file of theirs linked to a sample of ours, as a historical association would be."""
        f = File(
            organization_id=world["other"].id,
            gcs_uri="gs://theirs/LEAKED_R1_001.fastq.gz",
            filename="LEAKED_R1_001.fastq.gz",
            file_type="leaked_type",
            source_type="upload",
        )
        derived = File(
            organization_id=world["other"].id,
            gcs_uri="gs://theirs/LEAKED_DERIVED.bam",
            filename="LEAKED_DERIVED.bam",
            file_type="leaked_derived_type",
            source_type="pipeline_output",
        )
        session.add_all([f, derived])
        await session.flush()
        for linked in (f, derived):
            await session.execute(sample_files.insert().values(sample_id=world["ours"].id, file_id=linked.id))
        await session.commit()
        return {"file": f, "derived": derived}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("include_derived", [False, True])
    async def test_a_foreign_file_enters_neither_the_preview_nor_the_declaration(
        self, client, admin_token, world, stale_link, include_derived
    ):
        r = await client.post(
            PREFLIGHT,
            json={
                "pipeline_key": "nf-core/demo",
                "experiment_id": world["exp"].id,
                "include_derived_inputs": include_derived,
            },
            headers=_auth(admin_token),
        )

        assert r.status_code == 200
        assert "LEAKED" not in r.text
        assert "leaked" not in r.json()["declaration"]["file_types"]
        assert r.json()["declaration"]["file_types"] == ["fastq"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("include_derived", [False, True])
    async def test_a_foreign_file_is_neither_submitted_nor_recorded(
        self, client, session, admin_token, world, stale_link, include_derived
    ):
        captured: list = []
        r = await _launch(
            client,
            admin_token,
            {
                "pipeline_key": "nf-core/demo",
                "experiment_id": world["exp"].id,
                "include_derived_inputs": include_derived,
            },
            captured,
        )

        assert r.status_code == 200, r.text
        assert "LEAKED" not in captured[0].sample_sheet
        recorded = set(
            (
                await session.execute(
                    select(PipelineRunInputFile.file_id).where(PipelineRunInputFile.pipeline_run_id == r.json()["id"])
                )
            )
            .scalars()
            .all()
        )
        assert stale_link["file"].id not in recorded
        assert stale_link["derived"].id not in recorded
        assert len(recorded) == 2


class TestLaunchRefusesForeignReferences:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["foreign", "missing"])
    async def test_a_foreign_experiment_is_refused_with_400(self, client, session, admin_token, world, which):
        before = await _run_count(session)
        experiment_id = world["foreign_exp"].id if which == "foreign" else 999999
        captured: list = []
        r = await _launch(
            client, admin_token, {"pipeline_key": "nf-core/demo", "experiment_id": experiment_id}, captured
        )

        assert r.status_code == 400
        assert r.json()["detail"].replace(str(experiment_id), "N") == "Experiment N not found"
        assert captured == []
        assert await _run_count(session) == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["foreign", "missing", "zero"])
    async def test_an_explicit_project_must_be_ours(self, client, session, admin_token, world, which):
        project_id = {"foreign": world["foreign_project"].id, "missing": 999999, "zero": 0}[which]
        before = await _run_count(session)
        captured: list = []
        r = await _launch(
            client,
            admin_token,
            {"pipeline_key": "nf-core/demo", "experiment_id": world["exp"].id, "project_id": project_id},
            captured,
        )

        assert r.status_code == 400
        assert r.json()["code"] == "validation_error"
        assert r.json()["detail"].replace(str(project_id), "N") == "Project N not found"
        assert captured == []
        assert await _run_count(session) == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["foreign", "missing", "zero"])
    async def test_an_explicit_resume_parent_must_be_ours(self, client, session, admin_token, world, which):
        run_id = {"foreign": world["their_run"].id, "missing": 999999, "zero": 0}[which]
        before = await _run_count(session)
        captured: list = []
        r = await _launch(
            client,
            admin_token,
            {"pipeline_key": "nf-core/demo", "experiment_id": world["exp"].id, "resume_from_run_id": run_id},
            captured,
        )

        assert r.status_code == 400
        assert r.json()["detail"].replace(str(run_id), "N") == "Pipeline run N not found"
        assert captured == []
        assert await _run_count(session) == before

    @pytest.mark.asyncio
    async def test_a_foreign_sample_is_refused_and_nothing_changes(self, client, session, admin_token, world):
        before = await _run_count(session)
        captured: list = []
        r = await _launch(
            client,
            admin_token,
            {
                "pipeline_key": "nf-core/demo",
                "experiment_id": world["exp"].id,
                "sample_ids": [world["ours"].id, world["theirs"].id],
            },
            captured,
        )

        assert r.status_code == 400
        assert "THEIRS" not in r.text
        assert captured == []
        assert await _run_count(session) == before

    @pytest.mark.asyncio
    async def test_our_own_project_and_parent_are_recorded_and_submitted(self, client, session, admin_token, world):
        captured: list = []
        r = await _launch(
            client,
            admin_token,
            {
                "pipeline_key": "nf-core/demo",
                "experiment_id": world["exp"].id,
                "project_id": world["second_project"].id,
                "resume_from_run_id": world["our_run"].id,
            },
            captured,
        )

        assert r.status_code == 200, r.text
        assert r.json()["project_id"] == world["second_project"].id
        assert r.json()["resume_from_run_id"] == world["our_run"].id
        assert captured[0].resume_from_run_id == world["our_run"].id


class TestPreviewAndLaunchResolveTheSameParameters:
    @pytest.mark.asyncio
    async def test_a_catalog_default_reaches_the_preview_as_it_reaches_the_launch(self, client, admin_token, world):
        """The catalog says strandedness=reverse. Preflight used to show 'auto'."""
        body = {"pipeline_key": "nf-core/rnaseq", "experiment_id": world["exp"].id, "parameters": {}}
        preview = await client.post(PREFLIGHT, json=body, headers=_auth(admin_token))
        captured: list = []
        launched = await _launch(client, admin_token, body, captured)

        assert launched.status_code == 200, launched.text
        assert preview.json()["samplesheet"]["csv"] == captured[0].sample_sheet
        assert ",reverse" in captured[0].sample_sheet

    @pytest.mark.asyncio
    async def test_a_stated_value_wins_in_both(self, client, admin_token, world):
        body = {
            "pipeline_key": "nf-core/rnaseq",
            "experiment_id": world["exp"].id,
            "parameters": {"strandedness": "forward"},
        }
        preview = await client.post(PREFLIGHT, json=body, headers=_auth(admin_token))
        captured: list = []
        await _launch(client, admin_token, body, captured)

        assert preview.json()["samplesheet"]["csv"] == captured[0].sample_sheet
        assert ",forward" in captured[0].sample_sheet

    @pytest.mark.asyncio
    async def test_a_parameter_refusal_is_a_preflight_verdict(self, client, admin_token, world):
        """Resolution can refuse (mixed 10x chemistries, an unservable pinned reference). Preflight
        reports that as the launch would, rather than previewing a sheet that cannot run."""
        from app.services.pipeline_run_service import PipelineRunService
        from app.exceptions import ValidationError

        def refuse(*_args, **_kwargs):
            raise ValidationError("these samples cannot share one protocol")

        with patch.object(PipelineRunService, "resolve_parameters", side_effect=refuse):
            r = await client.post(
                PREFLIGHT,
                json={"pipeline_key": "nf-core/rnaseq", "experiment_id": world["exp"].id},
                headers=_auth(admin_token),
            )

        assert r.status_code == 200
        assert r.json()["can_launch"] is False
        assert r.json()["reason"] == "these samples cannot share one protocol"


class TestSavedDesignResolution:
    @pytest.mark.asyncio
    async def test_a_foreign_experiment_does_not_lead_to_a_project_design(self, session, world):
        """Resolution read the experiment's project by bare id. A design filed (wrongly) in our
        organization against their project must not be reached through their experiment."""
        from app.models.samplesheet_mapping import SamplesheetMapping
        from app.services.samplesheet_mapping_service import SamplesheetMappingService

        world["foreign_exp"].project_id = world["foreign_project"].id
        session.add(
            SamplesheetMapping(
                organization_id=world["org_id"],
                pipeline_key="nf-core/demo",
                scope="project",
                project_id=world["foreign_project"].id,
                values_json={},
            )
        )
        await session.commit()

        mapping, scope = await SamplesheetMappingService.resolve(
            session, world["org_id"], "nf-core/demo", world["foreign_exp"].id
        )

        assert scope != "project"
