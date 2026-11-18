### Terminal access

- Pipeline run details (catalog and custom runs, while running) and Jupyter or RStudio session details
  (while running or idle) have a **Terminal instructions** section. **Show instructions** first checks with the
  cluster that the recorded workload still exists, belongs to this run or session, and has its main container
  running. It then gives a `kubectl exec` command to run on your own computer and a first-time setup guide for
  this installation's cluster. Requires `notebooks:launch` and a compute backend that supports exec. API keys
  cannot request instructions.
- The command always names an explicit cluster context, namespace, pod and container, so it does not depend on
  your current kubectl context. On GKE the setup guide uses the region or zone the cluster was deployed in, not
  the installation's default region. On EKS the kubeconfig alias includes the AWS account, region and cluster
  name.
- A collapsed **Connection details** section lists the exact workload, pod, container, namespace and cluster
  context, each with a short definition.
- Refusals say what to do: refresh the details when the workload is no longer running, try again when the cluster
  could not be reached in time, or ask an administrator when bioAF's own cluster access or setup is the problem.
  Simulated compute mode offers no terminal instructions.
- Each request writes a `connection_command_generated` audit entry naming the run or session, workload,
  namespace, container and cluster context. It records that instructions were shown, not that anyone connected.
- Open notebook session details stay current, so a session stopped elsewhere loses its instructions.

### Compute

- Cluster status, the stack view's cluster detail and the daily compute cost estimate now read the GKE cluster
  at its deployed location instead of the installation's default region. Installations deployed before bioAF
  recorded that location find it once, by listing the project's clusters across all locations, and record it.
  This needs bioAF's Google Cloud identity to be allowed `container.clusters.list`; otherwise an administrator
  re-applies the compute stack.
- When the cluster cannot be read, the Cluster Status card stays on screen and says why, and the daily cost sync
  keeps the day's existing compute estimate instead of recording $0.

### Fixes

- Orphaned clusters from a failed deploy or teardown are recorded at the cluster's real location, and an
  orphaned cluster is treated as already gone only when Google Cloud confirms it no longer exists. A cluster
  whose check is refused or fails, or whose location is unknown, is shown in the deployment recovery dialog as
  "could not be checked", with the reason. **Clean up all** skips it, and it stays visible beside a resumable or
  still-provisioning deployment.
