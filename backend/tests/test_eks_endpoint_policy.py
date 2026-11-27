"""EKS endpoint and control-plane logging policy (plan 04C): what a compute plan may set.

Pure decisions, no AWS or database: given an installation's recorded configuration, the policy
chooses the endpoint and logging inputs or refuses the operation, and it reviews a Terraform plan's
cluster change against the recorded mode.
"""

import json
from datetime import datetime, timezone

import pytest

from app.exceptions import ValidationError
from app.services import eks_endpoint_policy as policy

ARN = "arn:aws:eks:us-west-1:123456789012:cluster/bioaf-acme-c0ffee"
CREATED = "2026-06-18T10:00:00.123Z"
NETWORK = {"aws_app_vpc_id": "vpc-app", "aws_app_security_group_id": "sg-app"}


def _snapshot(public=True, private=True, cidrs=("0.0.0.0/0",)):
    return {
        "cluster_arn": ARN,
        "cluster_name": "bioaf-acme-c0ffee",
        "created_at": CREATED,
        "endpoint_public_access": public,
        "endpoint_private_access": private,
        "public_access_cidrs": list(cidrs),
    }


# --- Endpoint selection ---------------------------------------------------------


def test_a_fresh_installation_defaults_to_private_only():
    tf = policy.compute_tfvars({**NETWORK}, cluster_in_state=False)
    assert tf["endpoint_public_access"] is False
    assert tf["endpoint_private_access"] is True
    assert tf["public_access_cidrs"] == []


def test_private_only_without_the_application_network_is_refused():
    with pytest.raises(ValidationError, match="application network"):
        policy.compute_tfvars({}, cluster_in_state=False)


def test_restricted_public_keeps_private_access_and_passes_the_approved_cidrs():
    config = {
        "aws_eks_endpoint_mode": "public_restricted",
        "aws_eks_public_access_cidrs": json.dumps(["203.0.113.10/32", "198.51.100.0/24"]),
    }
    tf = policy.compute_tfvars(config, cluster_in_state=False)
    assert tf["endpoint_public_access"] is True
    assert tf["endpoint_private_access"] is True
    assert tf["public_access_cidrs"] == ["203.0.113.10/32", "198.51.100.0/24"]


@pytest.mark.parametrize(
    "cidrs",
    [[], ["0.0.0.0/0"], ["::/0"], ["203.0.113.10/32", "0.0.0.0/0"], ["not-a-cidr"], ["203.0.113.10/33"]],
)
def test_restricted_public_refuses_empty_unrestricted_or_invalid_cidrs(cidrs):
    config = {"aws_eks_endpoint_mode": "public_restricted", "aws_eks_public_access_cidrs": json.dumps(cidrs)}
    with pytest.raises(ValidationError):
        policy.compute_tfvars(config, cluster_in_state=False)


def test_an_existing_cluster_without_a_mode_never_inherits_the_private_default():
    with pytest.raises(ValidationError, match="endpoint"):
        policy.compute_tfvars({**NETWORK}, cluster_in_state=True)


def test_preserve_existing_reproduces_the_legacy_endpoint_exactly():
    config = {"aws_eks_endpoint_mode": "preserve_existing", "aws_eks_endpoint_snapshot": json.dumps(_snapshot())}
    tf = policy.compute_tfvars(config, cluster_in_state=True)
    assert tf["endpoint_public_access"] is True
    assert tf["endpoint_private_access"] is True
    assert tf["public_access_cidrs"] == ["0.0.0.0/0"]


def test_preserve_existing_needs_no_cutover_prerequisites():
    """No application network, unrestricted CIDRs: still allowed, because nothing changes."""
    config = {"aws_eks_endpoint_mode": "preserve_existing", "aws_eks_endpoint_snapshot": json.dumps(_snapshot())}
    assert policy.compute_tfvars(config, cluster_in_state=True)["endpoint_public_access"] is True


def test_preserve_existing_is_invalid_for_a_fresh_or_replacement_cluster():
    config = {"aws_eks_endpoint_mode": "preserve_existing", "aws_eks_endpoint_snapshot": json.dumps(_snapshot())}
    with pytest.raises(ValidationError, match="existing cluster"):
        policy.compute_tfvars(config, cluster_in_state=False)


def test_preserve_existing_without_a_snapshot_is_refused():
    with pytest.raises(ValidationError, match="snapshot"):
        policy.compute_tfvars({"aws_eks_endpoint_mode": "preserve_existing"}, cluster_in_state=True)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValidationError, match="mode"):
        policy.compute_tfvars({"aws_eks_endpoint_mode": "open"}, cluster_in_state=False)


def test_a_pending_cutover_supplies_its_target():
    config = {
        "aws_eks_endpoint_mode": "preserve_existing",
        "aws_eks_endpoint_snapshot": json.dumps(_snapshot()),
        "aws_eks_endpoint_cutover": json.dumps(
            {"mode": "private_only", "public_access_cidrs": [], "status": "pending"}
        ),
        **NETWORK,
    }
    tf = policy.compute_tfvars(config, cluster_in_state=True)
    assert tf["endpoint_public_access"] is False and tf["endpoint_private_access"] is True


# --- Logging ---------------------------------------------------------------------


def test_logging_defaults_to_all_five_types_and_thirty_days():
    tf = policy.compute_tfvars({**NETWORK}, cluster_in_state=False)
    assert tf["control_plane_log_types"] == ["api", "audit", "authenticator", "controllerManager", "scheduler"]
    assert tf["control_plane_log_retention_days"] == 30


def test_retention_is_configurable_to_a_supported_value():
    tf = policy.compute_tfvars({**NETWORK, "aws_eks_log_retention_days": "90"}, cluster_in_state=False)
    assert tf["control_plane_log_retention_days"] == 90


@pytest.mark.parametrize("value", ["0", "31", "-1", "thirty", "1.5"])
def test_unsupported_retention_is_refused(value):
    with pytest.raises(ValidationError, match="retention"):
        policy.compute_tfvars({**NETWORK, "aws_eks_log_retention_days": value}, cluster_in_state=False)


def test_a_log_type_override_must_keep_the_audit_and_authentication_trail():
    tf = policy.compute_tfvars(
        {**NETWORK, "aws_eks_log_types": json.dumps(["audit", "authenticator", "api"])}, cluster_in_state=False
    )
    assert tf["control_plane_log_types"] == ["api", "audit", "authenticator"]
    with pytest.raises(ValidationError, match="audit"):
        policy.compute_tfvars({**NETWORK, "aws_eks_log_types": json.dumps(["api"])}, cluster_in_state=False)
    with pytest.raises(ValidationError, match="log type"):
        policy.compute_tfvars(
            {**NETWORK, "aws_eks_log_types": json.dumps(["audit", "authenticator", "everything"])},
            cluster_in_state=False,
        )


# --- Identity: remote state against the live cluster ---------------------------


def _state(**overrides):
    attributes = {
        "arn": ARN,
        "name": "bioaf-acme-c0ffee",
        "created_at": CREATED,
        "vpc_config": [
            {"endpoint_public_access": True, "endpoint_private_access": True, "public_access_cidrs": ["0.0.0.0/0"]}
        ],
    }
    attributes.update(overrides)
    return {
        "resources": [
            {"mode": "managed", "type": "aws_eks_cluster", "name": "this", "instances": [{"attributes": attributes}]}
        ]
    }


def _live(**overrides):
    cluster = {
        "arn": ARN,
        "name": "bioaf-acme-c0ffee",
        "createdAt": datetime(2026, 6, 18, 10, 0, 0, 123000, tzinfo=timezone.utc),
        "resourcesVpcConfig": {
            "endpointPublicAccess": True,
            "endpointPrivateAccess": True,
            "publicAccessCidrs": ["0.0.0.0/0"],
        },
    }
    cluster.update(overrides)
    return cluster


CONFIG = {"aws_account_id": "123456789012", "aws_region": "us-west-1", "gke_cluster_name": "bioaf-acme-c0ffee"}


def test_a_verified_cluster_yields_a_snapshot_of_its_live_endpoint():
    snapshot = policy.snapshot_existing_cluster(_state(), _live(), CONFIG)
    assert snapshot["cluster_arn"] == ARN
    assert snapshot["endpoint_public_access"] is True
    assert snapshot["public_access_cidrs"] == ["0.0.0.0/0"]


def test_identity_is_checked_when_config_records_only_the_cluster_name():
    snapshot = policy.snapshot_existing_cluster(
        _state(), _live(), {"aws_account_id": "123456789012", "aws_region": "us-west-1"}
    )
    assert snapshot["cluster_name"] == "bioaf-acme-c0ffee"


@pytest.mark.parametrize(
    "state, live, match",
    [
        (None, _live(), "state"),
        ({"resources": []}, _live(), "state"),
        (_state(arn=None), _live(), "ARN"),
        (_state(created_at=None), _live(), "creation"),
        (_state(), _live(arn="arn:aws:eks:us-west-1:123456789012:cluster/other"), "does not match"),
        (_state(), _live(createdAt=datetime(2026, 7, 1, tzinfo=timezone.utc)), "does not match"),
        (_state(arn="arn:aws:eks:us-east-1:123456789012:cluster/bioaf-acme-c0ffee"), _live(), "region"),
        (_state(arn="arn:aws:eks:us-west-1:999999999999:cluster/bioaf-acme-c0ffee"), _live(), "account"),
        (_state(name="bioaf-other"), _live(), "name"),
    ],
)
def test_missing_or_mismatched_identity_refuses_the_snapshot(state, live, match):
    with pytest.raises(ValidationError, match=match):
        policy.snapshot_existing_cluster(state, live, CONFIG)


def test_the_live_endpoint_must_still_match_the_snapshot_before_apply():
    snapshot = _snapshot()
    policy.require_live_matches(snapshot, _live())
    changed = _live(
        resourcesVpcConfig={"endpointPublicAccess": False, "endpointPrivateAccess": True, "publicAccessCidrs": []}
    )
    with pytest.raises(ValidationError, match="changed"):
        policy.require_live_matches(snapshot, changed)
    with pytest.raises(ValidationError, match="changed"):
        policy.require_live_matches(snapshot, _live(arn="arn:aws:eks:us-west-1:123456789012:cluster/other"))


# --- Plan review ---------------------------------------------------------------


def _plan(actions, before=None, after=None):
    return {
        "resource_changes": [
            {
                "address": "aws_eks_cluster.this",
                "type": "aws_eks_cluster",
                "name": "this",
                "change": {"actions": actions, "before": before, "after": after},
            }
        ]
    }


def _cluster_values(public, private, cidrs, logs=None):
    return {
        "vpc_config": [
            {"endpoint_public_access": public, "endpoint_private_access": private, "public_access_cidrs": cidrs}
        ],
        "enabled_cluster_log_types": logs or [],
    }


PRESERVE = {"aws_eks_endpoint_mode": "preserve_existing", "aws_eks_endpoint_snapshot": json.dumps(_snapshot())}


def test_a_preserve_plan_that_only_adds_logging_is_accepted():
    before = _cluster_values(True, True, ["0.0.0.0/0"])
    after = _cluster_values(True, True, ["0.0.0.0/0"], logs=["api", "audit"])
    policy.review_plan(_plan(["update"], before, after), PRESERVE)


def test_a_preserve_plan_with_any_endpoint_delta_is_refused():
    before = _cluster_values(True, True, ["0.0.0.0/0"])
    for after in (
        _cluster_values(False, True, ["0.0.0.0/0"]),
        _cluster_values(True, False, ["0.0.0.0/0"]),
        _cluster_values(True, True, ["203.0.113.10/32"]),
    ):
        with pytest.raises(ValidationError, match="endpoint"):
            policy.review_plan(_plan(["update"], before, after), PRESERVE)


@pytest.mark.parametrize("actions", [["delete", "create"], ["create", "delete"], ["delete"]])
def test_a_plan_that_replaces_or_deletes_the_cluster_is_refused(actions):
    before = _cluster_values(True, True, ["0.0.0.0/0"])
    with pytest.raises(ValidationError, match="replace"):
        policy.review_plan(_plan(actions, before, None if actions == ["delete"] else before), PRESERVE)


def test_creating_a_cluster_under_preserve_existing_is_refused():
    with pytest.raises(ValidationError, match="existing cluster"):
        policy.review_plan(_plan(["create"], None, _cluster_values(True, True, ["0.0.0.0/0"])), PRESERVE)


def test_a_fresh_private_only_create_is_accepted():
    policy.review_plan(_plan(["create"], None, _cluster_values(False, True, None)), {**NETWORK})


def test_a_plan_without_a_cluster_change_is_accepted():
    policy.review_plan({"resource_changes": []}, PRESERVE)


# --- Status ----------------------------------------------------------------------


def test_preserved_unrestricted_access_is_reported_as_pending_hardening():
    status = policy.status(PRESERVE)
    assert status["mode"] == "preserve_existing"
    assert status["pending_hardening"] is True
    assert status["public_access_cidrs"] == ["0.0.0.0/0"]


def test_a_hardened_mode_is_not_pending():
    assert policy.status({"aws_eks_endpoint_mode": "private_only"})["pending_hardening"] is False


def test_a_deployed_cluster_without_a_recorded_mode_is_pending():
    assert policy.status({"compute_deployed": "true"})["pending_hardening"] is True
    assert policy.status({})["pending_hardening"] is False


# --- One identity check everywhere (review finding: recreated cluster accepted) ---------


def test_a_recreated_cluster_with_the_same_arn_fails_the_live_recheck():
    recreated = _live(createdAt=datetime(2026, 9, 1, tzinfo=timezone.utc))
    with pytest.raises(ValidationError, match="not the recorded cluster"):
        policy.require_live_matches(_snapshot(), recreated)


def test_a_snapshot_without_a_creation_time_cannot_verify_anything():
    snapshot = _snapshot()
    del snapshot["created_at"]
    with pytest.raises(ValidationError, match="creation time"):
        policy.require_same_cluster(snapshot, _live())


def test_remote_state_must_describe_the_recorded_cluster_too():
    attributes = policy.state_cluster(_state(created_at="2026-09-01T00:00:00Z"))
    with pytest.raises(ValidationError, match="remote compute state"):
        policy.require_same_cluster(_snapshot(), _live(), attributes)
    policy.require_same_cluster(_snapshot(), _live(), policy.state_cluster(_state()))
