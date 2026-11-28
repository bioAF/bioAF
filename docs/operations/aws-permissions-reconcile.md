# AWS permissions reconciliation (existing installations)

New AWS installations get installation-scoped permissions from `install-aws.sh`. An installation
set up before that keeps the old, broad `bioaf-app-starter` policy until an operator moves it over.
Updating the bioAF application does not change IAM: the app is not allowed to change its own
permissions.

## What changes

- **The `bioaf-app` role** gets four installer-owned managed policies (`bioaf-app-access-core`,
  `bioaf-app-access-control`, `bioaf-app-access-roles`, `bioaf-app-access-services`, written by
  `installer/aws_iam.sh`) in place of the inline `bioaf-app-starter` policy. Reads stay account-wide where AWS cannot scope
  them. Changes are limited to:
  - EC2 resources tagged `bioaf:installation=<account>-<region>`;
  - security groups and routes in the application VPC;
  - `bioaf-*` EKS, ECR, CodeBuild and log resources in this account and region;
  - IAM roles under the `/bioaf/` path (new) or roles you tag during reconciliation (existing).

  The app can no longer write any role policy or edit a role's trust policy, edit or pass its own
  role, change a boundary, touch a role tagged `bioaf:protected=true`, tag IAM resources as its own,
  move the EC2 ownership tag, read control-plane logs, delete log history, or share images.
- **What app-created roles can hold.** Each policy attaches only to the role it is for: AWS's
  `AmazonEKSClusterPolicy` to `*-cluster`, the node policies and `bioaf-role-s3-readwrite` to
  `*-node`, the EBS CSI policy to `*-ebs-csi`, the S3 policies to runners and Work Nodes,
  `bioaf-role-cluster-autoscaler` to `*-cluster-autoscaler`, and `bioaf-role-image-build` to the
  build role. Each role can be passed only to its own service (cluster, node and EBS CSI roles to
  EKS, Work Node roles to EC2, the build role to CodeBuild). Every role also carries
  `bioaf-app-created-role-boundary`, which lists only the actions those policies need and repeats
  the app's denials: no app-created role can terminate unrelated instances, delete unrelated
  volumes, images or log groups, write IAM or read secrets.
- **Ownership tags.** Existing EC2 resources, the app VM, and the roles and instance profiles bioAF
  created get the `bioaf:installation` tag. Only an operator can add it to IAM resources.
  Packer tags its temporary key pair, builder, and new work-node AMIs and snapshots at creation
  (the AMI's description is a `Description` tag, because the call that sets an AMI description
  also shares images and is denied); reconciliation also tags the existing
  ones (`bioaf-managed=true`), so a rebuild can replace them.

## Order

Run reconciliation **before** the first infrastructure plan on the upgraded release. The upgraded
compute and image-build modules put the boundary on their roles and tag their resources. The old
starter policy cannot set boundaries, and the new policy cannot tag resources it does not own yet.

1. Upgrade bioAF as usual.
2. Run reconciliation (below).
3. In bioAF, open **Infrastructure > Components** and run **Check for Infrastructure Updates**.

## Protect operator roles first

The app may manage IAM roles named `bioaf-*` that carry the boundary. Tag any role you created
yourself with a `bioaf-` name, and every recovery or administrator role the app must never change:

```sh
aws iam tag-role --role-name ROLE --tags Key=bioaf:protected,Value=true
```

Roles that do not start with `bioaf-` are outside the app's reach.

## Run it

Use an operator identity with IAM, EC2 and resource-tagging rights in the account. From the bioAF
checkout on your own machine:

```sh
./install-aws.sh --reconcile-permissions --region REGION
```

The installer lists:

- the installation ID;
- the app VM and the VPC it uses;
- the `bioaf-*` roles tagged `managed_by=bioaf` that it will bound and tag (never `bioaf-app`, and
  never a role without that tag);
- the EC2 resources tagged `managed_by=bioaf` or `bioaf-managed=true` that it will tag.

The lists also show the instance profiles of those roles. Read the lists: everything on them
becomes this installation's to change and clean up. Answer `n`
if anything does not belong to this installation; nothing is changed. Remove the wrong tag from
that resource yourself and run reconciliation again.

On `y` it:

1. creates or updates the boundary and the managed policies;
2. puts the boundary on each listed role and tags it and its instance profiles;
3. tags the listed EC2 resources and the app VM;
4. attaches the four app policies, then deletes the inline `bioaf-app-starter`.

If AWS refuses to tag any listed resource, reconciliation prints each one with AWS's error and
stops before step 4, so the old policy stays in place. Fix the cause (usually the operator
identity's tagging rights) and run it again.

It creates no VM, key pair, security group or role, and does not touch the application. Running it
again is safe.

## Afterwards

- Run **Check for Infrastructure Updates** and confirm it completes. A `403` or
  `AccessDenied` in the plan or apply names the missing permission; report it rather than widening
  the policy by hand.
- Start a Work Node and stop it; build a work-node environment image if you use them; confirm
  notebooks and pipelines still schedule.
- To roll back, put the previous policy document back on `bioaf-app` with your own credentials. Do
  not give the app a broader policy to fix a failure.

## A deleted cluster or node role

A role's ARN includes its path. EKS cannot change the role of an existing cluster or node group in
place: a different ARN replaces the cluster or every node group using it. So when a role from
before this release (path `/`) that the cluster or a node group uses has been deleted in AWS,
bioAF does not recreate it under `/bioaf/`. The compute plan stops before proposing a new ARN. Its
message names the role, the ARN recorded in the compute state, the cluster or node groups that use
it, and this section.

Restore the role at its original name and path with your own operator credentials (the app cannot
create roles at `/`), then verify it, then plan again. Set the values from the plan's message:

```sh
ACCOUNT=123456789012 REGION=us-west-1 CLUSTER=bioaf-acme-1
BOUNDARY="arn:aws:iam::${ACCOUNT}:policy/bioaf-app-created-role-boundary"
TAGS="Key=managed_by,Value=bioaf Key=bioaf:installation,Value=${ACCOUNT}-${REGION}"
```

**Cluster role** (`CLUSTER-cluster`), trusted by EKS:

```sh
aws iam create-role --path / --role-name "${CLUSTER}-cluster" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"eks.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
  --permissions-boundary "$BOUNDARY" --tags $TAGS
aws iam attach-role-policy --role-name "${CLUSTER}-cluster" \
  --policy-arn arn:aws:iam::aws:policy/AmazonEKSClusterPolicy
```

**Node role** (`CLUSTER-node`), trusted by EC2:

```sh
aws iam create-role --path / --role-name "${CLUSTER}-node" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
  --permissions-boundary "$BOUNDARY" --tags $TAGS
for policy in arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy \
              arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy \
              arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly \
              "arn:aws:iam::${ACCOUNT}:policy/bioaf-role-s3-readwrite"; do
  aws iam attach-role-policy --role-name "${CLUSTER}-node" --policy-arn "$policy"
done
```

**Verify before any infrastructure change.** For the restored role:

```sh
ROLE="${CLUSTER}-node"   # or "${CLUSTER}-cluster"
aws iam get-role --role-name "$ROLE" \
  --query '[Role.Arn, Role.PermissionsBoundary.PermissionsBoundaryArn]' --output text
aws iam list-attached-role-policies --role-name "$ROLE" --query 'AttachedPolicies[].PolicyArn'
```

The ARN must equal the recorded ARN in the plan's message exactly, the boundary must be
`bioaf-app-created-role-boundary`, and the policies must be the ones above. Then run **Check for
Infrastructure Updates**: Terraform finds the role by name and the plan must show no replacement of
the cluster or any node group. Confirm the apply only after reviewing that plan. Nodes that were
running without their role may need to be cycled after the apply; check they are `Ready`.

This check only blocks an ARN change caused by recovery. Replacing a node group on purpose (for
example a new instance type or disk) is still possible through a separately reviewed maintenance
plan; bioAF refuses only cluster replacement.

## Accepted residual risk: dedicated account required

The app still chooses a new role's trust policy when it creates the role, and IAM has no condition
on trust-policy contents. A compromised bioAF app could create a role named `bioaf-<x>-cluster`
that trusts the cluster's OIDC provider, attach `AmazonEKSClusterPolicy`, and assume it from a pod
(bioAF is cluster administrator). It would then hold that AWS-managed policy's unscoped
load-balancer and security-group rights, capped by the boundary.

The owner accepted this on 2026-11-27 for a **dedicated bioAF AWS account**: run bioAF in an
account that holds nothing else. In a shared account, those rights reach other workloads' load
balancers and security groups. Closing it needs installer-owned, fixed-trust roles; that is future
work.

## Limits

- One bioAF installation per AWS account (the `bioaf-app` role name is fixed), so the ownership tag
  is `<account>-<region>`.
- Existing roles and instance profiles keep their path (`/`); only new ones use `/bioaf/`. A role
  or profile deleted in AWS but still in Terraform state is recreated under `/bioaf/` on the next
  plan; bioAF checks each one still exists first. A Work Node profile left at `/` keeps that path
  when its role is recreated. The EKS cluster and node roles are the exception: see
  [A deleted cluster or node role](#a-deleted-cluster-or-node-role).
- Packer removes a same-named AMI on rebuild and cleans up an AMI after a failed build only when the
  image carries this installation's tag from creation. A same-named AMI from elsewhere stops the
  build. Packer does not clean up an AMI if waiting for it to become available fails.
- These documents were checked with a static evaluator in the test suite, not with AWS's policy
  simulator or a live installation. Validate on a test account before production.
