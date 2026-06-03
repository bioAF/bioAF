### Infrastructure menu

- Removed the Infrastructure > Compute page along with the Job Browser and
  Quotas pages it linked to. The status metrics they showed were duplicated by
  the dashboard and Cost Center, the node-pool internals were unactionable for
  non-technical users, and the configuration knobs already live in the
  Component Catalog. Job cancel/resubmit and per-user spend controls will be
  reconsidered in the experiment, pipeline, and Cost Center surfaces where
  users actually look for them.
- Stale bookmarks to `/compute`, `/compute/cluster`, `/compute/jobs`, and
  `/compute/quotas` now redirect to the dashboard.
- The backend endpoints those pages consumed are removed:
  `/api/v1/infrastructure/compute/{status,metrics}`, `/api/compute/cluster`,
  `/api/compute/jobs*`, `/api/compute/budget`, and `/api/quotas*`. The BAL
  compute adapter (`get_compute_adapter`) is unaffected; it remains the
  abstraction used by pipeline runs, work nodes, and the cost service.
