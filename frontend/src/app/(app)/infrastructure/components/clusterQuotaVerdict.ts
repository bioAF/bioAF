/** Presenting what a node pool's cloud quota will actually allow.
 *
 *  An operator set this pool to pd-balanced at 500 GB. The form accepted it, terraform applied it,
 *  GKE reported the pool RUNNING, and it could not create a single node: pd-balanced bills to the
 *  regional SSD_TOTAL_GB quota, whose limit was 500 GB. Every signal said success. A study sat in
 *  `running` for 35 minutes behind pods that could never be placed, and the only evidence anywhere
 *  was a QUOTA_EXCEEDED line in the GCE audit log.
 *
 *  Two severities, because the two things that can be wrong differ in kind:
 *
 *  - **block**: the pool cannot build one node, so every run against it hangs forever.
 *  - **warn**: it builds some but not all of the requested nodes. That pool works, just with less
 *    concurrency, so it is reported and NOT refused, and the operator's typed value is never
 *    rewritten.
 *
 *  Quota metric names (`SSD_TOTAL_GB`) are shown deliberately rather than softened. They are what
 *  the cloud's own error text and its quota-increase console are keyed on, so an operator handed
 *  that string can act on it. The plain-English sentence carries the meaning; the identifier
 *  carries the next step.
 */

export type QuotaStatus = "ok" | "warn" | "block" | "unverified";

export interface QuotaVerdict {
  status: QuotaStatus;
  achievable_nodes: number | null;
  binding_metric: string | null;
  message: string;
}

export type VerdictTone = "error" | "warning" | "info";

export interface VerdictDisplay {
  text: string;
  tone: VerdictTone;
}

/** True only when the proposed pool could not create a single node.
 *
 *  Deliberately narrow. A partially satisfiable pool still runs, and an unreadable quota must never
 *  stop an operator: a missing IAM role or a cloud API blip would otherwise make this page unusable.
 *  A verdict that has not arrived yet also never blocks, so the form is usable before it lands.
 */
export function applyIsBlocked(verdict: QuotaVerdict | null | undefined): boolean {
  return verdict?.status === "block";
}

/** What to show the operator about a proposed change, or null when it simply fits. */
export function describeVerdict(verdict: QuotaVerdict | null | undefined): VerdictDisplay | null {
  if (!verdict) return null;

  switch (verdict.status) {
    case "block":
      // The backend sentence carries the numbers (what is free, what one node needs). This
      // prefix carries the consequence, which is the part "tight" failed to convey last time.
      return {
        text: `These settings would build no nodes at all. ${verdict.message}`,
        tone: "error",
      };
    case "warn":
      return { text: verdict.message, tone: "warning" };
    case "unverified":
      return {
        text: "Quota could not be checked, so this pool size is not verified. It will be applied as entered.",
        tone: "info",
      };
    default:
      return null;
  }
}

/** A pool's current buildable capacity, independent of any pending edit.
 *
 *  This exists because "terraform applied successfully" and "the node pool is RUNNING" were both
 *  true of a pool with zero capacity. Without this line, the difference is invisible until a run
 *  tries to use it, which can be days later.
 */
export function describePoolCapacity(
  verdict: QuotaVerdict | null | undefined,
  maxNodes: number,
): string {
  if (!verdict || verdict.status === "unverified" || verdict.achievable_nodes === null) {
    return "Capacity could not be verified against this region's quota.";
  }

  const available = Math.min(verdict.achievable_nodes, maxNodes);
  const base = `${available} of ${maxNodes} nodes available`;

  if (verdict.status === "block") {
    return `${base}: ${verdict.binding_metric} is exhausted in this region.`;
  }
  if (verdict.status === "warn") {
    return `${base}: limited by ${verdict.binding_metric}.`;
  }
  return `${base}.`;
}
