/**
 * Consistency guard: the page scroller contains what it scrolls.
 *
 * The authenticated shell is a 100vh box (`div.flex.h-screen`) holding the
 * sidebar and a column of Header + `<main>`, and each page scrolls INSIDE its
 * own `<main class="overflow-y-auto">`. Nothing at the document root is meant to
 * scroll or to move.
 *
 * An overflow container does NOT clip a descendant whose containing block is one
 * of its own ancestors. `sr-only` is `position: absolute`, so with nothing
 * positioned between it and the document it resolves against the initial
 * containing block and escapes `<main>` entirely. `documentElement.scrollHeight`
 * then equals that box's document position rather than the shell's height, and
 * once page content pushes it below the fold the root becomes genuinely
 * scrollable and the whole app can be dragged out of view, sidebar included.
 *
 * Reported 2026-08-15 on the dashboard at a 842px viewport: doc 1201, so 359px
 * of root overflow, traced to the cost chart's visually hidden table.
 *
 * This is asserted against the stylesheet rather than in jsdom because jsdom
 * implements no layout, so no rendered assertion can observe containing blocks
 * or scroll behavior. It follows the same source-level approach as
 * brandColor.test.ts.
 */

import * as fs from "fs";
import * as path from "path";

const GLOBALS = path.join(__dirname, "..", "..", "src", "app", "globals.css");

/** Strip comments so a rule named only in prose cannot satisfy the guard. */
function css(): string {
  return fs.readFileSync(GLOBALS, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
}

test("the page scroller is a containing block for absolute descendants", () => {
  const text = css();

  // Reproduced by pushing the widget below the fold: doc=1178 against a 842
  // viewport, 336px of root overflow, with doc exactly equal to the hidden box's
  // document position. With <main> positioned: doc=842, overflow=0, and the
  // widget's own layout unchanged.
  const rule = text.match(/(^|[},;\s])main[^{}]*\{[^{}]*position\s*:\s*relative[^{}]*\}/m);
  expect(rule).not.toBeNull();
});
