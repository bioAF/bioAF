### Fixes

- Single-cell viewer (cellxgene) deployments no longer fail to launch or tear
  down after the compute cluster is rebuilt. The adapter now refreshes its
  Kubernetes connection when the cluster's address changes, instead of holding
  onto a stale connection until the backend is restarted.
