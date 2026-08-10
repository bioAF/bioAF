/**
 * Item 6, third pass: states that were reached and never left.
 *
 * Three shapes, all found by reading the sites the assessment named:
 *
 *  1. `setLoadError(...)` on the way down with no `setLoadError(null)` on the way back
 *     up. The Retry button worked, the data arrived, and the failure sentence stayed on
 *     screen anyway. The only way out was a full page reload, which makes a Retry
 *     button that appears not to work.
 *
 *  2. A failure that leaves state exactly as it started, where the render reads that
 *     starting state as "still loading". `setData(null)` and a null `updateCheck` both
 *     mean "loading" to their components, so the spinner ran forever.
 *
 *  3. Two mutually exclusive claims rendered as two independent conditions, so both
 *     appeared at once.
 *
 * These are source-level assertions rather than one mounted test per page, because the
 * defect is structural (a missing reset, a missing branch) and lives in six unrelated
 * components. A behavioural test per component would be six mocked page harnesses
 * asserting the same one-line invariant.
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

describe("a retry that succeeds clears the failure it retried", () => {
  const SETS_AND_CLEARS = [
    "app/(app)/results/plot-archive/page.tsx",
    "app/(app)/results/qc-dashboards/page.tsx",
    "components/data/DatasetBrowser.tsx",
    "app/(app)/notebooks/page.tsx",
    "app/(app)/pipelines/runs/page.tsx",
  ];

  it.each(SETS_AND_CLEARS)("%s resets its load error on success", (rel) => {
    const src = code(rel);
    expect(src).toMatch(/setLoadError\(/);
    expect(src).toMatch(/setLoadError\(null\)/);
  });
});

describe("a failed load is not left looking like a load in progress", () => {
  it("the glossary proposal dialog stops saying 'Loading proposals'", () => {
    const src = code("components/lab-knowledge/LabGlossaryBrowser.tsx");
    // The failure now has its own state and its own branch, so a null `data` no
    // longer means "still loading" unconditionally.
    expect(src).toMatch(/setProposalsFailed\(true\)/);
    expect(src).toMatch(/data-testid="review-load-failed"/);
  });

  it("Platform Info stops saying 'Loading version information'", () => {
    const src = code("app/(app)/settings/info/page.tsx");
    expect(src).toMatch(/setVersionLoadFailed\(true\)/);
    expect(src).toMatch(/data-testid="version-load-failed"/);
  });
});

describe("two claims that contradict each other are not rendered together", () => {
  it("notebooks says either 'could not load' or 'no active sessions', never both", () => {
    const src = code("app/(app)/notebooks/page.tsx");
    // The two rows are one ternary chain now, not two independent `&&` conditions.
    expect(src).toMatch(/loadError \?[\s\S]{0,1400}: sessions\.length === 0 \?/);
  });

  it("notebooks spans the full width of its 8-column table", () => {
    const src = code("app/(app)/notebooks/page.tsx");
    const headers = (src.match(/<th\b/g) ?? []).length;
    expect(headers).toBe(8);
    // Both filler rows were colSpan={7}, so each stopped a column short.
    expect(src).not.toMatch(/colSpan=\{7\}/);
  });

  it("the audit log does not print a stale total over a failed table", () => {
    const src = code("app/(app)/settings/audit-log/page.tsx");
    expect(src).toMatch(/!loadError && \(\s*<div[^>]*>\s*\{total\}/);
  });
});

describe("a failure is not reported while the write goes ahead anyway", () => {
  it("DatasetBrowser reads every experiment before it posts any samples", () => {
    const src = code("components/data/DatasetBrowser.tsx");
    const loop = /for \(const ds of selectedDs\) \{([\s\S]*?)\n      \}/.exec(src);
    expect(loop).not.toBeNull();
    // A try/catch inside the loop is what let a failed read be toasted as a failure
    // and then followed by a POST of the IDs that had been collected so far.
    expect(loop![1]).not.toMatch(/catch/);
  });

  it("DatasetBrowser's outer catch reports, rather than claiming the api client did", () => {
    const src = code("components/data/DatasetBrowser.tsx");
    // lib/api.ts only throws; it reports nothing. The old comment said otherwise and
    // the block was empty, so a failed POST was completely silent.
    expect(src).toMatch(/logError\("adding the selected datasets to a project"/);
    expect(src).toMatch(/toast\.error\("The datasets could not be added/);
  });
});
