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

/**
 * Strip comments before scanning. Without this, a comment explaining a defect
 * ("was `setError(\"Failed to load ...\")`") trips the guard that bans it, and the
 * only way out is to stop writing the explanation down. The bundled detector has
 * this exact bug: 2 of its 12 findings on this repo were comments describing code
 * the file deliberately avoids.
 *
 * Line comments only after a newline-or-start boundary, so a `//` inside a URL
 * string ("https://...") is left alone.
 */
function code(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|\n)(\s*)\/\/[^\n]*/g, "$1$2");
}

test("the widget directory is where it is expected to be", () => {
  expect(files.length).toBeGreaterThanOrEqual(15);
});

test("no dashboard widget recovers by reloading the page", () => {
  const offenders = files.filter((f) =>
    /window\.location\.reload/.test(code(readFileSync(join(WIDGETS, f), "utf8"))),
  );
  expect(offenders).toEqual([]);
});

test("every Retry on the dashboard recovers its own widget", () => {
  const offenders: string[] = [];
  for (const f of files) {
    const src = code(readFileSync(join(WIDGETS, f), "utf8"));
    if (!/>\s*Retry\s*</.test(src)) continue;
    // Either the shared hook, or a local loader the widget already had
    // (InfrastructureHealthWidget calls its own loadHealth).
    if (!/useWidgetData|onClick=\{load\w+\}/.test(src)) offenders.push(f);
  }
  expect(offenders).toEqual([]);
});

test("the sixteen that used to reload now share one retry", () => {
  const usingHook = files.filter((f) => /useWidgetData/.test(code(readFileSync(join(WIDGETS, f), "utf8"))));
  expect(usingHook.length).toBeGreaterThanOrEqual(16);
});

test("every widget that can show an error also offers a way back", () => {
  // Was an allowlist of two deliberate exceptions (IngestStatus, QueueDepth).
  // The owner signed off on closing them 2026-08-07, so the exception is gone
  // and the rule is now universal.
  const noRetry = files.filter((f) => {
    const src = code(readFileSync(join(WIDGETS, f), "utf8"));
    return /data-testid="widget-error"/.test(src) && !/>\s*Retry\s*</.test(src);
  });
  expect(noRetry).toEqual([]);
});

/**
 * The defect this exists to prevent, measured on the deployed app: four widgets
 * rendered text byte-identical to their healthy state under a total backend
 * outage, because an inner `.catch` substituted a falsy literal before the
 * rejection could reach `useWidgetData`.
 *
 * A widget may not decide on the user's behalf that a failed request means zero.
 * If the request failed, say so.
 */
test("no widget substitutes a value for a failed request", () => {
  const offenders: string[] = [];
  for (const f of files) {
    const src = code(readFileSync(join(WIDGETS, f), "utf8"));
    src.split("\n").forEach((line, i) => {
      // `.catch(() => ({ total: 0 }))`, `.catch(() => ([]))`, `.catch(() => null)`
      if (/\.catch\(\s*\(\s*\)\s*=>\s*[({[]/.test(line)) {
        offenders.push(`${f}:${i + 1} ${line.trim()}`);
      }
      // `.catch(() => setData(...))` / `.catch(() => setStats(...))`
      if (/\.catch\(\s*\(\s*\)\s*=>\s*set\w+\(/.test(line)) {
        offenders.push(`${f}:${i + 1} ${line.trim()}`);
      }
    });
  }
  expect(offenders).toEqual([]);
});

/**
 * A widget nobody can reach is not a shipped widget. `IngestStatusWidget` was
 * fully built, tested, and named in the Getting Started tour, but was absent from
 * the registry, so no user could ever add it to a dashboard. It was also one of
 * the three widgets an earlier handoff listed as "needs a ruling", which cannot
 * be true of something invisible.
 */
test("every widget in the directory is registered", () => {
  const registry = code(readFileSync(join(WIDGETS, "registry.tsx"), "utf8"));
  const widgetFiles = files.filter((f) => f.endsWith("Widget.tsx"));
  const unregistered = widgetFiles.filter((f) => !registry.includes(f.replace(".tsx", "")));
  expect(unregistered).toEqual([]);
});

/**
 * Thirteen widgets say "X could not be loaded, so nothing is shown here. The
 * technical detail is in the application logs." One said "Failed to load service
 * health". One sentence, one shape.
 */
test("no widget hand-writes its own failure sentence", () => {
  const offenders: string[] = [];
  for (const f of files) {
    const src = code(readFileSync(join(WIDGETS, f), "utf8"));
    if (/setError\(\s*["'`]Failed to load/.test(src)) offenders.push(f);
  }
  expect(offenders).toEqual([]);
});
