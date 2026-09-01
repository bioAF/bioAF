import { DISK_TYPE_OPTIONS, describeDiskFor } from "./clusterDiskOptions";

/**
 * Run 43's alignments were evicted for want of node disk: each STAR step used ~80 GB of a 100 GB
 * node, and the request was 0. Machine size, max nodes and spot were all on this page; the disk was
 * a literal in terraform, so the one setting that bounded the workload was the one nobody could
 * reach.
 */

test("offers the three GCE disk types, slowest first as today's default", () => {
  expect(DISK_TYPE_OPTIONS.map((o) => o.value)).toEqual(["pd-standard", "pd-balanced", "pd-ssd"]);
});

test("each disk type says what it is for, not just what it is called", () => {
  // "pd-balanced" means nothing to a lab without a bioinformatics FTE.
  for (const opt of DISK_TYPE_OPTIONS) {
    expect(opt.label.length).toBeGreaterThan(opt.value.length);
  }
  expect(DISK_TYPE_OPTIONS.find((o) => o.value === "pd-standard")!.label).toMatch(/slow|hdd/i);
});

test("warns when the disk is too small for a genome-scale step", () => {
  // The observed failure, in the units the user sets.
  expect(describeDiskFor(100)).toMatch(/evict|too small|tight/i);
  expect(describeDiskFor(500)).not.toMatch(/evict|too small/i);
});

test("the warning names the real number rather than a vague caution", () => {
  expect(describeDiskFor(100)).toMatch(/80/);
});
