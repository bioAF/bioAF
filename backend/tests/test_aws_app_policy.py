"""The installer's bioaf-app policy and app-created-role boundary (plan 04B), evaluated statically.

The documents come from ``installer/aws_iam.sh`` exactly as the installer renders them. Each test
asks the static evaluator in ``tests/support/iam_policy_eval.py`` whether a request the app makes
(or must never make) is allowed. This proves what the documents say; it is not an AWS policy
simulation or a live result.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.iam_policy_eval import Request, allowed, allowed_with_boundary, decide

REPO = Path(__file__).resolve().parents[2]
A, R, VPC = "123456789012", "us-west-1", "vpc-0a1b2c3d4e5f60718"
ID = f"{A}-{R}"
BOUNDARY = f"arn:aws:iam::{A}:policy/bioaf-app-created-role-boundary"
EC2 = f"arn:aws:ec2:{R}:{A}"
APP_VPC = f"{EC2}:vpc/{VPC}"
OWNED = {"aws:ResourceTag/bioaf:installation": ID}
TAGGED = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation", "Name"]}

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to render the installer documents")


def _render(function: str, *args: str) -> dict:
    script = f'source "{REPO}/installer/aws_iam.sh"; {function} ' + " ".join(f'"{a}"' for a in args)
    out = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def app():
    return [_render("aws_iam_app_policy", A, R, VPC)]


@pytest.fixture(scope="module")
def boundary():
    return _render("aws_iam_role_boundary_policy", A, R)


def _role(name: str) -> str:
    return f"arn:aws:iam::{A}:role/{name}"


# --- Size and shape -----------------------------------------------------------------


def test_the_policy_fits_iams_inline_limit_for_the_longest_region():
    rendered = _render("aws_iam_app_policy", A, "ap-southeast-3", "vpc-0123456789abcdef0")
    assert len(json.dumps(rendered, separators=(",", ":"))) < 10240


def test_no_service_is_granted_wholesale(app):
    for statement in app[0]["Statement"]:
        if statement["Effect"] != "Allow":
            continue
        actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        assert "ec2:*" not in actions and "iam:*" not in actions
        if "eks:*" in actions:
            assert all(r.startswith(f"arn:aws:eks:{R}:{A}:") for r in statement["Resource"])
        if "s3:*" in actions:
            assert statement["Condition"]["StringEquals"]["aws:ResourceAccount"] == A


# --- The app cannot expand its own permissions -----------------------------------------


@pytest.mark.parametrize(
    "action",
    ["iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:DeleteRolePolicy", "iam:TagRole"],
)
def test_the_app_cannot_edit_its_own_role(app, action):
    ctx = {"iam:PermissionsBoundary": BOUNDARY, "iam:PolicyARN": "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"}
    assert decide(app, Request(action, _role("bioaf-app"), ctx)) == "explicit_deny"


def test_the_app_cannot_pass_its_own_role(app):
    ctx = {"iam:PassedToService": "ec2.amazonaws.com", **OWNED}
    assert decide(app, Request("iam:PassRole", _role("bioaf-app"), ctx)) == "explicit_deny"


def test_the_app_cannot_change_the_boundary_policy(app):
    for action in ("iam:CreatePolicyVersion", "iam:DeletePolicy", "iam:SetDefaultPolicyVersion"):
        assert not allowed(app, Request(action, BOUNDARY))


def test_roles_are_created_only_under_the_boundary(app):
    assert allowed(app, Request("iam:CreateRole", _role("bioaf-acme-1-node"), {"iam:PermissionsBoundary": BOUNDARY}))
    assert not allowed(app, Request("iam:CreateRole", _role("bioaf-acme-1-node")))
    other = f"arn:aws:iam::{A}:policy/AdministratorAccessBoundary"
    assert not allowed(app, Request("iam:CreateRole", _role("bioaf-acme-1-node"), {"iam:PermissionsBoundary": other}))
    assert not allowed(app, Request("iam:CreateRole", _role("ops-recovery"), {"iam:PermissionsBoundary": BOUNDARY}))


def test_only_bounded_roles_take_new_permissions(app):
    bounded = {"iam:PermissionsBoundary": BOUNDARY}
    assert allowed(app, Request("iam:PutRolePolicy", _role("bioaf-acme-1-node"), bounded))
    assert not allowed(app, Request("iam:PutRolePolicy", _role("bioaf-operator-made")))


def test_the_boundary_cannot_be_removed_or_replaced(app):
    assert decide(app, Request("iam:DeleteRolePermissionsBoundary", _role("bioaf-acme-1-node"))) == "explicit_deny"
    other = {"iam:PermissionsBoundary": f"arn:aws:iam::{A}:policy/wide"}
    assert not allowed(app, Request("iam:PutRolePermissionsBoundary", _role("bioaf-acme-1-node"), other))


def test_only_approved_managed_policies_attach(app):
    bounded = {"iam:PermissionsBoundary": BOUNDARY}
    ok = {**bounded, "iam:PolicyARN": "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"}
    admin = {**bounded, "iam:PolicyARN": "arn:aws:iam::aws:policy/AdministratorAccess"}
    assert allowed(app, Request("iam:AttachRolePolicy", _role("bioaf-acme-1-node"), ok))
    assert not allowed(app, Request("iam:AttachRolePolicy", _role("bioaf-acme-1-node"), admin))


def test_roles_are_passed_only_when_owned_and_only_to_approved_services(app):
    owned_role = _role("bioaf-acme-1-cluster")
    assert allowed(app, Request("iam:PassRole", owned_role, {"iam:PassedToService": "eks.amazonaws.com", **OWNED}))
    assert not allowed(
        app, Request("iam:PassRole", owned_role, {"iam:PassedToService": "lambda.amazonaws.com", **OWNED})
    )
    assert not allowed(app, Request("iam:PassRole", owned_role, {"iam:PassedToService": "eks.amazonaws.com"}))


def test_protected_operator_roles_are_off_limits(app):
    protected = {"aws:ResourceTag/bioaf:protected": "true", "iam:PermissionsBoundary": BOUNDARY, **OWNED}
    for action in ("iam:PutRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:DeleteRole"):
        assert decide(app, Request(action, _role("bioaf-ops-recovery"), protected)) == "explicit_deny"


def test_ownership_cannot_be_untagged_from_roles(app):
    ctx = {"aws:TagKeys": ["bioaf:installation"]}
    assert decide(app, Request("iam:UntagRole", _role("bioaf-acme-1-node"), ctx)) == "explicit_deny"


def test_app_created_roles_never_gain_iam_or_secret_access(boundary):
    roles = [{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}]
    for action in (
        "iam:PutRolePolicy",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:PassRole",
        "sts:AssumeRole",
        "secretsmanager:GetSecretValue",
        "organizations:ListAccounts",
    ):
        assert not allowed_with_boundary(roles, boundary, Request(action, "*")), action
    assert allowed_with_boundary(roles, boundary, Request("ec2:CreateNetworkInterface", f"{EC2}:network-interface/*"))
    assert not allowed_with_boundary(
        roles, boundary, Request("s3:GetObject", "arn:aws:s3:::bioaf-other/x", {"aws:ResourceAccount": "999999999999"})
    )


# --- EC2: owned resources only ----------------------------------------------------------


def test_work_nodes_launch_with_owned_tags_into_owned_networks(app):
    for resource in ("instance/*", "volume/*", "network-interface/*"):
        assert allowed(app, Request("ec2:RunInstances", f"{EC2}:{resource}", TAGGED))
    for resource in ("subnet/subnet-1", "security-group/sg-1"):
        assert allowed(app, Request("ec2:RunInstances", f"{EC2}:{resource}", OWNED))
    assert allowed(app, Request("ec2:RunInstances", f"arn:aws:ec2:{R}::image/ami-1", {"ec2:Owner": A}))


def test_a_launch_without_the_ownership_tag_or_into_another_network_is_refused(app):
    assert not allowed(app, Request("ec2:RunInstances", f"{EC2}:instance/*", {"aws:RequestTag/Name": "x"}))
    assert not allowed(app, Request("ec2:RunInstances", f"{EC2}:subnet/subnet-foreign", TAGGED))
    assert not allowed(app, Request("ec2:RunInstances", f"{EC2}:security-group/sg-foreign", TAGGED))
    assert not allowed(app, Request("ec2:RunInstances", f"arn:aws:ec2:{R}::image/ami-x", {"ec2:Owner": "099720109477"}))


def test_only_owned_instances_are_terminated(app):
    assert allowed(app, Request("ec2:TerminateInstances", f"{EC2}:instance/i-1", OWNED))
    assert not allowed(app, Request("ec2:TerminateInstances", f"{EC2}:instance/i-2"))
    assert not allowed(
        app, Request("ec2:TerminateInstances", f"{EC2}:instance/i-3", {"aws:ResourceTag/bioaf:installation": "x-y"})
    )


def test_tags_cannot_claim_or_reassign_ownership(app):
    claim = {"aws:RequestTag/bioaf:installation": ID, "aws:TagKeys": ["bioaf:installation"]}
    assert not allowed(app, Request("ec2:CreateTags", f"{EC2}:vpc/vpc-foreign", claim))
    reassign = {**OWNED, "aws:RequestTag/bioaf:installation": "other", "aws:TagKeys": ["bioaf:installation"]}
    assert decide(app, Request("ec2:CreateTags", f"{EC2}:vpc/vpc-1", reassign)) == "explicit_deny"
    remove = {**OWNED, "aws:TagKeys": ["bioaf:installation"]}
    assert decide(app, Request("ec2:DeleteTags", f"{EC2}:vpc/vpc-1", remove)) == "explicit_deny"
    # Other tags on owned resources, and the ownership tag at creation, are fine.
    assert allowed(app, Request("ec2:CreateTags", f"{EC2}:vpc/vpc-1", {**OWNED, "aws:TagKeys": ["Name"]}))
    at_create = {**TAGGED, "ec2:CreateAction": "CreateVpc"}
    assert allowed(app, Request("ec2:CreateTags", f"{EC2}:vpc/vpc-new", at_create))


def test_the_compute_network_is_created_and_torn_down_only_when_owned(app):
    assert allowed(app, Request("ec2:CreateVpc", f"{EC2}:vpc/*", TAGGED))
    assert not allowed(app, Request("ec2:CreateVpc", f"{EC2}:vpc/*"))
    assert allowed(app, Request("ec2:CreateSubnet", f"{EC2}:vpc/vpc-1", OWNED))
    assert allowed(app, Request("ec2:CreateSubnet", f"{EC2}:subnet/*", TAGGED))
    assert not allowed(app, Request("ec2:CreateSubnet", f"{EC2}:vpc/vpc-foreign", TAGGED))
    assert allowed(app, Request("ec2:DeleteVpc", f"{EC2}:vpc/vpc-1", OWNED))
    assert not allowed(app, Request("ec2:DeleteVpc", f"{EC2}:vpc/vpc-foreign"))


def test_the_application_network_is_changed_only_inside_its_vpc(app):
    in_vpc = {"ec2:Vpc": APP_VPC}
    assert allowed(app, Request("ec2:AuthorizeSecurityGroupIngress", f"{EC2}:security-group/sg-app", in_vpc))
    assert allowed(app, Request("ec2:CreateRoute", f"{EC2}:route-table/rtb-app", in_vpc))
    elsewhere = {"ec2:Vpc": f"{EC2}:vpc/vpc-other"}
    assert not allowed(app, Request("ec2:AuthorizeSecurityGroupIngress", f"{EC2}:security-group/sg-x", elsewhere))
    assert not allowed(app, Request("ec2:CreateRoute", f"{EC2}:route-table/rtb-x", elsewhere))


def test_images_and_snapshots_cannot_be_shared(app):
    for action in ("ec2:ModifyImageAttribute", "ec2:ModifySnapshotAttribute"):
        assert decide(app, Request(action, f"arn:aws:ec2:{R}::image/ami-1", OWNED)) == "explicit_deny"


# --- Other services ------------------------------------------------------------------------


def test_eks_changes_stay_on_bioaf_clusters_in_this_account_and_region(app):
    assert allowed(app, Request("eks:UpdateClusterConfig", f"arn:aws:eks:{R}:{A}:cluster/bioaf-acme-1"))
    assert not allowed(app, Request("eks:UpdateClusterConfig", f"arn:aws:eks:{R}:{A}:cluster/payments"))
    assert not allowed(app, Request("eks:DeleteCluster", f"arn:aws:eks:us-east-1:{A}:cluster/bioaf-acme-1"))


def test_s3_access_is_limited_to_this_accounts_bioaf_buckets(app):
    assert allowed(app, Request("s3:PutObject", "arn:aws:s3:::bioaf-raw-acme-1/x", {"aws:ResourceAccount": A}))
    assert not allowed(
        app, Request("s3:PutObject", "arn:aws:s3:::bioaf-raw-acme-1/x", {"aws:ResourceAccount": "9" * 12})
    )
    assert not allowed(app, Request("s3:PutObject", "arn:aws:s3:::lab-archive/x", {"aws:ResourceAccount": A}))


def test_control_plane_logs_are_managed_but_not_read_or_deleted(app):
    group = f"arn:aws:logs:{R}:{A}:log-group:/aws/eks/bioaf-acme-1/cluster"
    assert allowed(app, Request("logs:PutRetentionPolicy", group))
    assert allowed(app, Request("logs:CreateLogGroup", group))
    assert not allowed(app, Request("logs:GetLogEvents", f"{group}:log-stream:audit"))
    assert not allowed(app, Request("logs:FilterLogEvents", group))
    assert decide(app, Request("logs:DeleteLogGroup", group)) == "explicit_deny"
    build = f"arn:aws:logs:{R}:{A}:log-group:/aws/codebuild/bioaf-image-build:log-stream:x"
    assert allowed(app, Request("logs:GetLogEvents", build))


def test_secrets_and_unrelated_services_are_not_granted(app):
    for action, resource in (
        ("secretsmanager:GetSecretValue", "*"),
        ("kms:Decrypt", "*"),
        ("sts:AssumeRole", _role("ops-admin")),
        ("lambda:CreateFunction", "*"),
    ):
        assert not allowed(app, Request(action, resource)), action
