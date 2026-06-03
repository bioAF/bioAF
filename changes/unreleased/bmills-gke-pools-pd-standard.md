### Compute

- GKE node pools (default bootstrap, pipelines, interactive) now use
  `pd-standard` disks instead of GKE's default `pd-balanced`. This
  matches the system and pipeline-head pools, which were already on
  `pd-standard`. The change cuts boot-disk cost by ~60% per node and,
  more importantly, removes the pipeline and interactive pools from
  the `SSD_TOTAL_GB` regional quota: a fresh install previously
  consumed 200 GB of SSD quota at idle (one pipelines node + one
  interactive node at GKE's 100 GB pd-balanced default), and at peak
  could blow past the 300 GB default limit during autoscaler retries.
  Scratch I/O for nf-core pipelines and notebook sessions is dominated
  by GCS round-trips and memory, so the throughput difference is not
  observable in practice.
- The bioAF app VM created by `install-gcp.sh` now provisions its
  boot disk as `pd-standard` instead of `pd-ssd`. Postgres on the app
  VM is far below the workload where SSD makes a measurable
  difference, and this drops the install's SSD footprint to zero.
- The optional Meilisearch component's 20 GB data disk moves from
  `pd-ssd` to `pd-standard`. At bioAF's scale the entire search index
  fits in RAM after warmup, so disk latency only matters for cold
  reads and indexing. Worth revisiting if Meilisearch is ever wired
  into the user-facing search path and a real workload shows up.
- Existing installs: the GKE node pool changes will trigger a
  destroy-and-recreate of the pipelines and interactive pools on next
  `terraform apply` of the compute module. Expect a brief disruption
  while pods reschedule onto the new pool: active notebook sessions
  will disconnect and need to be reopened, and in-flight pipeline
  tasks will rely on Nextflow/Snakemake retry semantics. The app VM
  change is install-time only; existing app VMs keep their `pd-ssd`
  boot disk until manually migrated.
