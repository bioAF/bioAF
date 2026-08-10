/**
 * Consistency guard: the app has ONE primary action color.
 *
 * The UX review scored Consistency 1/4, and "two blues" was a named cause: the
 * brand primary is `bioaf-600` (#0284c7) but ~90 call sites still used Tailwind's
 * default `blue-600` (#2563eb) for buttons, links, selected states and progress
 * chrome, so the same affordance rendered in two different blues depending on
 * which page you were on.
 *
 * The rule this test encodes:
 *
 *  1. Filled action buttons and selected-state fills are ALWAYS the brand:
 *     `bg-bioaf-600` / `hover:bg-bioaf-700`.
 *  2. Text links, text buttons and interactive icons on a neutral surface are
 *     `text-bioaf-600` / `hover:text-bioaf-700`.
 *  3. Content inside a semantic-blue info panel (`bg-blue-50` + `border-blue-200`)
 *     stays in the blue family, but uses the tint shades `blue-700`/`blue-800`,
 *     never the retired `blue-600` action shade.
 *
 * So `blue-600` is retired outright, and `hover:bg-blue-700` (which only ever
 * paired with a `bg-blue-600` base) goes with it. The remaining blue tints
 * (`bg-blue-50/100`, `text-blue-700/800/900`, `border-blue-200`) are legitimate
 * semantic status/info colors and are deliberately NOT covered here.
 */

import * as fs from "fs";
import * as path from "path";

const SRC = path.join(__dirname, "..", "..", "src");

/** Class fragments that are retired app-wide, with why. */
const BANNED: { pattern: RegExp; why: string }[] = [
  { pattern: /blue-600/, why: "retired action shade; use the brand bioaf-600 (or a blue-700 tint inside a blue info panel)" },
  { pattern: /hover:bg-blue-700/, why: "hover partner of the retired bg-blue-600; use hover:bg-bioaf-700" },
];

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...sourceFiles(full));
      continue;
    }
    if (/\.(tsx?|css)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

describe("brand color consistency", () => {
  it("uses one primary action color (bioaf), not Tailwind's default blue-600", () => {
    const offenders: string[] = [];

    for (const file of sourceFiles(SRC)) {
      const lines = fs.readFileSync(file, "utf8").split("\n");
      lines.forEach((line, i) => {
        for (const { pattern, why } of BANNED) {
          if (pattern.test(line)) {
            offenders.push(`${path.relative(SRC, file)}:${i + 1}: ${pattern.source} (${why})`);
          }
        }
      });
    }

    expect(offenders).toEqual([]);
  });
});
