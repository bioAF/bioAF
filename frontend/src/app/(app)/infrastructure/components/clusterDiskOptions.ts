/** Node disk choices for the pipeline pool.
 *
 *  The disk is the node's EPHEMERAL storage: the Nextflow work directory, the container images and
 *  (with Fusion) the local cache of the cloud work dir all share it. A step that outgrows what is
 *  left is EVICTED by kubelet rather than failed by the tool, which reads as a mysterious retry
 *  rather than as "out of disk".
 *
 *  Machine size, node count and spot were all settable here while this was a literal in terraform,
 *  so on run 43 the only lever that bound the workload was the one nobody could reach.
 */

export const DISK_TYPE_OPTIONS = [
  { value: "pd-standard", label: "Standard (HDD) - cheapest, slowest" },
  { value: "pd-balanced", label: "Balanced (SSD) - ~2x cost, much faster" },
  { value: "pd-ssd", label: "SSD - fastest, highest cost" },
] as const;

/** Roughly what one genome-scale alignment consumes of a node's disk, measured on run 43: a STAR
 *  step on a human reference held ~80 GB (a ~30 GB index, 12-18 GB of reads, plus outputs and the
 *  Fusion cache). */
const GENOME_STEP_GB = 80;

/** Headroom the node needs for itself: the OS, container images, and kubelet's ~10 GB eviction
 *  threshold. */
const NODE_OVERHEAD_GB = 20;

/** Slack a step needs ON TOP of its typical size. Run 43 is why this is not zero: a 100 GB disk
 *  leaves 80 GB usable, which is exactly one step's typical footprint, and it still evicted --
 *  because usage fluctuates and "exactly enough" is not enough. A step must fit with room over. */
const STEP_SLACK_GB = 20;

/** Plain-language guidance for a chosen disk size. */
export function describeDiskFor(sizeGb: number): string {
  const usable = sizeGb - NODE_OVERHEAD_GB;
  const perStep = GENOME_STEP_GB + STEP_SLACK_GB;
  const steps = Math.floor(usable / perStep);
  if (steps < 1) {
    return (
      `Tight: a single genome-scale alignment uses about ${GENOME_STEP_GB} GB and ${NODE_OVERHEAD_GB} GB of ` +
      `this disk goes to the system, so steps may be evicted part-way through.`
    );
  }
  return `Room for ${steps} genome-scale step${steps === 1 ? "" : "s"} per node (about ${GENOME_STEP_GB} GB each).`;
}
