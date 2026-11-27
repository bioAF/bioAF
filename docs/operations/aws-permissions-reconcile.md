# AWS permissions reconciliation (existing installations)

New AWS installations get installation-scoped permissions from `install-aws.sh`. An installation
set up before that keeps the old, broad `bioaf-app-starter` policy until an operator moves it over.
Updating the bioAF application does not change IAM: the app is not allowed to change its own
permissions.

## What changes

- **The `bioaf-app` role** gets three installer-owned managed policies (`bioaf-app-access-core`,
  `bioaf-app-access-control`, `bioaf-app-access-services`, written by `installer/aws_iam.sh`) in
  place of the inline `bioaf-app-starter` policy. Reads stay account-wide where AWS cannot scope
  them. Changes are limited to:
  - EC2 resources tagged `bioaf:installation=<account>-<region>`;
  - security groups and routes in the application VPC;
  - `bioaf-*` EKS, ECR, CodeBuild and log resources in this account and region;
  - IAM roles under the `/bioaf/` path (new) or roles you tag during reconciliation (existing).

  The app can no longer write any role policy, edit or pass its own role, change a boundary, touch
  a role tagged `bioaf:protected=true`, tag IAM resources as its own, move the EC2 ownership tag,
  read control-plane logs, delete log history, or remove or share images.
- **What app-created roles can hold.** The app can attach only AWS's EKS policies and four
  installer-owned policies (`bioaf-role-s3-readwrite`, `bioaf-role-s3-readonly`,
  `bioaf-role-cluster-autoscaler`, `bioaf-role-image-build`). Every role also carries the
  `bioaf-app-created-role-boundary`. That boundary lists only the actions those policies need and
  repeats the app's denials, so no app-created role can terminate unrelated instances, delete
  unrelated volumes or log groups, remove images, write IAM or read secrets.
- **Ownership tags.** Existing EC2 resources, the app VM, and the roles and instance profiles bioAF
  created get the `bioaf:installation` tag. Only an operator can add it to IAM resources.
  Work-node images and snapshots are not tagged; nothing needs them owned.

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
4. attaches the three app policies, then deletes the inline `bioaf-app-starter`.

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

## Limits

- One bioAF installation per AWS account (the `bioaf-app` role name is fixed), so the ownership tag
  is `<account>-<region>`.
- Existing roles keep their path (`/`); only roles created after this change use `/bioaf/`.
- The EKS cluster role holds AWS's `AmazonEKSClusterPolicy`, which manages load balancers and their
  security groups for Kubernetes `LoadBalancer` services. AWS does not scope that policy, so the
  boundary allows those actions.
- Work-node AMIs are never removed automatically. A retry of the same build after its AMI was
  registered fails with a name conflict; start a new build. Delete superseded `bioaf-worknode-*`
  AMIs and their snapshots yourself.
- These documents were checked with a static evaluator in the test suite, not with AWS's policy
  simulator or a live installation. Validate on a test account before production.
