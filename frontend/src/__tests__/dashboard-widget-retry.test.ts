import { readFileSync, readdirSync } from "fs";
import { join } from "path";

/**
 * Sixteen dashboard widgets recovered a failed card by reloading the whole
 * page. That discards every other widget's data, re-runs the dashboard's
 * eighteen requests, and loses scroll position: the most expensive possible
 * answer to one failed fetch. They refetch themselves now, through
 * `useWidgetData`, and this keeps the cheap reflex from coming back.
 */

const WIDGETS = join(__dirname, "..", "components", "dashboard");

const files = readdirSync(WIDGETS).filter(
  (f) => f.endsWith(".tsx") && !f.includes(".test."),
);

test("the widget directory is where it is expected to be", () => {
  expect(files.length).toBeGreaterThanOrEqual(15);
});

test("no dashboard widget recovers by reloading the page", () => {
  const offenders = files.filter((f) =>
    /window\.location\.reload/.test(readFileSync(join(WIDGETS, f), "utf8")),
  );
  expect(offenders).toEqual([]);
});

test("every Retry on the dashboard recovers its own widget", () => {
  const offenders: string[] = [];
  for (const f of files) {
    const src = readFileSync(join(WIDGETS, f), "utf8");
    if (!/>\s*Retry\s*</.test(src)) continue;
    // Either the shared hook, or a local loader the widget already had
    // (InfrastructureHealthWidget calls its own loadHealth).
    if (!/useWidgetData|onClick=\{load\w+\}/.test(src)) offenders.push(f);
  }
  expect(offenders).toEqual([]);
});

test("the sixteen that used to reload now share one retry", () => {
  const usingHook = files.filter((f) => /useWidgetData/.test(readFileSync(join(WIDGETS, f), "utf8")));
  expect(usingHook.length).toBeGreaterThanOrEqual(16);
});

test("three widgets still report a failure with no way back, deliberately", () => {
  // IngestStatus, QueueDepth and InfrastructureHealth were not part of this
  // change. The first two show an error with no recovery control at all, which
  // is a finding to raise rather than a control to invent.
  const noRetry = files.filter((f) => {
    const src = readFileSync(join(WIDGETS, f), "utf8");
    return /data-testid="widget-error"/.test(src) && !/>\s*Retry\s*</.test(src);
  });
  expect(noRetry.sort()).toEqual(["IngestStatusWidget.tsx", "QueueDepthWidget.tsx"]);
});
