"""The author's actual source, operation identity and observed result own execution claims."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.validation_code_followup import code_followup


def evidence(text="print(42)\n"):
    return {
        "code_inspection": {"sources": [{"path": "analysis.py", "language": "python", "text": text}]},
        "capabilities": {"preprocessed_data": {"value": "yes"}},
    }


def test_paper_wide_data_availability_does_not_bind_inputs_to_code():
    row = code_followup(
        evidence=evidence(),
        plan={"reported_experiments": [{"id": "e1", "assay": "RNA-seq"}, {"id": "e2", "assay": "MPRA"}]},
        route="deposit",
    )["followups"][0]
    assert row["action"] == "attempt_bounded_check"
    assert row["inputs"]["processed_available"] is False
    assert "RNA-seq; MPRA" not in row["claimed_analysis"]


def test_fetched_code_contract_invokes_author_entry_under_enforced_network_policy():
    from app.adapters.notebooks.kubernetes import build_fetched_code_script

    script = build_fetched_code_script(
        {
            "code_uri": "gs://test/held.tar.gz",
            "entry_point": "analysis.py",
            "arguments": "",
            "execution_contract": {
                "language": "python",
                "network": "install_only",
                "working_directory": "/work",
                "filesystem": "read-only inputs; writable /work, /tmp and /outputs",
            },
        },
        copy_in="cp {uri} {local}",
        auth="true",
        timeout_seconds=900,
    )
    assert "bwrap" in script and "--unshare-net" in script
    assert "analysis.py" in script
    assert "BIOAF_AUTHOR_EXIT" in script
    assert "BIOAF_SANDBOX_UNAVAILABLE" in script


@pytest.mark.parametrize(
    "transcript, expected",
    [
        ("BIOAF_SANDBOX_READY\n42\nBIOAF_AUTHOR_EXIT 0", "succeeded"),
        ("BIOAF_SANDBOX_UNAVAILABLE", "blocked"),
        ("BIOAF_SANDBOX_READY\nFileNotFoundError: counts.csv\nBIOAF_AUTHOR_EXIT 1", "blocked"),
        ("BIOAF_SANDBOX_READY\nNameError: undefined\nBIOAF_AUTHOR_EXIT 1", "failed"),
        ("", "inconclusive"),
    ],
)
def test_author_attempt_requires_observed_invocation(transcript, expected):
    from app.services.validation_code_followup import invocation_outcome

    found = invocation_outcome(transcript, compute_status="completed")
    assert found["status"] == expected
    assert found.get("reproduced") is not True


@pytest.mark.asyncio
async def test_assessment_launches_polls_and_reuses_the_actual_author_operation(monkeypatch):
    import io
    import tarfile
    from app.services import validation_assessment as assessment
    from app.services import validation_environment_check as environment
    from app.services.notebook_execution_service import NotebookExecutionService
    from app.services.validation_driver_service import ValidationDriverService

    study = SimpleNamespace(
        id=13,
        organization_id=1,
        requested_by_user_id=2,
        experiment_id=None,
        intended_route="deposit",
        evidence_json=evidence(),
    )
    session = SimpleNamespace(flush=AsyncMock(), commit=AsyncMock())
    compute = SimpleNamespace(id=17, status="running", provider_metadata={})
    storage = SimpleNamespace(build_uri=lambda bucket, path: f"gs://{bucket}/{path}", write_bytes=AsyncMock())
    identity = SimpleNamespace(bucket="isolated", prefix_for=lambda study_id: f"studies/{study_id}")
    monkeypatch.setattr(assessment, "active_plan", AsyncMock(return_value=None))
    monkeypatch.setattr(assessment.llm_provider_config_service, "get_for_feature", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: storage)
    execute = AsyncMock(return_value=compute)
    monkeypatch.setattr(NotebookExecutionService, "execute_fetched_code", execute)
    monkeypatch.setattr(environment, "_compute_session", AsyncMock(return_value=compute))
    monkeypatch.setattr(NotebookExecutionService, "poll_execution", AsyncMock(return_value=compute))
    monkeypatch.setattr(
        ValidationDriverService,
        "_read_code_outputs",
        AsyncMock(return_value=[{"path": "transcript.txt", "text": "BIOAF_SANDBOX_READY\n42\nBIOAF_AUTHOR_EXIT 0"}]),
    )
    first = await assessment.schedule_code_followup(session, study)
    row = first["followups"][0]
    assert row["session_id"] == 17 and row["operation_id"]
    assert first["pending"] is True
    assert execute.call_args.kwargs["entry_point"] == "analysis.py"
    with tarfile.open(fileobj=io.BytesIO(storage.write_bytes.call_args.args[1])) as archive:
        assert archive.extractfile("bundle/analysis.py").read() == b"print(42)\n"
    await assessment.schedule_code_followup(session, study)
    assert execute.await_count == 1
    compute.status = "completed"
    last = await assessment.schedule_code_followup(session, study)
    assert last["pending"] is False
    assert last["followups"][0]["outcome"]["status"] == "succeeded"
    assert last["followups"][0]["outcome"]["reproduced"] is False
    assert study.evidence_json["code_inspection"]["execution_by_unit"]["code:analysis.py"]["invocation"]["invoked"]
    await assessment.schedule_code_followup(session, study)
    assert execute.await_count == 1


def test_single_implementation_credits_only_the_revision_actually_invoked():
    from app.services.validation_code_followup import _digest
    from app.services.validation_rubric_evidence import assess_evidence

    held = evidence()
    inspection = held["code_inspection"]
    digest = _digest(inspection["sources"])
    inspection["execution_by_unit"] = {
        "code:analysis.py": {
            "source_digest": digest,
            "dependencies_digest": digest,
            "invocation": {"invoked": True, "status": "succeeded"},
        }
    }
    assert assess_evidence(evidence=held, plan={})["C1.B"]["outcome"] == "verified"
    inspection["sources"][0]["text"] = "raise ValueError('broken')\n"
    assert assess_evidence(evidence=held, plan={})["C1.B"]["outcome"] == "undetermined"
    assert not code_followup(evidence=held, plan={}, route="deposit")["followups"][0]["attempted"]


def test_legacy_environment_imports_do_not_become_author_execution_on_reassessment():
    from app.services.validation_rubric_evidence import assess_evidence

    held = evidence()
    held["code_inspection"]["execution"] = {"load": {"status": "succeeded", "ref": "old-import-check"}}
    assert assess_evidence(evidence=held, plan={})["C1.B"]["outcome"] == "undetermined"


@pytest.mark.parametrize("number, agrees", [(42, True), (84, False)])
def test_author_comparison_uses_only_the_bound_claim(number, agrees):
    from app.services.validation_code_followup import compare_author_outputs

    result = compare_author_outputs(
        [{"path": "result.json", "text": '{"peak_count":' + str(number) + "}"}],
        {"claims": [{"metric_key": "peak_count", "claimed_value": 42, "tolerance": 0.05}]},
        {},
        {},
    )
    assert len(result) == 1
    assert result[0]["within_tolerance"] is agrees


def test_advisory_comparison_does_not_claim_reproduction():
    from app.services.validation_code_followup import compare_author_outputs

    result = compare_author_outputs(
        [{"path": "result.json", "text": '{"peak_count":42}'}],
        {"claims": [{"metric_key": "peak_count", "claimed_value": 42, "unit": "consensus peaks"}]},
        {},
        {},
    )
    assert result[0]["advisory"] is True
    assert result[0]["within_tolerance"] is None


def test_author_result_does_not_leave_a_false_empty_results_summary():
    from app.services.validation_report_areas import areas_for

    results = next(
        a
        for a in areas_for(
            {
                "code_followup": {
                    "followups": [
                        {
                            "action": "attempt_reproduction",
                            "outcome": {
                                "invoked": True,
                                "comparisons": [
                                    {
                                        "metric_key": "gene_count",
                                        "within_tolerance": True,
                                    }
                                ],
                            },
                        }
                    ]
                }
            }
        )
        if a["key"] == "results"
    )
    assert results["reproduction"]["agreed"] is True
    assert "reproduced nothing" not in results["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("compatible", [True, False])
async def test_binding_uses_held_files_and_scopes_the_pre_run_comparison(monkeypatch, compatible):
    from app.services.validation_code_followup import bind_execution_inputs
    from tests.support.fake_llm import FakeClient
    from app.services import notebook_execution_service as executor

    held = evidence("import sys\nprint(sys.argv[1])\n")
    held["level3"] = {"input_file_ids": [7]}
    held["comparison_targets"] = [
        {"metric_key": "peak_count", "claimed_value": 42},
        {"metric_key": "unrelated", "claimed_value": 84},
    ]
    study = SimpleNamespace(evidence_json=held, organization_id=1, intended_route="deposit")
    files = [SimpleNamespace(id=7, storage_uri="gs://held/counts.tsv")]
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: files))),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(executor, "_resolve_input_file_context", AsyncMock(return_value={}))
    monkeypatch.setattr(executor, "_build_relative_path", lambda *_: "counts.tsv")
    client = FakeClient(
        [
            {
                "compatible": compatible,
                "experiment_id": "e1",
                "input_file_ids": [7],
                "arguments": ["/data/counts.tsv"],
                "reason": "analysis.py reads the held counts.tsv",
            }
        ]
    )
    plan = {"reported_experiments": [{"id": "e1", "assay": "ChIP-seq", "claim_indices": [0]}]}
    await bind_execution_inputs(session, study, plan, client=client, model="m", api_key=None)
    row = code_followup(evidence=study.evidence_json, plan=plan, route="deposit")["followups"][0]
    assert row["action"] == ("attempt_reproduction" if compatible else "attempt_bounded_check")
    if compatible:
        assert row["inputs"]["file_ids"] == [7]
        assert row["binding"]["claims"] == held["comparison_targets"][:1]
    await bind_execution_inputs(session, study, plan, client=client, model="m", api_key=None)
    assert len(client.asked) == 1


@pytest.mark.asyncio
async def test_failed_contract_execution_collects_its_transcript(monkeypatch):
    from app.adapters.models import ServiceState
    from app.services.notebook_execution_service import NotebookExecutionService
    from app.services import notebook_execution_service as executor

    compute = SimpleNamespace(
        status="running",
        compute_job_ref="pod",
        provider_namespace="isolated",
        provider_metadata={"execution_contract": {"language": "python"}},
    )
    adapter = SimpleNamespace(
        get_session_status=AsyncMock(
            return_value=SimpleNamespace(status=ServiceState.ERROR, provider_details={"message": "exit 1"})
        )
    )
    monkeypatch.setattr(executor, "get_notebook_adapter", lambda: adapter)
    collect = AsyncMock()
    monkeypatch.setattr(NotebookExecutionService, "_finalize_success", collect)
    await NotebookExecutionService.poll_execution(SimpleNamespace(flush=AsyncMock()), compute)
    collect.assert_awaited_once()
    assert compute.status == "failed"


@pytest.mark.asyncio
async def test_settled_author_attempt_refreshes_interpretation_under_the_same_budget(monkeypatch):
    from app.services import validation_assessment as assessment
    from app.services import validation_code_followup as followup
    from app.services.validation_driver_service import ValidationDriverService
    from app.services.validation_decision_budgets import ACTIVE_ASSESSMENT

    study = SimpleNamespace(
        id=1,
        state="classified",
        organization_id=1,
        evidence_json={
            "assessment_budget": {"requests": 4},
            "code_followup": {"pending": True, "followups": [{"status": "running"}]},
        },
    )
    session = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock())
    monkeypatch.setattr(assessment, "active_plan", AsyncMock(return_value=None))
    monkeypatch.setattr(assessment.llm_provider_config_service, "get_for_feature", AsyncMock(return_value=None))

    async def advance(*_, **__):
        study.evidence_json["code_followup"] = {"pending": False, "followups": [{"status": "settled"}]}
        return study.evidence_json["code_followup"]

    async def review(*_, **__):
        assert ACTIVE_ASSESSMENT.get().requests == 4
        return {}

    monkeypatch.setattr(followup, "advance_code_followups", advance)
    reviewer = AsyncMock(side_effect=review)
    monkeypatch.setattr(assessment, "refresh_interpretation_review", reviewer)
    monkeypatch.setattr(assessment, "publish_assessment", AsyncMock())
    synthesis = AsyncMock()
    monkeypatch.setattr(assessment, "refresh_report_synthesis", synthesis)
    assert await ValidationDriverService._advance_one(session, study)
    reviewer.assert_awaited_once()
    synthesis.assert_awaited_once()


@pytest.mark.asyncio
async def test_acquired_inputs_rebind_before_reproduction_can_finish(monkeypatch):
    from app.services import validation_assessment as assessment
    from app.services.validation_driver_service import ValidationDriverService
    from app.services.validation_study_service import ValidationStudyService

    study = SimpleNamespace(
        id=1,
        organization_id=1,
        requested_by_user_id=1,
        evidence_json={
            "level3": {"input_file_ids": [7]},
            "assessment_budget": {"requests": 3},
            "code_followup": {"version": 1, "pending": False, "followups": [{"status": "settled"}]},
        },
    )
    session = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock())
    rebind = AsyncMock(return_value={"version": 1, "pending": True, "followups": [{"status": "running"}]})
    transition = AsyncMock()
    monkeypatch.setattr(assessment, "schedule_code_followup", rebind)
    monkeypatch.setattr(ValidationStudyService, "transition", transition)
    assert await ValidationDriverService._handle_reproducing(session, study)
    assert study.evidence_json["code_followup"]["pending"]
    assert not await ValidationDriverService._handle_reproducing(session, study)
    transition.assert_not_awaited()
    study.evidence_json["code_followup"]["pending"] = False
    assert await ValidationDriverService._handle_reproducing(session, study)
    rebind.assert_awaited_once()
    assert transition.call_args.args[-1] == "comparing"


@pytest.mark.asyncio
async def test_executor_adopts_the_dispatched_operation_without_charging_quota_twice(session, admin_user, monkeypatch):
    from app.adapters.models import ServiceState, SessionInfo
    from app.services import notebook_execution_service as executor

    identity = SimpleNamespace(namespace="isolated", sa_email="isolated@example.org", bucket="isolated")
    monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", AsyncMock(return_value=identity))
    quota = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(executor.QuotaService, "check_quota", quota)
    monkeypatch.setattr(
        executor.PlatformConfigService, "get_many", AsyncMock(return_value={"bioaf_scrna_image": "test:revision"})
    )
    adapter = SimpleNamespace(
        launch_session=AsyncMock(
            return_value=SessionInfo(
                session_id="s1",
                status=ServiceState.RUNNING,
                provider_details={"pod_name": "p1", "namespace": "isolated"},
            )
        )
    )
    monkeypatch.setattr(executor, "get_notebook_adapter", lambda: adapter)
    args = dict(
        org_id=admin_user.organization_id,
        user_id=admin_user.id,
        code_uri="gs://isolated/code.tar.gz",
        entry_point="analysis.py",
        operation_key="author-test-one",
        execution_contract={"language": "python"},
    )
    first = await executor.NotebookExecutionService.execute_fetched_code(session, **args)
    quota.return_value = (False, "No new compute authorized")
    second = await executor.NotebookExecutionService.execute_fetched_code(session, **args)
    assert first.id == second.id
    quota.assert_awaited_once()
    adapter.launch_session.assert_awaited_once()
    assert first.provider_metadata is not None
    assert first.provider_metadata["execution_contract"]["image"] == "test:revision"
