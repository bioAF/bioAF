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

EKS cannot change the role of an existing cluster or node group in place. If Terraform had to
create the role, its ARN would be unknown until apply, so the plan would replace the cluster or
every node group using it, even with the same name and path. bioAF therefore never recreates these
roles itself. Before each compute plan it reads the role ARN that every cluster (`role_arn`) and
node group (`node_role_arn`) has in the compute state and checks that the role exists at exactly
that ARN. If not, the plan stops before proposing anything. The message names the role, the
recorded ARN, the cluster or node groups that use it, and this section. bioAF also refuses any plan
that would replace a cluster or node group because of its role ARN.

Recovery has four steps, in order, all with your own operator credentials: restore the role,
repair what used the old role, verify, then plan again. Do not apply any infrastructure change
until the last step shows no replacement.

### 1. Restore the role at its recorded name and path

Take the name and path from the recorded ARN (`arn:aws:iam::ACCOUNT:role/PATH/NAME`; a role from
before this release has path `/`):

```sh
ACCOUNT=123456789012 REGION=us-west-1 CLUSTER=bioaf-acme-1
ROLE_PATH=/            # "/" or "/bioaf/", from the recorded ARN
RECORDED_ARN=arn:aws:iam::123456789012:role/bioaf-acme-1-node   # from the message
BOUNDARY="arn:aws:iam::${ACCOUNT}:policy/bioaf-app-created-role-boundary"
TAGS="Key=managed_by,Value=bioaf Key=org,Value=ORG_SLUG Key=bioaf:installation,Value=${ACCOUNT}-${REGION}"
```

**Cluster role** (`CLUSTER-cluster`), trusted by EKS:

```sh
aws iam create-role --path "$ROLE_PATH" --role-name "${CLUSTER}-cluster" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"eks.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
  --permissions-boundary "$BOUNDARY" --tags $TAGS
aws iam attach-role-policy --role-name "${CLUSTER}-cluster" \
  --policy-arn arn:aws:iam::aws:policy/AmazonEKSClusterPolicy
```

**Node role** (`CLUSTER-node`), trusted by EC2:

```sh
aws iam create-role --path "$ROLE_PATH" --role-name "${CLUSTER}-node" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
  --permissions-boundary "$BOUNDARY" --tags $TAGS
for policy in arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy \
              arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy \
              arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly \
              "arn:aws:iam::${ACCOUNT}:policy/bioaf-role-s3-readwrite"; do
  aws iam attach-role-policy --role-name "${CLUSTER}-node" --policy-arn "$policy"
done
```

### 2. Repair what used the old role (node role)

A recreated role is a new IAM principal with a new internal ID, even at the same ARN. Two things
that pointed at the old one must be fixed by hand.

**Instance profiles.** Deleting a role removes it from its instance profiles. Each node group's
launch template names an EKS-created instance profile, which is now empty. For each node group in
the message:

```sh
NG=system   # each node group from the message
for asg in $(aws eks describe-nodegroup --cluster-name "$CLUSTER" --nodegroup-name "$NG" \
    --query 'nodegroup.resources.autoScalingGroups[].name' --output text); do
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$asg" \
    --query 'AutoScalingGroups[].[LaunchTemplate.LaunchTemplateId,LaunchTemplate.Version,MixedInstancesPolicy.LaunchTemplate.LaunchTemplateSpecification.LaunchTemplateId,MixedInstancesPolicy.LaunchTemplate.LaunchTemplateSpecification.Version]' --output text
done
aws ec2 describe-launch-template-versions --launch-template-id LT_ID --versions LT_VERSION \
  --query 'LaunchTemplateVersions[].LaunchTemplateData.IamInstanceProfile'
```

For each profile found (its name is the last part of the ARN):

```sh
aws iam get-instance-profile --instance-profile-name PROFILE --query 'InstanceProfile.Roles[].Arn'
# empty: put the restored role back
aws iam add-role-to-instance-profile --instance-profile-name PROFILE --role-name "${CLUSTER}-node"
```

**Node authentication.** The cluster uses access entries and the `aws-auth` ConfigMap
(`API_AND_CONFIG_MAP`). AWS documents that an access entry for a deleted role is not removed and
does not work for a role recreated at the same ARN. Delete it and create it again, keeping the type
it had (managed Linux node groups use `EC2_LINUX`):

```sh
aws eks describe-access-entry --cluster-name "$CLUSTER" --principal-arn "$RECORDED_ARN" \
  --query 'accessEntry.[type,principalArn]' --output text
aws eks delete-access-entry --cluster-name "$CLUSTER" --principal-arn "$RECORDED_ARN"
aws eks create-access-entry --cluster-name "$CLUSTER" --principal-arn "$RECORDED_ARN" --type EC2_LINUX
```

If `describe-access-entry` finds no entry, check the ConfigMap instead (`kubectl -n kube-system get
configmap aws-auth -o yaml` from your operator access). It must map the role (written without its
path, as `aws-auth` requires) to `system:bootstrappers` and `system:nodes`. Do not create a second
mapping if an access entry exists.

The cluster role needs neither repair: it is not a Kubernetes principal and has no instance profile.

### 3. Verify before any infrastructure change

```sh
ROLE="${CLUSTER}-node"   # or "${CLUSTER}-cluster"
aws iam get-role --role-name "$ROLE" \
  --query '[Role.Arn, Role.PermissionsBoundary.PermissionsBoundaryArn]' --output text
aws iam list-attached-role-policies --role-name "$ROLE" --query 'AttachedPolicies[].PolicyArn'
```

- The ARN equals the recorded ARN exactly, the boundary is `bioaf-app-created-role-boundary`, and
  the policies are the ones in step 1.
- Node role: every profile from step 2 lists the role; `aws eks describe-nodegroup --cluster-name
  "$CLUSTER" --nodegroup-name "$NG" --query 'nodegroup.[status,health.issues]'` shows `ACTIVE` with
  no issues; nodes are `Ready` (`kubectl get nodes`). Nodes that were running without credentials
  may need to be cycled (terminate one; its group replaces it) before they rejoin; check again.
- Cluster role: `aws eks describe-cluster --name "$CLUSTER" --query 'cluster.[status,health]'`
  shows `ACTIVE` with no issues.

Recovery is complete only when all of these hold.

### 4. Plan again

Run **Check for Infrastructure Updates**. bioAF re-checks the role, and Terraform finds it by name.
Review the plan: it must show no replacement of the cluster or any node group (bioAF refuses one
caused by the role). Confirm the apply only after that review.

If the message instead says the role is **not in the compute state**, the state no longer records
the role the cluster or node groups use. bioAF does not produce that state, and planning would
create a new role. Do not work around it; restore the state from its versioned backup in the state
bucket after review, or ask for help, before planning again.

This check blocks only an ARN change caused by recovery. Replacing a node group on purpose (for
example a new instance type or disk) is still possible through a separately reviewed maintenance
plan; bioAF refuses only cluster replacement for other causes.

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
  when its role is recreated. Roles the EKS cluster or a node group uses are the exception, at any
  path: see
  [A deleted cluster or node role](#a-deleted-cluster-or-node-role).
- Packer removes a same-named AMI on rebuild and cleans up an AMI after a failed build only when the
  image carries this installation's tag from creation. A same-named AMI from elsewhere stops the
  build. Packer does not clean up an AMI if waiting for it to become available fails.
- These documents were checked with a static evaluator in the test suite, not with AWS's policy
  simulator or a live installation. Validate on a test account before production.
