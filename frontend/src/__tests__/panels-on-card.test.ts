/**
 * Panels are spelled once, in `components/ui/Card`.
 *
 * The owner's framing, 2026-08-09: *"I want to ensure I am using shared
 * functions, libraries, definitions, etc. wherever possible. I should not have
 * one-offs all over the place."*
 *
 * There were 195 hand-spelled panels in at least eight spellings of the same
 * idea, the largest split being 140 `bg-white rounded-lg shadow` against 25
 * `bg-surface rounded-lg shadow` -- the same panel, one tokenised and one not.
 * `Card` had shipped in `de3a1d40` and had been called **zero** times.
 *
 * The colour is not what is at stake. `bg-white` resolves to `--bg-white`,
 * which the token layer redefines per theme (globals.css: surface is
 * `255 255 255` light, `22 27 34` dark), so `bg-white` and `bg-surface` paint
 * the same pixels in both themes. That compatibility token exists to carry the
 * hand-spelled sites. What Card buys is one spelling, one padding scale, and a
 * heading that becomes a named region instead of an anonymous div.
 *
 * This is a RATCHET, not a finish line. The count may fall; it may not rise.
 * A guard that fails on unstarted work is one people delete, so this pins the
 * number that is left rather than demanding zero.
 */
import { readFileSync, readdirSync } from "fs";
import { join, relative } from "path";

const SRC = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "__tests__") out.push(...sourceFiles(path));
    } else if (entry.name.endsWith(".tsx") && !entry.name.includes(".test.")) {
      out.push(path);
    }
  }
  return out;
}

const FILES = sourceFiles(SRC);

/** Every hand-spelled panel: a rounded box on the app's surface colour. */
function handSpelledPanels(): string[] {
  const found: string[] = [];
  for (const file of FILES) {
    const src = readFileSync(file, "utf8");
    for (const m of src.matchAll(/className="([^"]*rounded-lg[^"]*)"/g)) {
      const classes = m[1];
      if (!/\b(bg-white|bg-surface)\b/.test(classes)) continue;
      if (!/\b(shadow|shadow-lg|shadow-xl|border)\b/.test(classes)) continue;
      found.push(`${relative(SRC, file)}:${src.slice(0, m.index).split("\n").length}`);
    }
  }
  return found;
}

test("the sweep is looking at the whole tree", () => {
  expect(FILES.length).toBeGreaterThan(200);
});

/**
 * 211 before this matcher's first run, 137 after 74 were converted. Lower this
 * number when more are converted; never raise it. A new panel belongs in `Card`.
 */
const PANEL_CEILING = 137;

test("the hand-spelled panel count does not grow", () => {
  const panels = handSpelledPanels();
  expect(panels.length).toBeLessThanOrEqual(PANEL_CEILING);
});

test("the ceiling tracks reality rather than drifting above it", () => {
  // Guard-the-guard. A ceiling far above the real count silently permits new
  // one-offs, which is exactly what this test exists to stop.
  expect(handSpelledPanels().length).toBeGreaterThan(PANEL_CEILING - 25);
});

test("Card is actually called", () => {
  // It shipped in de3a1d40 and had 0 call sites for four rounds.
  const callers = FILES.filter((f) =>
    /from "@\/components\/ui\/Card"/.test(readFileSync(f, "utf8")),
  );
  expect(callers.length).toBeGreaterThan(10);
});
