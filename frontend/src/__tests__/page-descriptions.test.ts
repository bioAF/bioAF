import { existsSync, readFileSync } from "fs";
import { join } from "path";
import { navConfig } from "@/lib/navConfig";

// Every destination says, in one line under its heading, what it is for.
//
// The reason is the empty instance. On a brand-new installation almost every
// page in this app renders a heading and nothing else, because nothing has been
// created yet. "Plot Archive" over an empty table tells a new user neither what
// a plot archive is nor how one would come to exist. Owner, 2026-08-08: "add a
// small 1-sentence description under the header for each page telling the user
// what it does ... so users can quickly see what each page does even if there is
// nothing to display yet on a brand new installation."
//
// The marker is a `data-testid`, not a class or a position, because the first
// two are what an earlier survey tried and both were wrong: `<p class="text-sm
// text-gray-500">Loading infrastructure status...</p>` and `<p class="text-sm
// font-medium text-amber-800">Cost data is estimated</p>` both sit close under a
// heading and neither is a description. An explicit marker cannot be
// accidentally satisfied, and it is also what the live browser check reads.

const SRC = join(__dirname, "..");

/** Follow `export { default } from "@/..."` re-exports to the file that renders. */
function resolvePage(route: string, depth = 0): string | null {
  if (depth > 3) return null;
  const file = [
    join(SRC, "app", "(app)", route, "page.tsx"),
    join(SRC, "app", route, "page.tsx"),
  ].find(existsSync);
  if (!file) return null;
  const src = readFileSync(file, "utf8");
  const reexport = /^export \{ default \} from "@\/app\/\(app\)([^"]+)\/page";/m.exec(src.trim());
  return reexport ? resolvePage(reexport[1], depth + 1) : file;
}

const DESCRIPTION = /<p[^>]*data-testid="page-description"[^>]*>\s*([\s\S]*?)\s*<\/p>/;

/** The description's visible words, with JSX entities and tags flattened. */
function describedAs(file: string): string | null {
  const m = DESCRIPTION.exec(readFileSync(file, "utf8"));
  if (!m) return null;
  return m[1]
    .replace(/<[^>]+>/g, "")
    .replace(/\{"\s*"\}/g, " ")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

interface Dest {
  label: string;
  route: string;
}

const destinations: Dest[] = navConfig.flatMap((section) => [
  ...(section.path ? [{ label: section.label, route: section.path }] : []),
  ...(section.children ?? []).map((c) => ({ label: c.label, route: c.path })),
]);

// Destinations with no literal <h1> of their own, so there is nothing to sit a
// description under. Pinned rather than skipped silently: this list may shrink,
// and if it grows a page has quietly lost its heading.
const NO_HEADING = ["/dashboard", "/settings/networking"];

describe("every destination says what it is for", () => {
  it("finds the whole nav", () => {
    expect(destinations.length).toBeGreaterThan(25);
  });

  it("carries a description under the heading", () => {
    const missing: string[] = [];
    for (const d of destinations) {
      if (NO_HEADING.includes(d.route)) continue;
      const file = resolvePage(d.route);
      if (!file) continue;
      if (!describedAs(file)) missing.push(`${d.label} (${d.route})`);
    }
    expect(missing).toEqual([]);
  });

  it("writes a real sentence, not a label repeated", () => {
    const bad: string[] = [];
    for (const d of destinations) {
      if (NO_HEADING.includes(d.route)) continue;
      const file = resolvePage(d.route);
      if (!file) continue;
      const text = describedAs(file);
      if (!text) continue;

      // Long enough to say something, short enough to stay one line of prose.
      if (text.length < 30) bad.push(`${d.label}: too short (${text.length}) "${text}"`);
      if (text.length > 220) bad.push(`${d.label}: too long (${text.length})`);
      // "Projects" under a heading that already says Projects is not a description.
      if (text.toLowerCase().replace(/[^a-z ]/g, "").trim() === d.label.toLowerCase()) {
        bad.push(`${d.label}: repeats the heading`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("keeps the disclosure guides operable by keyboard and screen reader", () => {
    // The collapsible "How X works" guides are the pattern Naming Profiles was
    // asked to match. A disclosure that never reports its state leaves a screen
    // reader user unable to tell whether the panel is open, so the attribute is
    // required on all of them rather than only the newest.
    const guides = [
      "app/(app)/notebooks/page.tsx",
      "app/(app)/workbench/work-nodes/page.tsx",
      "app/(app)/settings/naming-profiles/page.tsx",
    ];
    const missing: string[] = [];
    for (const rel of guides) {
      const src = readFileSync(join(SRC, rel), "utf8");
      if (!/setShowGuide\(/.test(src)) missing.push(`${rel}: no disclosure`);
      if (!/aria-expanded=\{showGuide\}/.test(src)) missing.push(`${rel}: no aria-expanded`);
    }
    expect(missing).toEqual([]);
  });
});
