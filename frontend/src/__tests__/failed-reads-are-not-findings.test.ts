/**
 * Item 6, fourth pass. One rule, thirteen sites:
 *
 *   A read that failed must not be rendered as a fact about the user's data,
 *   and must not arm a write.
 *
 * Every site here was proven on the deployed demo by failing exactly one
 * endpoint and reading the sentence the app then showed. They fall into three
 * shapes:
 *
 *  1. A rejection sets an empty collection, and the render turns that emptiness
 *     into a claim -- "No publishable h5ad files available.", "No other versions
 *     of this reference exist.", "No files found for this experiment."
 *
 *  2. A rejection is swallowed entirely behind a control, so the control is
 *     dead: clicking a QC dashboard row whose GET 500s left the page text
 *     BYTE-IDENTICAL, with no message anywhere.
 *
 *  3. A rejection leaves a picker empty and the write behind it still armed:
 *     "Add to Project" opened offering only ["Choose a project...", "+ Create
 *     New Project"] while the user's two real projects were invisible, so the
 *     only path forward created a duplicate.
 *
 * These are source assertions rather than thirteen mounted page harnesses,
 * because the defect is structural (a missing failure branch) and the pages
 * involved each need a different set of mocked providers to render at all. The
 * five components whose failure is a *rendered sentence* do have behavioural
 * tests, colocated next to them.
 */
import { readFileSync } from "fs";
import { join } from "path";

const SRC = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

/** Strip comments, so a comment ABOUT a defect cannot satisfy the guard that bans it. */
function code(rel: string): string {
  return read(rel)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

describe("an empty collection reached from a rejection is not rendered as a finding", () => {
  const CASES: Array<[string, string, RegExp]> = [
    [
      "the cellxgene publish picker",
      "app/(app)/results/cellxgene/page.tsx",
      /setPublishableFilesFailed\(true\)/,
    ],
    [
      "the cellxgene publication list",
      "app/(app)/results/cellxgene/page.tsx",
      /setPublicationsError\(/,
    ],
    [
      "a reference's other versions",
      "app/(app)/data/references/[id]/page.tsx",
      /setVersionsFailed\(true\)/,
    ],
    [
      "the notebook launch file picker",
      "app/(app)/notebooks/page.tsx",
      /setExperimentFilesFailed\(true\)/,
    ],
    [
      "the work-node launch file picker",
      "app/(app)/workbench/work-nodes/page.tsx",
      /setExperimentFilesFailed\(true\)/,
    ],
    [
      "the custom-pipeline launch file picker",
      "components/pipelines/CustomPipelineLaunchDialog.tsx",
      /setFilesFailed\(true\)/,
    ],
    [
      "the project sample picker",
      "app/(app)/projects/[id]/page.tsx",
      /setAvailableSamplesFailed\(true\)/,
    ],
    [
      "a run's pod system logs",
      "app/(app)/pipelines/runs/[id]/page.tsx",
      /setSystemLogsFailed\(true\)/,
    ],
  ];

  it.each(CASES)("%s", (_name, rel, marker) => {
    expect(code(rel)).toMatch(marker);
  });

  it("the cellxgene picker no longer claims the user has no h5ad files", () => {
    const src = code("app/(app)/results/cellxgene/page.tsx");
    // The sentence may still render, but only behind a successful read.
    const claim = src.indexOf("No publishable h5ad files available");
    expect(claim).toBeGreaterThan(-1);
    expect(src.slice(Math.max(0, claim - 400), claim)).toMatch(/publishableFilesFailed/);
  });

  it("the reference page no longer claims a version history it could not read", () => {
    const src = code("app/(app)/data/references/[id]/page.tsx");
    const claim = src.indexOf("No other versions of this reference exist");
    expect(claim).toBeGreaterThan(-1);
    expect(src.slice(Math.max(0, claim - 400), claim)).toMatch(/versionsFailed/);
  });
});

describe("a control whose request failed is not silently dead", () => {
  it("opening a QC dashboard reports a failure", () => {
    const src = code("app/(app)/results/qc-dashboards/page.tsx");
    // Three swallowed sites: the row click, Regenerate (a POST), and the deep
    // link the "results ready" notification lands on.
    expect(src).not.toMatch(/catch\s*\{\s*\}/);
    expect(src.match(/logError\(/g)?.length ?? 0).toBeGreaterThanOrEqual(4);
    expect(src).toMatch(/setActionError\(/);
  });

  it("Regenerate does not report a failed POST as nothing at all", () => {
    const src = code("app/(app)/results/qc-dashboards/page.tsx");
    const fn = src.slice(src.indexOf("const regenerateQc"));
    expect(fn.slice(0, 900)).toMatch(/logError\(/);
    expect(fn.slice(0, 900)).toMatch(/setActionError\(/);
  });
});

describe("a read that failed does not arm the write behind it", () => {
  it("Add to Project will not submit over an unread project list", () => {
    const src = code("components/data/DatasetBrowser.tsx");
    expect(src).toMatch(/setProjectsFailed\(true\)/);
    // The submit is gated on the read having succeeded, not merely on a
    // selection having been made.
    expect(src).toMatch(/disabled=\{[^}]*projectsFailed/);
  });

  it("a custom pipeline will not launch on an input list that failed to load", () => {
    const src = code("components/pipelines/CustomPipelineLaunchDialog.tsx");
    expect(src).toMatch(/setFilesFailed\(true\)/);
    // The Launch button reads one computed flag, so the guard follows it there
    // rather than to the attribute.
    const gate = src.slice(src.indexOf("const launchDisabled"));
    expect(gate.slice(0, 300)).toMatch(/filesFailed/);
    expect(src).toMatch(/disabled=\{launchDisabled\}/);
  });

  it("a review cannot be filed over a review history that failed to load", () => {
    const src = code("components/experiments/ReviewPanel.tsx");
    expect(src).toMatch(/setReviewsFailed\(true\)/);
    expect(src).toMatch(/disabled=\{[^}]*reviewsFailed/);
  });
});

describe("the notification bell does not report an outage as zero", () => {
  it("a failed count is distinguishable from a real zero", () => {
    const src = code("components/notifications/NotificationBell.tsx");
    expect(src).toMatch(/setCountFailed\(true\)/);
    expect(src).toMatch(/notification-count-unknown/);
    // The button had no accessible name at all, so the state was unreachable
    // to a screen reader even once it rendered.
    expect(src).toMatch(/aria-label=/);
  });

  it("the dropdown does not claim there are none when the list failed", () => {
    const src = code("components/notifications/NotificationDropdown.tsx");
    expect(src).toMatch(/notifications-load-failed/);
    const claim = src.indexOf("No notifications");
    expect(claim).toBeGreaterThan(-1);
    // The claim now sits in a later branch of the same ternary chain, so the
    // failure test is reached first.
    expect(src.slice(Math.max(0, claim - 800), claim)).toMatch(/loadFailed \?/);
  });
});
