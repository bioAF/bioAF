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
// Dark mode is unaffected: globals.css already maps `.dark .text-gray-500` and
// `.dark .text-gray-400` to the same token.
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
  "app/(app)/layout.tsx", // the bg-gray-900 boot splash
];

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
