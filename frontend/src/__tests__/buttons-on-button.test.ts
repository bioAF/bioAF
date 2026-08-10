/**
 * The primary and danger actions are spelled once, in `components/ui/Button`.
 *
 * The owner's framing, 2026-08-09: *"I want to ensure I am using shared
 * functions, libraries, definitions, etc. wherever possible. I should not have
 * one-offs all over the place."*
 *
 * Measured at `8cb0a9d8`: 731 raw `<button>` in 166 files. Classifying them
 * (`local/ui_rework_v2/verification/button-census.js`) showed most are not
 * candidates at all -- 23 filter chips with a selected state Button has no
 * variant for, 21 toggles, 17 table-row affordances, 13 icon-only controls,
 * 22 dialog dismissals the Modal primitive already owns -- so the honest target
 * was 409, not 731.
 *
 * Inside that, ONE control was spelled dozens of ways across 215 instances:
 * the primary action. `px-4` and `px-6`; `py-2` and `py-1.5`; `rounded`,
 * `rounded-md` and `rounded-lg`; with and without `disabled:opacity-50`. That
 * is the split this guard exists to stop reopening, and it is the same finding
 * as the panels, in a different element.
 *
 * This is a RATCHET, not a finish line. The count may fall; it may not rise.
 * A guard that fails on unstarted work is one people delete, so it pins what is
 * left rather than demanding zero.
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

/** Comments first: a commented-out button is not a button. */
function stripComments(src: string): string {
  return src
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

/**
 * End index of the `>` that closes the opening tag starting at `start`.
 *
 * Not a regex, and this matters. `<button\b[^>]*?>` stops at the first `>`,
 * which for `onClick={() => save()}` is the one inside the arrow function. The
 * first version of this guard used that pattern and could therefore not see a
 * hand-rolled button with an inline handler, which is most of them: it reported
 * 43 where the real number was 85.
 */
function endOfOpenTag(src: string, start: number): number {
  let depth = 0;
  let quote: string | null = null;
  for (let i = start + "<button".length; i < src.length; i++) {
    const c = src[i];
    if (quote) {
      if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") quote = c;
    else if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (c === ">" && depth === 0) return i;
  }
  return -1;
}

/** A raw <button> painted as the primary or the danger action. */
function handRolledActions(): string[] {
  const found: string[] = [];
  for (const file of FILES) {
    if (file.endsWith(join("components", "ui", "Button.tsx"))) continue;
    const src = stripComments(readFileSync(file, "utf8"));
    const re = /<button(\s|>)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(src))) {
      const gt = endOfOpenTag(src, m.index);
      if (gt === -1) break;
      const tag = src.slice(m.index, gt + 1);
      re.lastIndex = gt + 1;
      if (!/\b(bg-bioaf-(600|700)|bg-red-600)\b/.test(tag)) continue;
      if (!/\btext-white\b/.test(tag)) continue;
      found.push(`${relative(SRC, file)}:${src.slice(0, m.index).split("\n").length}`);
    }
  }
  return found;
}

test("the sweep is looking at the whole tree", () => {
  expect(FILES.length).toBeGreaterThan(200);
});

/**
 * 215 before the sweep, 85 after 130 were converted in two passes. Lower this
 * number when more are converted; never raise it. A new primary or danger
 * action uses `Button`.
 *
 * The 85 left are there on purpose, in 38 spellings, 20 of them building their
 * class list in a template literal. Each one either sets a padding that is not
 * one of Button's two sizes (converting would silently resize the hit target),
 * a font size Button does not set, a disabled treatment of its own, or spells
 * its class list as a template literal with a condition inside it. Passing any
 * of those through `className` would put two paddings or two font sizes on one
 * element, and Tailwind resolves that by stylesheet order rather than attribute
 * order -- so the call site would not control which one won. That trap cost the
 * Card sweep its first run. They need reading, not sweeping.
 */
const ACTION_CEILING = 85;

test("the hand-rolled primary/danger button count does not grow", () => {
  expect(handRolledActions().length).toBeLessThanOrEqual(ACTION_CEILING);
});

test("the ceiling tracks reality rather than drifting above it", () => {
  // Guard-the-guard. A ceiling far above the real count silently permits the
  // one-offs this exists to stop.
  expect(handRolledActions().length).toBeGreaterThan(ACTION_CEILING - 20);
});

test("Button is actually called, across the tree and not just one corner", () => {
  const callers = FILES.filter((f) =>
    /from "@\/components\/ui\/Button"/.test(readFileSync(f, "utf8")),
  );
  expect(callers.length).toBeGreaterThan(50);
});
