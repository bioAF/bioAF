# AWS permissions reconciliation (existing installations)

New AWS installations get installation-scoped permissions from `install-aws.sh`. An installation
set up before that keeps the old, broad `bioaf-app-starter` policy until an operator moves it over.
Updating the bioAF application does not change IAM: the app is not allowed to change its own
permissions.

## What changes

- **The `bioaf-app` role** gets the policy in `installer/aws_iam.sh` in place of
  `bioaf-app-starter`. Reads stay account-wide where AWS cannot scope them. Changes are limited to:
  - EC2 resources tagged `bioaf:installation=<account>-<region>`;
  - security groups and routes in the application VPC;
  - `bioaf-*` EKS, ECR, CodeBuild and log resources in this account and region;
  - IAM roles created under the role boundary.

  The app can no longer edit or pass its own role, remove a boundary, change a role tagged
  `bioaf:protected=true`, move the ownership tag, read control-plane logs, delete control-plane log
  history, or share images.
- **A boundary policy**, `bioaf-app-created-role-boundary`, caps every role the app creates
  (cluster, node, runner, Work Node, autoscaler, EBS CSI and CodeBuild roles). Those roles can never
  write IAM, read secrets or assume other roles, whatever policy is attached to them.
- **Ownership tags.** The narrowed policy needs the `bioaf:installation` tag on bioAF's existing EC2
  resources, its Packer images and snapshots, the app VM and the roles it created. Reconciliation adds
  them.

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

Read the lists. Everything on them becomes this installation's to change and clean up. Answer `n`
if anything does not belong to this installation; nothing is changed. Remove the wrong tag from
that resource yourself and run reconciliation again.

On `y` it:

1. creates or updates the boundary policy;
2. puts the boundary on each listed role and tags it;
3. tags the listed EC2 resources and the app VM;
4. replaces the `bioaf-app` policy and deletes `bioaf-app-starter`.

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
- Packer tags its images and snapshots after creating them, so the image-build role can tag images
  and snapshots as owned. It runs only bioAF's own buildspec.
- These documents were checked with a static evaluator in the test suite, not with AWS's policy
  simulator or a live installation. Validate on a test account before production.
