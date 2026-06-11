### Fixes

- Single-cell viewer (cellxgene) instances now launch on installs that use the
  default (metadata) credential mode. Previously the viewer pod required a
  service-account key file that those installs do not have, so it never started
  and the launch timed out; the pod now authenticates via Workload Identity
  (the runner service account), and the key file is only used on installs that
  store one.
- Single-cell viewer (cellxgene) deployments no longer fail to launch or tear
  down after the compute cluster is rebuilt. The adapter now refreshes its
  Kubernetes connection when the cluster's address changes, instead of holding
  onto a stale connection until the backend is restarted.
