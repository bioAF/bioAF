"""AWS compute plans and applies run the EKS endpoint guardrails (plan 04C) through the executor.

The Terraform CLI and AWS lookups are faked; the executor, the Terraform seam and platform_config
are real. GCP compute plans are untouched (covered by the existing executor tests).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.platform.platform_config_service import PlatformConfigService
from app.services.terraform_executor import TerraformExecutor
from tests.support.terraform_cli import terraform_cli
from tests.test_terraform_executor import (
    _make_apply_output,
    _make_plan_output,
    _mock_async_process,
    _mock_subprocess_run,
    _patch_work_dir,
    _seed_user,
)

ARN = "arn:aws:eks:us-west-1:123456789012:cluster/bioaf-acme-c0ffee"
NAME = "bioaf-acme-c0ffee"


async def _seed_aws(session, **extra):
    values = {
        "cloud_provider": "aws",
        "aws_region": "us-west-1",
        "aws_account_id": "123456789012",
        "org_slug": "acme",
        "compute_stack_uid": "c0ffee",
        "aws_app_vpc_id": "vpc-app",
        "aws_app_security_group_id": "sg-app",
        "terraform_initialized": "true",
        "terraform_state_bucket": "bioaf-tfstate-123456789012-us-west-1",
        **extra,
    }
    for key, value in values.items():
        await PlatformConfigService.set(session, key, value)
    await session.commit()


def _state(public=True, cidrs=("0.0.0.0/0",)):
    return {
        "resources": [
            {
                "mode": "managed",
                "type": "aws_eks_cluster",
                "name": "this",
                "instances": [
                    {
                        "attributes": {
                            "arn": ARN,
                            "name": NAME,
                            "created_at": "2026-06-18T10:00:00Z",
                            "vpc_config": [
                                {
                                    "endpoint_public_access": public,
                                    "endpoint_private_access": True,
                                    "public_access_cidrs": list(cidrs),
                                }
                            ],
                        }
                    }
                ],
            }
        ]
    }


def _live(public=True, cidrs=("0.0.0.0/0",)):
    return {
        "arn": ARN,
        "name": NAME,
        "createdAt": datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc),
        "resourcesVpcConfig": {
            "endpointPublicAccess": public,
            "endpointPrivateAccess": True,
            "publicAccessCidrs": list(cidrs),
        },
    }


def _show(actions, before, after):
    def values(endpoint):
        public, cidrs = endpoint
        return {
            "vpc_config": [
                {"endpoint_public_access": public, "endpoint_private_access": True, "public_access_cidrs": cidrs}
            ]
        }

    return json.dumps(
        {
            "format_version": "1.2",
            "resource_changes": [
                {
                    "address": "aws_eks_cluster.this",
                    "type": "aws_eks_cluster",
                    "name": "this",
                    "change": {
                        "actions": actions,
                        "before": values(before) if before else None,
                        "after": values(after) if after else None,
                    },
                }
            ],
        }
    )


def _cli(state, show):
    def run(cmd, **kwargs):
        if cmd[:3] == ["terraform", "state", "pull"]:
            return _mock_subprocess_run(json.dumps(state) if state is not None else "")
        if "show" in cmd:
            return _mock_subprocess_run(show)
        return _mock_subprocess_run(_make_plan_output(1))

    return terraform_cli(side_effect=run)


def _aws(live=None):
    return (
        patch("app.adapters.eks_control_plane.aws.describe_cluster", return_value=live or _live()),
        patch("app.adapters.eks_control_plane.aws.log_group_exists", return_value=False),
    )


async def _plan(session, user_id, state, show, live=None):
    describe, logs = _aws(live)
    with describe, logs, _cli(state, show), _patch_work_dir() as work_dir:
        run = await TerraformExecutor.run_plan(session=session, user_id=user_id, module_name="compute")
        written = json.loads((Path(work_dir) / "terraform.tfvars.json").read_text())
    return run, written


@pytest.mark.asyncio
async def test_an_upgraded_existing_cluster_plans_with_its_exact_legacy_endpoint(session):
    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true", gke_cluster_name=NAME)

    unchanged = (True, ["0.0.0.0/0"])
    run, written = await _plan(session, user_id, _state(), _show(["update"], unchanged, unchanged))

    assert run.status == "awaiting_confirmation", run.error_message
    for tfvars in (written, run.tfvars_json):
        assert tfvars["endpoint_public_access"] is True
        assert tfvars["endpoint_private_access"] is True
        assert tfvars["public_access_cidrs"] == ["0.0.0.0/0"]
        assert tfvars["control_plane_log_types"] == ["api", "audit", "authenticator", "controllerManager", "scheduler"]
        assert tfvars["control_plane_log_retention_days"] == 30
    assert await PlatformConfigService.get(session, "aws_eks_endpoint_mode") == "preserve_existing"


@pytest.mark.asyncio
async def test_a_plan_that_would_move_a_preserved_endpoint_fails(session):
    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true")

    run, _ = await _plan(session, user_id, _state(), _show(["update"], (True, ["0.0.0.0/0"]), (False, [])))

    assert run.status == "failed"
    assert "endpoint" in run.error_message


@pytest.mark.asyncio
async def test_a_plan_that_would_replace_the_cluster_fails(session):
    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true")

    run, _ = await _plan(
        session, user_id, _state(), _show(["delete", "create"], (True, ["0.0.0.0/0"]), (True, ["0.0.0.0/0"]))
    )

    assert run.status == "failed"
    assert "replace" in run.error_message


@pytest.mark.asyncio
async def test_a_fresh_compute_deploy_plans_private_only(session):
    user_id = await _seed_user(session)
    await _seed_aws(session)

    run, written = await _plan(session, user_id, {"resources": []}, _show(["create"], None, (False, [])))

    assert run.status == "awaiting_confirmation", run.error_message
    assert written["endpoint_public_access"] is False and written["endpoint_private_access"] is True


@pytest.mark.asyncio
async def test_an_apply_is_refused_when_the_preserved_endpoint_drifted_after_the_plan(session):
    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true")
    unchanged = (True, ["0.0.0.0/0"])
    run, _ = await _plan(session, user_id, _state(), _show(["update"], unchanged, unchanged))
    assert run.status == "awaiting_confirmation", run.error_message

    describe, logs = _aws(_live(public=False, cidrs=()))
    events = []
    with (
        describe,
        logs,
        _cli(_state(), "{}"),
        _patch_work_dir(),
        patch("asyncio.create_subprocess_exec") as spawn,
    ):
        async for event in TerraformExecutor.run_apply(session=session, run_id=run.id, user_id=user_id):
            events.append(event)

    spawn.assert_not_called()
    assert any(e.event_type == "apply_error" and "changed" in (e.message or "") for e in events)


@pytest.mark.asyncio
async def test_a_reviewed_cutover_commits_after_a_successful_apply(session):
    from app.services import eks_endpoint

    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true", gke_cluster_name=NAME)
    unchanged = (True, ["0.0.0.0/0"])
    await _plan(session, user_id, _state(), _show(["update"], unchanged, unchanged))
    describe, logs = _aws()
    with describe, logs:
        await eks_endpoint.request_cutover(
            session, mode="private_only", public_access_cidrs=[], user_id=user_id, acknowledged=True
        )
    await session.commit()

    run, written = await _plan(session, user_id, _state(), _show(["update"], unchanged, (False, [])))
    assert run.status == "awaiting_confirmation", run.error_message
    assert written["endpoint_public_access"] is False

    # prepare_run reads the live cluster before the apply; finish_apply reads it back afterwards.
    _, logs = _aws()
    with (
        patch(
            "app.adapters.eks_control_plane.aws.describe_cluster",
            side_effect=[_live(), _live(public=False, cidrs=())],
        ),
        logs,
        _cli(_state(), "{}"),
        _patch_work_dir(),
        patch("asyncio.create_subprocess_exec", return_value=_mock_async_process(_make_apply_output(1))),
    ):
        async for _ in TerraformExecutor.run_apply(session=session, run_id=run.id, user_id=user_id):
            pass

    assert await PlatformConfigService.get(session, "aws_eks_endpoint_mode") == "private_only"
    assert not await PlatformConfigService.get(session, "aws_eks_endpoint_cutover")


# --- The apply is the reviewed plan (review finding: fresh auto-approved apply) --------------


async def _reviewed_update(session, user_id):
    unchanged = (True, ["0.0.0.0/0"])
    run, _ = await _plan(session, user_id, _state(), _show(["update"], unchanged, unchanged))
    assert run.status == "awaiting_confirmation", run.error_message
    return run


async def _apply(session, user_id, run, show_at_apply):
    describe, logs = _aws()
    events = []
    with (
        describe,
        logs,
        _cli(_state(), show_at_apply),
        _patch_work_dir(),
        patch("asyncio.create_subprocess_exec", return_value=_mock_async_process(_make_apply_output(1))) as spawn,
    ):
        async for event in TerraformExecutor.run_apply(session=session, run_id=run.id, user_id=user_id):
            events.append(event)
    return spawn, events


@pytest.mark.asyncio
async def test_an_apply_whose_fresh_plan_would_replace_the_cluster_is_refused(session):
    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true", gke_cluster_name=NAME)
    run = await _reviewed_update(session, user_id)

    replace = _show(["delete", "create"], (True, ["0.0.0.0/0"]), (True, ["0.0.0.0/0"]))
    spawn, events = await _apply(session, user_id, run, replace)

    spawn.assert_not_called()
    assert any(e.event_type == "apply_error" for e in events)


@pytest.mark.asyncio
async def test_an_apply_with_changes_the_review_did_not_contain_is_refused(session):
    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true", gke_cluster_name=NAME)
    run = await _reviewed_update(session, user_id)

    extra = json.loads(_show(["update"], (True, ["0.0.0.0/0"]), (True, ["0.0.0.0/0"])))
    extra["resource_changes"].append(
        {
            "address": "aws_vpc.this",
            "type": "aws_vpc",
            "name": "this",
            "change": {"actions": ["delete"], "before": {}, "after": None},
        }
    )
    spawn, events = await _apply(session, user_id, run, json.dumps(extra))

    spawn.assert_not_called()
    assert any(e.event_type == "apply_error" and "review" in (e.message or "") for e in events)


@pytest.mark.asyncio
async def test_an_apply_after_naming_inputs_changed_is_refused(session):
    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true", gke_cluster_name=NAME)
    run = await _reviewed_update(session, user_id)
    await PlatformConfigService.set(session, "org_slug", "other")
    await session.commit()

    unchanged = _show(["update"], (True, ["0.0.0.0/0"]), (True, ["0.0.0.0/0"]))
    spawn, events = await _apply(session, user_id, run, unchanged)

    spawn.assert_not_called()
    assert any(e.event_type == "apply_error" and "org_slug" in (e.message or "") for e in events)


@pytest.mark.asyncio
async def test_a_matching_apply_runs_the_checked_plan_file(session):
    user_id = await _seed_user(session)
    await _seed_aws(session, compute_deployed="true", gke_cluster_name=NAME)
    run = await _reviewed_update(session, user_id)

    unchanged = _show(["update"], (True, ["0.0.0.0/0"]), (True, ["0.0.0.0/0"]))
    spawn, _ = await _apply(session, user_id, run, unchanged)

    args = spawn.call_args.args
    assert args[:2] == ("terraform", "apply") and args[-1] == "tfplan"
    assert not any(str(a).startswith("-target") for a in args)
