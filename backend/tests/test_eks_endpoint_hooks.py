"""EKS endpoint lifecycle around AWS compute plans and applies (plan 04C).

Covers upgrade backfill into preserve_existing from verified remote state and the live cluster,
the pre-apply recheck, log-group adoption, the reviewed cutover and its reconciliation after a
failed or interrupted apply, and teardown. AWS lookups and ``terraform state pull`` are faked; the
platform_config rows are real.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.exceptions import ValidationError
from app.platform.platform_config_service import PlatformConfigService
from app.services import eks_endpoint
from app.services import eks_endpoint_policy as policy

ARN = "arn:aws:eks:us-west-1:123456789012:cluster/bioaf-acme-c0ffee"
NAME = "bioaf-acme-c0ffee"
CREATED = "2026-06-18T10:00:00Z"
BASE = {
    "cloud_provider": "aws",
    "aws_region": "us-west-1",
    "aws_account_id": "123456789012",
    "org_slug": "acme",
    "compute_stack_uid": "c0ffee",
    "aws_app_vpc_id": "vpc-app",
    "aws_app_security_group_id": "sg-app",
}


def _state(cluster=True, log_group=False, public=True, cidrs=("0.0.0.0/0",)):
    resources = []
    if cluster:
        resources.append(
            {
                "mode": "managed",
                "type": "aws_eks_cluster",
                "name": "this",
                "instances": [
                    {
                        "attributes": {
                            "arn": ARN,
                            "name": NAME,
                            "created_at": CREATED,
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
        )
    if log_group:
        resources.append(
            {"mode": "managed", "type": "aws_cloudwatch_log_group", "name": "control_plane", "instances": [{}]}
        )
    return {"resources": resources}


def _live(public=True, cidrs=("0.0.0.0/0",), arn=ARN):
    return {
        "arn": arn,
        "name": NAME,
        "createdAt": datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc),
        "resourcesVpcConfig": {
            "endpointPublicAccess": public,
            "endpointPrivateAccess": True,
            "publicAccessCidrs": list(cidrs),
        },
    }


async def _seed(session, **extra):
    for key, value in {**BASE, **extra}.items():
        await PlatformConfigService.set(session, key, value)
    await session.flush()


async def _config(session):
    keys = list(BASE) + list(policy.CONFIG_KEYS) + ["compute_deployed", "gke_cluster_name"]
    return await PlatformConfigService.get_many(session, keys)


def _fakes(live=None, log_group_exists=False, describe_error=None):
    def describe(name, region):
        if describe_error:
            raise describe_error
        return live or _live()

    return (
        patch("app.adapters.eks_control_plane.aws.describe_cluster", side_effect=describe),
        patch("app.adapters.eks_control_plane.aws.log_group_exists", return_value=log_group_exists),
    )


@pytest.fixture(autouse=True)
def _no_real_aws():
    """No test here may reach AWS: the adapter's client seam fails unless a test fakes the call."""

    def refuse(service, region):
        raise AssertionError(f"unexpected real AWS {service} call")

    with patch("app.adapters.eks_control_plane.aws._client", side_effect=refuse):
        yield


async def _request(session, mode="private_only", cidrs=(), acknowledged=True, **fake_kwargs):
    describe, logs = _fakes(**fake_kwargs)
    with describe, logs:
        return await eks_endpoint.request_cutover(
            session, mode=mode, public_access_cidrs=list(cidrs), user_id=1, acknowledged=acknowledged
        )


async def _prepare(session, state, phase="plan", run_id=1, **fake_kwargs):
    async def pull():
        return state

    describe, logs = _fakes(**fake_kwargs)
    with describe, logs:
        return await eks_endpoint.prepare_run(
            session, await _config(session), phase=phase, run_id=run_id, pull_state=pull
        )


# --- Upgrade backfill -------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_existing_cluster_is_recorded_as_preserve_existing_with_its_live_endpoint(session):
    await _seed(session, compute_deployed="true", gke_cluster_name=NAME)

    config = await _prepare(session, _state())

    recorded = await _config(session)
    assert recorded["aws_eks_endpoint_mode"] == "preserve_existing"
    snapshot = json.loads(recorded["aws_eks_endpoint_snapshot"])
    assert snapshot["cluster_arn"] == ARN and snapshot["public_access_cidrs"] == ["0.0.0.0/0"]
    resolved = config["_eks_resolved"]
    assert resolved["endpoint_public_access"] is True
    assert resolved["public_access_cidrs"] == ["0.0.0.0/0"]
    assert resolved["control_plane_log_retention_days"] == 30


@pytest.mark.asyncio
async def test_backfill_works_without_the_application_network(session):
    await _seed(session, compute_deployed="true", aws_app_vpc_id="", aws_app_security_group_id="")
    config = await _prepare(session, _state())
    assert config["_eks_resolved"]["endpoint_public_access"] is True


@pytest.mark.asyncio
async def test_a_mismatched_live_cluster_refuses_and_records_nothing(session):
    await _seed(session, compute_deployed="true")
    with pytest.raises(ValidationError, match="does not match"):
        await _prepare(session, _state(), live=_live(arn="arn:aws:eks:us-west-1:123456789012:cluster/other"))
    assert (await _config(session)).get("aws_eks_endpoint_mode") in (None, "")


@pytest.mark.asyncio
async def test_failed_live_discovery_refuses_the_compute_plan(session):
    await _seed(session, compute_deployed="true")
    with pytest.raises(ValidationError, match="could not read"):
        await _prepare(session, _state(), describe_error=RuntimeError("AccessDenied"))
    assert (await _config(session)).get("aws_eks_endpoint_mode") in (None, "")


@pytest.mark.asyncio
async def test_a_deployed_install_with_no_cluster_in_state_is_refused(session):
    await _seed(session, compute_deployed="true")
    with pytest.raises(ValidationError, match="state"):
        await _prepare(session, _state(cluster=False))


@pytest.mark.asyncio
async def test_unreadable_state_is_refused(session):
    await _seed(session, compute_deployed="true")
    with pytest.raises(ValidationError, match="state"):
        await _prepare(session, None)


# --- Fresh installs ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_fresh_install_records_and_applies_private_only(session):
    await _seed(session)
    config = await _prepare(session, {"resources": []})
    assert config["_eks_resolved"]["endpoint_public_access"] is False
    assert (await _config(session))["aws_eks_endpoint_mode"] == "private_only"


@pytest.mark.asyncio
async def test_a_fresh_private_only_install_without_the_application_network_is_refused(session):
    await _seed(session, aws_app_vpc_id="", aws_app_security_group_id="")
    with pytest.raises(ValidationError, match="application network"):
        await _prepare(session, {"resources": []})


@pytest.mark.asyncio
async def test_preserve_existing_cannot_redeploy_a_torn_down_cluster(session):
    await _seed(
        session,
        aws_eks_endpoint_mode="preserve_existing",
        aws_eks_endpoint_snapshot=json.dumps(
            {
                "cluster_arn": ARN,
                "endpoint_public_access": True,
                "endpoint_private_access": True,
                "public_access_cidrs": ["0.0.0.0/0"],
            }
        ),
    )
    with pytest.raises(ValidationError, match="existing cluster"):
        await _prepare(session, {"resources": []})


# --- Pre-apply recheck -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_preserved_endpoint_that_drifted_refuses_the_apply(session):
    await _seed(session, compute_deployed="true")
    await _prepare(session, _state())
    with pytest.raises(ValidationError, match="changed"):
        await _prepare(session, _state(), phase="apply", live=_live(public=False, cidrs=()))
    # The snapshot is not overwritten by the drifted value.
    assert json.loads((await _config(session))["aws_eks_endpoint_snapshot"])["endpoint_public_access"] is True


# --- Log group adoption -------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_existing_log_group_outside_state_is_adopted(session):
    await _seed(session, compute_deployed="true")
    config = await _prepare(session, _state(), log_group_exists=True)
    assert config["_eks_resolved"]["adopt_control_plane_log_group"] is True


@pytest.mark.asyncio
async def test_a_log_group_already_in_state_is_not_imported_again(session):
    await _seed(session, compute_deployed="true")
    config = await _prepare(session, _state(log_group=True), log_group_exists=True)
    assert config["_eks_resolved"].get("adopt_control_plane_log_group", False) is False


# --- Reviewed cutover -----------------------------------------------------------------


async def _preserved(session):
    await _seed(session, compute_deployed="true", gke_cluster_name=NAME)
    await _prepare(session, _state())


@pytest.mark.asyncio
async def test_a_cutover_request_validates_its_target_and_is_bound_to_the_snapshot(session):
    await _preserved(session)
    with pytest.raises(ValidationError):
        await _request(session, mode="public_restricted", cidrs=["0.0.0.0/0"])
    with pytest.raises(ValidationError, match="recovery"):
        await _request(session, acknowledged=False)
    with pytest.raises(ValidationError, match="mode"):
        await _request(session, mode="preserve_existing")
    with pytest.raises(ValidationError, match="changed"):
        await _request(session, live=_live(public=False, cidrs=()))

    cutover = await _request(session)
    assert cutover["status"] == "pending"
    assert cutover["snapshot"]["cluster_arn"] == ARN
    # The committed mode is unchanged until the cutover applies.
    assert (await _config(session))["aws_eks_endpoint_mode"] == "preserve_existing"


@pytest.mark.asyncio
async def test_a_successful_cutover_commits_the_new_mode_after_readback(session):
    await _preserved(session)
    await _request(session)

    planned = await _prepare(session, _state(), run_id=7)
    assert planned["_eks_resolved"]["endpoint_public_access"] is False

    applied = await _prepare(session, _state(), phase="apply", run_id=7)
    assert json.loads((await _config(session))["aws_eks_endpoint_cutover"])["status"] == "applying"

    describe, logs = _fakes(live=_live(public=False, cidrs=()))
    with describe, logs:
        await eks_endpoint.finish_apply(session, applied, run_id=7, succeeded=True)

    recorded = await _config(session)
    assert recorded["aws_eks_endpoint_mode"] == "private_only"
    assert recorded.get("aws_eks_endpoint_cutover") in (None, "")
    assert json.loads(recorded["aws_eks_endpoint_snapshot"])["endpoint_public_access"] is False


@pytest.mark.asyncio
async def test_a_cutover_can_only_be_applied_from_its_own_plan(session):
    await _preserved(session)
    await _request(session)
    await _prepare(session, _state(), run_id=7)
    with pytest.raises(ValidationError, match="plan"):
        await _prepare(session, _state(), phase="apply", run_id=8)


@pytest.mark.asyncio
async def test_a_failed_cutover_must_be_reconciled_and_never_reverts_the_mode(session):
    await _preserved(session)
    await _request(session)
    await _prepare(session, _state(), run_id=7)
    applied = await _prepare(session, _state(), phase="apply", run_id=7)
    describe, logs = _fakes()
    with describe, logs:
        await eks_endpoint.finish_apply(session, applied, run_id=7, succeeded=False)
    assert json.loads((await _config(session))["aws_eks_endpoint_cutover"])["status"] == "needs_reconcile"

    # The live endpoint is neither the old snapshot nor the target: refuse, change nothing.
    with pytest.raises(ValidationError, match="reconcile"):
        await _prepare(session, _state(), run_id=9, live=_live(public=True, cidrs=("203.0.113.9/32",)))
    assert (await _config(session))["aws_eks_endpoint_mode"] == "preserve_existing"

    # Unchanged live endpoint: the cutover is still pending and can be planned again.
    config = await _prepare(session, _state(), run_id=10)
    assert config["_eks_resolved"]["endpoint_public_access"] is False
    assert json.loads((await _config(session))["aws_eks_endpoint_cutover"])["status"] == "pending"


@pytest.mark.asyncio
async def test_an_interrupted_cutover_that_reached_its_target_is_committed_on_the_next_plan(session):
    await _preserved(session)
    await _request(session)
    await _prepare(session, _state(), run_id=7)
    await _prepare(session, _state(), phase="apply", run_id=7)
    # No finish_apply: the backend restarted mid-apply. The next plan sees the target live.
    await _prepare(session, _state(public=False, cidrs=()), run_id=9, live=_live(public=False, cidrs=()))
    recorded = await _config(session)
    assert recorded["aws_eks_endpoint_mode"] == "private_only"
    assert recorded.get("aws_eks_endpoint_cutover") in (None, "")


@pytest.mark.asyncio
async def test_a_pending_cutover_can_be_cancelled_only_while_the_endpoint_is_unchanged(session):
    await _preserved(session)
    await _request(session)
    describe, logs = _fakes(live=_live(public=False, cidrs=()))
    with describe, logs, pytest.raises(ValidationError, match="changed"):
        await eks_endpoint.cancel_cutover(session)
    describe, logs = _fakes()
    with describe, logs:
        await eks_endpoint.cancel_cutover(session)
    assert (await _config(session)).get("aws_eks_endpoint_cutover") in (None, "")


# --- Teardown ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown_forgets_the_preserved_cluster_but_keeps_a_hardened_choice(session):
    await _preserved(session)
    await eks_endpoint.finish_destroy(session)
    recorded = await _config(session)
    assert recorded.get("aws_eks_endpoint_mode") in (None, "")
    assert recorded.get("aws_eks_endpoint_snapshot") in (None, "")

    await _seed(session, aws_eks_endpoint_mode="public_restricted", aws_eks_public_access_cidrs='["203.0.113.9/32"]')
    await eks_endpoint.finish_destroy(session)
    assert (await _config(session))["aws_eks_endpoint_mode"] == "public_restricted"


@pytest.mark.asyncio
async def test_status_reports_preserved_unrestricted_access_as_pending_hardening(session):
    await _preserved(session)
    status = await eks_endpoint.current_status(session)
    assert status["mode"] == "preserve_existing"
    assert status["pending_hardening"] is True
    rows = (await session.execute(text("SELECT count(*) FROM platform_config WHERE key LIKE 'aws_eks_%'"))).scalar()
    assert rows >= 2


@pytest.mark.asyncio
async def test_an_apply_that_left_the_endpoint_unchanged_keeps_the_cutover_pending(session):
    """The update flow applies one target at a time; a node-group apply must not strand the cutover."""
    await _preserved(session)
    await _request(session)
    await _prepare(session, _state(), run_id=7)
    applied = await _prepare(session, _state(), phase="apply", run_id=7)
    describe, logs = _fakes()
    with describe, logs:
        await eks_endpoint.finish_apply(session, applied, run_id=7, succeeded=True)
    recorded = await _config(session)
    assert json.loads(recorded["aws_eks_endpoint_cutover"])["status"] == "pending"
    assert recorded["aws_eks_endpoint_mode"] == "preserve_existing"
