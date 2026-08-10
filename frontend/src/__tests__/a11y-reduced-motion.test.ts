import { readFileSync } from "fs";
import { join } from "path";

// `LoadingSpinner` covers 63 of the app's spinner sites and carries
// `motion-reduce:animate-none` itself. The other 15 hand-roll `animate-spin`,
// and 10 more use `animate-pulse` for skeletons, so per-site variants would
// have to be remembered every time someone adds one.
//
// A global rule under the media query catches those and anything added later.
// It is scoped to the looping animation utilities on purpose: continuous,
// never-ending motion is the vestibular trigger. This app's transitions are
// almost all `transition-colors`, which is not one, so a blanket
// `transition-duration: 0` reset would change a lot of behaviour to fix nothing.

const CSS = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");

function reducedMotionBlock(): string {
  const start = CSS.search(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  if (start === -1) return "";
  let i = CSS.indexOf("{", start);
  let depth = 0;
  const from = i;
  for (; i < CSS.length; i++) {
    if (CSS[i] === "{") depth++;
    else if (CSS[i] === "}") {
      depth--;
      if (depth === 0) return CSS.slice(from, i + 1);
    }
  }
  return "";
}

test("the stylesheet honours prefers-reduced-motion at all", () => {
  expect(reducedMotionBlock()).not.toBe("");
});

test.each(["animate-spin", "animate-pulse"])(
  "%s stops under reduced motion",
  (util) => {
    const block = reducedMotionBlock();
    expect(block).toContain(`.${util}`);
    expect(block).toMatch(/animation:\s*none/);
  },
);

test("the rule does not fire for users who have not asked for it", () => {
  // A rule outside the media query would freeze every spinner in the app for
  // everyone, and grepping for the class name alone cannot tell the difference.
  const block = reducedMotionBlock();
  const outside = CSS.replace(block, "");
  expect(outside).not.toMatch(/\.animate-spin\s*\{[^}]*animation:\s*none/);
});
