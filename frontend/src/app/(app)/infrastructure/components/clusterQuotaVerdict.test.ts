import {
  applyIsBlocked,
  describeVerdict,
  describePoolCapacity,
  type QuotaVerdict,
} from "./clusterQuotaVerdict";

/**
 * An operator moved this pool to pd-balanced at 500 GB. The form accepted it, terraform applied it,
 * GKE reported the pool RUNNING, and it could not create one node: pd-balanced bills to
 * SSD_TOTAL_GB, whose regional limit was 500 GB. The first thing that noticed was a study stuck in
 * `running` behind pods that would never be placed.
 */

const block: QuotaVerdict = {
  status: "block",
  achievable_nodes: 0,
  binding_metric: "SSD_TOTAL_GB",
  message: "SSD_TOTAL_GB has 470 free in this region, but one node needs 500.",
};

const warn: QuotaVerdict = {
  status: "warn",
  achievable_nodes: 8,
  binding_metric: "DISKS_TOTAL_GB",
  message: "DISKS_TOTAL_GB supports 8 of 20 nodes at these settings.",
};

const ok: QuotaVerdict = { status: "ok", achievable_nodes: 40, binding_metric: "CPUS", message: "" };

const unverified: QuotaVerdict = {
  status: "unverified",
  achievable_nodes: null,
  binding_metric: null,
  message: "Could not read this region's quota.",
};

// -- what stops an apply -----------------------------------------------------

test("only a pool that cannot build one node blocks Apply", () => {
  expect(applyIsBlocked(block)).toBe(true);

  // A pool that runs fewer nodes than requested still runs. Refusing it would be wrong.
  expect(applyIsBlocked(warn)).toBe(false);
  expect(applyIsBlocked(ok)).toBe(false);
});

test("an unreadable quota never blocks Apply", () => {
  // A missing IAM role or a cloud API blip must not make this page unusable.
  expect(applyIsBlocked(unverified)).toBe(false);
});

test("a missing verdict never blocks Apply", () => {
  // The verdict arrives after the form renders; the form must work before it lands.
  expect(applyIsBlocked(null)).toBe(false);
  expect(applyIsBlocked(undefined)).toBe(false);
});

// -- what the operator reads -------------------------------------------------

test("the blocking message names the quota, because that is what they must go raise", () => {
  const text = describeVerdict(block)!.text;
  expect(text).toMatch(/SSD_TOTAL_GB/);
  expect(text).toMatch(/470/);
});

test("the blocking message says the pool would build nothing, not merely that it is tight", () => {
  // "Tight" was already the copy for a 100 GB disk and it under-sold an eviction.
  expect(describeVerdict(block)!.text).toMatch(/no nodes|not.*a single node|zero/i);
});

test("a reduced-concurrency warning names both counts and does not read as an error", () => {
  const verdict = describeVerdict(warn)!;
  expect(verdict.text).toMatch(/8/);
  expect(verdict.text).toMatch(/20/);
  expect(verdict.tone).toBe("warning");
});

test("an unreadable quota reads as unverified, not as a failure", () => {
  const verdict = describeVerdict(unverified)!;
  expect(verdict.tone).toBe("info");
  expect(verdict.text).toMatch(/not.*verif|could not.*check|unable/i);
});

test("a config that fits says nothing", () => {
  expect(describeVerdict(ok)).toBeNull();
});

// -- the pool viability indicator -------------------------------------------

test("a pool that can build nothing reports zero of its configured maximum", () => {
  // Terraform said "completed" and GKE said "RUNNING" for exactly this pool.
  const text = describePoolCapacity(block, 20);
  expect(text).toMatch(/0 of 20/);
  expect(text).toMatch(/SSD_TOTAL_GB/);
});

test("a partially satisfiable pool reports what it can actually run", () => {
  expect(describePoolCapacity(warn, 20)).toMatch(/8 of 20/);
});

test("a fully satisfiable pool is reported plainly", () => {
  expect(describePoolCapacity(ok, 20)).toMatch(/20 of 20/);
});

test("an unverified pool does not claim a capacity it does not know", () => {
  const text = describePoolCapacity(unverified, 20);
  expect(text).not.toMatch(/\d+ of 20/);
  expect(text).toMatch(/not.*verif|could not|unable/i);
});
