import { readFileSync, readdirSync } from "fs";
import { join } from "path";

// `text-gray-400` is #9ca3af, which is 2.54:1 on white. AA wants 4.5:1 for
// normal text and 3:1 even for large text, so it failed both, and it was used
// 361 times for real content: paragraphs, definition terms, table empty cells,
// and empty-state headings.
//
// `text-gray-500` is #6b7280 = 4.83:1 on white and 4.66:1 on gray-50, so it
// clears AA on the backgrounds this app actually uses while staying visibly
// lighter than body text, which is what the shade was chosen for.
//
// Dark mode is unaffected: `text-gray-500` and `text-gray-400` resolve to the
// same dark token (`--fg-gray-500` / `--fg-gray-400`, both --color-ink-subtle).
// See tailwind.tokens.js and dark-theme-tokens.test.ts.
//
// THE SHADE DEPENDS ON THE BACKGROUND, and the first version of this sweep got
// that wrong. The sidebar is permanently dark (`bg-gray-900`) regardless of
// theme, and there the relationship inverts: gray-400 is ~7:1 and gray-500 only
// 3.67:1, so "fixing" those sites made them fail. A browser audit of the
// rendered tree caught it; no amount of source grepping could have, because the
// background comes from an ancestor. Hence the allowlist below rather than a
// blanket ban.

const SRC = join(__dirname, "..");

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next") continue;
      out.push(...tsxFiles(path));
    } else if (entry.name.endsWith(".tsx") && !entry.name.includes(".test.")) {
      out.push(path);
    }
  }
  return out;
}

// Surfaces that are dark whatever the theme, where gray-400 is the CORRECT
// choice (~7:1) and gray-500 fails (3.67:1). Found by grepping for a dark
// background token used as a text container, rather than as a modal backdrop
// (`bg-black/40` sits behind a white panel, so text on it is unaffected) or as
// a `hover:` state.
const DARK_SURFACES = [
  "components/layout/Sidebar.tsx",
  "components/layout/NavItem.tsx",
  // The bg-gray-900 boot splash. It used to be inline in `app/(app)/layout.tsx`,
  // which is what this list named until the splash grew a failure state and moved
  // into its own component; the layout has no grey text left and no longer needs
  // an exemption.
  "components/layout/BootSplash.tsx",
];

// Pairings measured on a rendered page (composited, so alpha layers are real
// rather than assumed), each below the 4.5:1 AA wants for normal text. None is
// large text: every one is 10px or 12px.
//
// The replacement is the next step down the same ramp in each case, so the hue
// and the intent of the shade are unchanged.
const FAILING_PAIRS: { bad: RegExp; measured: string; use: string }[] = [
  // white on red-500 = 3.76:1, on the notification badge, on 52 of 53 pages,
  // and the only one of these that survives into dark mode.
  { bad: /\btext-white\b[^"'`]*\bbg-red-500\b|\bbg-red-500\b[^"'`]*\btext-white\b/, measured: "3.76:1", use: "bg-red-600 (4.83:1)" },
  // amber-600 on white = 3.05:1, measured, not the 3.19:1 first reported.
  { bad: /\btext-amber-600\b/, measured: "3.05:1", use: "text-amber-700 (5.02:1)" },
  // gray-500 on gray-100 = 4.39:1. On white the same shade is 4.83:1 and fine,
  // so this only fails where the two are paired.
  { bad: /\bbg-gray-100\b[^"'`]*\btext-gray-500\b|\btext-gray-500\b[^"'`]*\bbg-gray-100\b/, measured: "4.39:1", use: "text-gray-600 (6.87:1)" },
  // gray-300 on white = 1.47:1, effectively invisible.
  { bad: /\btext-gray-300\b/, measured: "1.47:1", use: "text-gray-500 (4.83:1)" },
  // white on bg-black/50 over a light thumbnail = 4.29:1. The backdrop varies
  // with the image, so the shade has to hold against a white one.
  { bad: /\bbg-black\/50\b[^"'`]*\btext-white\b|\btext-white\b[^"'`]*\bbg-black\/50\b/, measured: "4.29:1", use: "bg-black/70 (8.45:1 even over white)" },
];

test("the measured failing pairings are gone", () => {
  const offenders: string[] = [];
  for (const file of tsxFiles(SRC)) {
    const rel = file.replace(SRC, "src");
    // The sidebar is permanently dark, where gray-300 is the correct light text.
    if (DARK_SURFACES.some((d) => rel.endsWith(d))) continue;
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => {
        for (const { bad, measured, use } of FAILING_PAIRS) {
          if (bad.test(line)) offenders.push(`${rel}:${i + 1} (${measured}, use ${use})`);
        }
      });
  }
  expect(offenders).toEqual([]);
});

// An em-dash has THREE spellings in this codebase and the first pass only
// caught one. The literal character is greppable; `&#8212;` / `&mdash;` are not;
// and `"\u2014"` evades both, which is how two of them survived on
// /settings/users until the page was read in a browser.
//
// The remaining literal placeholders elsewhere are a separate, larger sweep that
// has not been signed off, so this guard covers the files already cleaned.
const EM_DASH_CLEANED = [
  "app/(app)/settings/users/page.tsx",
  "components/SnapshotComparison.tsx",
];

test("no user-facing em-dash in any of its three spellings, in the cleaned files", () => {
  const offenders: string[] = [];
  for (const file of tsxFiles(SRC)) {
    const rel = file.replace(SRC + "/", "");
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => {
        const entity = /&mdash;|&#8212;|&#x2014;/i.test(line);
        const escaped = /\\u2014/.test(line);
        const literal = line.includes("\u2014");
        if (entity || (EM_DASH_CLEANED.includes(rel) && (escaped || literal))) {
          offenders.push(`${rel}:${i + 1}`);
        }
      });
  }
  expect(offenders).toEqual([]);
});

test("the paired state of a status cell is as readable as the cell it pairs with", () => {
  // Fixing "not configured" to 4.83:1 while its ✓ sibling sat at 3.30:1 would
  // have been half a column. green-600 on white is 3.30:1; green-700 is 5.02:1.
  const users = readFileSync(join(SRC, "app", "(app)", "settings", "users", "page.tsx"), "utf8");
  expect(users).not.toMatch(/text-green-600/);
});

test("no text uses a shade that fails AA on its own background", () => {
  const offenders: string[] = [];
  for (const file of tsxFiles(SRC)) {
    const rel = file.replace(SRC, "src");
    const onDark = DARK_SURFACES.some((d) => rel.endsWith(d));
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => {
        // `disabled:text-gray-400` is allowed everywhere. WCAG 1.4.3 exempts
        // inactive controls, and a disabled field that looks as solid as an
        // active one is its own usability problem.
        const bare = line.replace(/disabled:text-gray-400/g, "");
        const bad = onDark
          ? /\btext-gray-(500|600)\b/.test(bare) // too dark for a dark surface
          : bare.includes("text-gray-400"); // too light for a light surface
        if (bad) offenders.push(`${rel}:${i + 1}`);
      });
  }
  expect(offenders).toEqual([]);
});

/**
 * A dash is not a word. 69 places rendered an em-dash where a value was
 * missing, which cannot be told apart from "failed to load", from a rendering
 * bug, or from a value that is itself a dash. The owner's ruling was a text
 * placeholder in caps; NOT_SET in src/lib/placeholders.ts is where it lives.
 */
test("nothing renders an em-dash as a missing-value placeholder", () => {
  const walk = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) return entry.name === "node_modules" ? [] : walk(path);
      return (path.endsWith(".tsx") || path.endsWith(".ts")) && !path.includes(".test.")
        ? [path]
        : [];
    });

  const offenders = walk(SRC).filter((file) => {
    const src = readFileSync(file, "utf8");
    // The literal, the HTML entities, and the escaped form that evaded both.
    return /"\u2014"|&mdash;|&#8212;|&#x2014;|"\\u2014"/.test(src);
  });

  expect(offenders.map((f) => f.replace(SRC, "src"))).toEqual([]);
});
