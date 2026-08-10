import { readFileSync } from "fs";
import { join } from "path";

/**
 * The design system was ~80% built and 0% adopted: a semantic token layer with
 * 12 usages against ~4,000 hardcoded gray/white utilities, and 797 raw
 * `<button>` with no primitive. Adoption started on the app's highest-traffic
 * surfaces; this guard keeps those surfaces from drifting back, because the
 * old spellings still work and are what habit produces.
 *
 * It deliberately covers a NAMED LIST rather than the whole tree. The rest of
 * the app has not been converted yet, and a guard that fails everywhere is one
 * people delete.
 */

const SRC = join(__dirname, "..");

const ADOPTED = [
  "app/(app)/experiments/page.tsx",
  "app/(app)/experiments/[id]/page.tsx",
  "app/(app)/activity/page.tsx",
  "app/(app)/notifications/page.tsx",
  "app/(app)/pipelines/catalog/page.tsx",
  "app/(app)/pipelines/runs/page.tsx",
  "components/data/DatasetBrowser.tsx",
];

function read(file: string): string {
  return readFileSync(join(SRC, file), "utf8");
}

test.each(ADOPTED)("%s paints its surfaces with tokens, not hardcoded white", (file) => {
  const src = read(file);
  expect(src).not.toMatch(/\bbg-white\b/);
});

test.each(ADOPTED)("%s takes its body text from the ink ramp", (file) => {
  const src = read(file);
  // gray-600 has no token yet and is left alone on purpose; these three do.
  expect(src).not.toMatch(/\btext-gray-(900|700|500)\b/);
});

test.each(ADOPTED)("%s does not hand-roll the primary or danger button", (file) => {
  const src = read(file);
  const handRolled = [...src.matchAll(/<button\b[\s\S]{0,400}?>/g)]
    .map((m) => m[0])
    .filter((tag) => /bg-(bioaf|red)-600/.test(tag));

  expect(handRolled).toEqual([]);
});

test("the primitives exist and are what the adopted files import", () => {
  expect(() => read("components/ui/Button.tsx")).not.toThrow();
  expect(() => read("components/ui/Card.tsx")).not.toThrow();

  const importers = ADOPTED.filter((f) => /@\/components\/ui\/Button/.test(read(f)));
  expect(importers.length).toBeGreaterThanOrEqual(6);
});
