/**
 * Consistency guard: the app shell owns its scrolling, and the root never moves.
 *
 * The authenticated shell is a 100vh box (`div.flex.h-screen`) holding the
 * sidebar and a column of Header + `<main>`, and each page scrolls INSIDE its
 * own `<main class="overflow-y-auto">`. Nothing at the document root is meant to
 * scroll, and measured on the deployed demo the root indeed has zero scrollable
 * height at every viewport tested.
 *
 * Zero scrollable height is not enough on its own. When an inner scroller
 * reaches its end, the browser chains the remaining gesture to its ancestors and
 * finally to the root, and macOS elastic overscroll then drags the ENTIRE app,
 * sidebar and header included, out of view for as long as the gesture continues.
 * Reported 2026-08-15: scroll to the bottom of the dashboard, pause, keep
 * scrolling, and the whole page lifts away.
 *
 * `overscroll-behavior: none` on the root stops the chain at the shell without
 * affecting any inner scroller, which keeps working normally.
 *
 * This is asserted against the stylesheet rather than in jsdom because jsdom
 * implements no layout, so no rendered assertion can see scroll behavior at all.
 * It follows the same source-level approach as brandColor.test.ts.
 */

import * as fs from "fs";
import * as path from "path";

const GLOBALS = path.join(__dirname, "..", "..", "src", "app", "globals.css");

/** Strip comments so a rule named only in prose cannot satisfy the guard. */
function css(): string {
  return fs.readFileSync(GLOBALS, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
}

test("the document root refuses scroll chaining", () => {
  const text = css();

  // The declaration exists, and is `none`/`contain` rather than the default.
  const rule = text.match(/(^|[},;\s])(html|body)[^{}]*\{[^{}]*overscroll-behavior[^;{}]*;/m);
  expect(rule).not.toBeNull();
  expect(rule![0]).toMatch(/overscroll-behavior\s*:\s*(none|contain)/);
});

test("the rule covers both html and body", () => {
  const text = css();

  // Safari applies root overscroll from <body> while Chromium propagates from
  // <html>, so naming only one leaves the other browser bouncing.
  const block = text.match(/[^{}]*\{[^{}]*overscroll-behavior\s*:\s*(none|contain)[^{}]*\}/);
  expect(block).not.toBeNull();
  const selector = block![0].split("{")[0];
  expect(selector).toMatch(/\bhtml\b/);
  expect(selector).toMatch(/\bbody\b/);
});
