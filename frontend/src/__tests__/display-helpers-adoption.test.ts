import { readFileSync, readdirSync } from "fs";
import { join, relative } from "path";

/**
 * Byte sizes and run durations each had several local formatters that printed
 * the same quantity differently. They now have one authority each:
 * `lib/bytes.ts` (named profiles keep each screen's output) and
 * `lib/duration.ts` for the run list and run detail.
 *
 * This guard keeps those authorities single. It does not ban every duration in
 * the app: other screens show different quantities (relative age, ETA) and
 * adopting them is a separate, checked change.
 */

const SRC = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next" || entry.name === "__tests__") continue;
      out.push(...sourceFiles(path));
    } else if (/\.tsx?$/.test(entry.name) && !entry.name.includes(".test.")) {
      out.push(path);
    }
  }
  return out;
}

const FILES = sourceFiles(SRC).map((f) => ({ path: relative(SRC, f), text: readFileSync(f, "utf8") }));

test("byte sizes have one formatter", () => {
  const local = FILES.filter(
    (f) => f.path !== "lib/bytes.ts" && /(function\s+formatBytes\b|\bformatBytes\s*=)/.test(f.text),
  ).map((f) => f.path);
  expect(local).toEqual([]);
});

test.each(["app/(app)/pipelines/runs/page.tsx", "app/(app)/pipelines/runs/[id]/page.tsx"])(
  "%s takes run durations from lib/duration",
  (path) => {
    const text = FILES.find((f) => f.path === path)!.text;
    expect(text).toMatch(/from "@\/lib\/duration"/);
    expect(text).not.toMatch(/function\s+formatDuration\b/);
    expect(text).not.toMatch(/\/\s*3600\b/);
  },
);
