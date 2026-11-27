### Cloud security

- Enforce public-access prevention on all nine GCP buckets, so a future IAM grant cannot make
  scientific, Terraform-state or backup data public. Signed uploads and downloads keep working.
  Existing installations get it on the seven application buckets through **Check for
  Infrastructure Updates**; an operator adds it to the state and backup buckets with the steps in
  `docs/operations/foundation-bucket-guards.md`.
- Refuse a foundation plan or apply that would recreate an initialized installation's state or
  backup bucket, and point to that procedure instead.
- New AWS clusters get a private-only Kubernetes API, reached by the backend over the existing VPC
  peering. Restricted public access is an explicit opt-in with approved CIDRs; `0.0.0.0/0` is
  refused. An existing cluster keeps its current endpoint, reported as pending hardening, until an
  administrator runs the reviewed cutover in `docs/operations/eks-endpoint-and-logs.md`.
- Export all five EKS control-plane log types to CloudWatch with 30-day retention (configurable).
  The log group is kept on teardown, and reads are for operators only.
- Narrow the `bioaf-app` AWS role to this installation's own resources, using installer-owned
  managed policies. The app can no longer write role policies, change its own permissions, pass or
  claim roles it does not own, claim resources by retagging, or share images. Roles it creates hold
  only AWS's EKS policies and installer-owned policies under a permissions boundary. The cluster
  autoscaler can scale only bioAF node groups. The image-build role is confined to its own builds
  and logs and can no longer remove images; superseded work-node AMIs are now removed by an
  operator.
  Existing AWS installations move over with `./install-aws.sh --reconcile-permissions`
  (`docs/operations/aws-permissions-reconcile.md`) before their next infrastructure update.
