# ADR-042: Spot preemption-aware retry strategy for pipeline execution

## Status

Accepted

## Context

bioAF runs bioinformatics pipelines (nf-core/scrnaseq, nf-core/rnaseq, etc.) on GKE using Spot
VMs for cost efficiency. Spot VMs can be reclaimed by GCP at any time with 30 seconds notice,
killing running containers with SIGTERM (exit code 143).

nf-core pipelines use a standard retry strategy that multiplies requested CPU and memory by
`task.attempt` on each retry, regardless of failure cause. When a task is killed by Spot
preemption (exit 143), the retry requests double resources. If the doubled request exceeds the
largest node SKU in the autoscaler pool, the retry pod sits Pending indefinitely.

This caused a 5+ hour stall on a scRNA-seq run where STAR genome generation was preempted after
15 minutes of healthy execution. The retry requested ~144 GB memory, exceeding the n2-highmem-16
node's 122 GB allocatable. The pod was unschedulable and Nextflow has no scheduling timeout.

Different pipelines have different resource profiles. scRNA-seq with STAR needs up to 120 GB for
genome indexing. Bulk RNA-seq is similar. fetchngs is lightweight. The retry strategy and resource
limits must accommodate all of these without per-pipeline hardcoding.

## Decision

### 1. Add `resourceLimits` to the generated Nextflow config

Set `resourceLimits` to match the largest node SKU in the pipeline pool. This prevents Nextflow
from ever submitting a pod that exceeds node capacity, regardless of retry escalation. The limits
are derived from the cluster configuration, not hardcoded per pipeline.

### 2. Add preemption-aware error strategy

Exit codes 143 (SIGTERM), 137 (SIGKILL), and 247 (OOMKilled complement) from Spot preemption or
node loss should retry without escalating resources. Genuine task failures (nonzero exit from the
pipeline process itself) continue to use nf-core's standard escalating retry.

### 3. Keep Spot VMs for the pipeline pool

Spot saves 60-70% on compute costs. With proper retry handling, preemption becomes a recoverable
event rather than a pipeline-ending one. The cost savings are significant for the target audience
(academic and small biotech labs).

### 4. Cap max retries

Set `maxRetries = 3` to prevent infinite retry loops. If a task fails 3 times, the pipeline fails
with a clear error rather than running indefinitely.

## Implementation

The generated Nextflow config (`_build_nextflow_k8s_config`) gains a process block:

```groovy
process {
    resourceLimits = [cpus: <pool_max_cpus>, memory: '<pool_max_memory>.GB']
    maxRetries = 3
    errorStrategy = {
        if (task.exitStatus in [143, 137, 247]) {
            return task.attempt <= 3 ? 'retry' : 'finish'
        }
        return task.attempt <= 2 ? 'retry' : 'finish'
    }
}
```

The `resourceLimits` values are derived from the pipeline pool's machine type at config generation
time, not hardcoded. This adapts automatically if the cluster SKU changes.

nf-core pipelines already define per-process resource requests via labels (`process_high`,
`process_medium`, etc.) and `max_memory`/`max_cpus` params. The `resourceLimits` directive acts as
a hard ceiling that Nextflow enforces before submitting pods. It does not override nf-core's
per-process definitions -- it caps them.

## Consequences

- Spot preemption retries without resource escalation, so the retry pod fits on the same node SKU
- No retry can request more than a single node can provide
- Different pipelines continue to define their own resource profiles via nf-core labels
- Cost savings from Spot VMs are preserved
- Pipeline runs that genuinely need more resources than the node SKU provides will fail after
  max retries with a clear error, rather than hanging indefinitely
