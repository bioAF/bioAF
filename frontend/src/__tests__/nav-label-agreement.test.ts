import { existsSync, readFileSync } from "fs";
import { join } from "path";
import { navConfig } from "@/lib/navConfig";

// A nav label and the heading of the page it opens are the same promise made
// twice. When they disagree, the user clicks "Upload" and lands on "Data
// Upload", clicks "Glossary" and lands on "Lab Glossary", clicks "Decision
// Records" and lands on "Scientific Decision Records". Eleven of the app's ~30
// destinations disagreed with themselves before this guard existed (owner,
// 2026-08-08: "I want the same experience throughout").
//
// This matters more than a naming quibble here, because Breadcrumb.tsx builds
// its trail FROM navConfig (`match.section.label`, `match.child.label`). So a
// nav label that disagrees with its page puts the disagreement in two places at
// once: the sidebar says one thing, the breadcrumb repeats it, and the heading
// underneath says something else.
//
// The direction of agreement is not always "nav follows page". Two were settled
// the other way by the owner, both because the page title was the weaker name:
//   * `/projects/experiments` was titled "Experiments" and sits UNDER the
//     "Experiments" section, so adopting it put the word twice in one column and
//     twice in the breadcrumb. The page is "Experiment List" now.
//   * `/environments` was titled "Environments" while "Pipeline Environments"
//     lives under Pipelines. Owner: "the 'Environments' moniker has created a lot
//     of confusion." Both are "Workbench Images" now.

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
  // `/projects/experiments` and `/projects/experiment-templates` are one-line
  // re-exports of the real page. Reading the stub would find no heading at all
  // and silently skip two of the routes this guard exists to check.
  const reexport = /^export \{ default \} from "@\/app\/\(app\)([^"]+)\/page";/m.exec(src.trim());
  if (reexport) return resolvePage(reexport[1], depth + 1);
  return file;
}

/** What the page calls itself: its first literal <h1>. */
function pageHeading(file: string): string | null {
  const src = readFileSync(file, "utf8");
  const m = /<h1[^>]*>[\s]*([^<{][^<]*?)[\s]*<\/h1>/.exec(src);
  if (m) return m[1].replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

  // A page whose heading comes from a component, not from its own markup.
  const delegate = /<([A-Z][A-Za-z]*)(?:Content|Panel)\b/.exec(src);
  if (delegate) {
    const comp = [
      join(SRC, "components", "settings", `${delegate[1]}Content.tsx`),
      join(SRC, "components", `${delegate[1]}Content.tsx`),
    ].find(existsSync);
    if (comp) {
      const c = /<h1[^>]*>[\s]*([^<{][^<]*?)[\s]*<\/h1>/.exec(readFileSync(comp, "utf8"));
      if (c) return c[1].replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
    }
  }
  return null;
}

interface Dest {
  section: string;
  label: string;
  route: string;
}

const destinations: Dest[] = navConfig.flatMap((section) => {
  const own: Dest[] = section.path
    ? [{ section: section.label, label: section.label, route: section.path }]
    : [];
  const kids: Dest[] = (section.children ?? []).map((c) => ({
    section: section.label,
    label: c.label,
    route: c.path,
  }));
  return [...own, ...kids];
});

describe("nav labels agree with the pages they open", () => {
  it("finds the whole nav, not a subset", () => {
    // Without this floor, a navConfig refactor that broke the flatMap would make
    // every assertion below pass over an empty list.
    expect(destinations.length).toBeGreaterThan(25);
    expect(destinations.some((d) => d.route === "/environments")).toBe(true);
  });

  it("resolves a re-exported page to the file that actually renders it", () => {
    // Guard the guard: /projects/experiments is a one-line re-export, and a
    // resolver that stopped there would report "no heading" and skip the route.
    const file = resolvePage("/projects/experiments");
    expect(file).not.toBeNull();
    expect(pageHeading(file!)).not.toBeNull();
  });

  it("gives every nav label the same words as its page heading", () => {
    const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    const disagreements: string[] = [];
    const unchecked: string[] = [];

    for (const d of destinations) {
      const file = resolvePage(d.route);
      if (!file) {
        unchecked.push(`${d.label} -> ${d.route} (no page file)`);
        continue;
      }
      const heading = pageHeading(file);
      if (heading === null) {
        unchecked.push(`${d.label} -> ${d.route} (no literal <h1>)`);
        continue;
      }
      if (norm(d.label) !== norm(heading)) {
        disagreements.push(`${d.section} > "${d.label}"  opens a page titled  "${heading}"  (${d.route})`);
      }
    }

    expect(disagreements).toEqual([]);

    // Routes with no literal heading cannot be checked, so the count is pinned:
    // it may shrink, and growing it means a new destination slipped past this
    // guard rather than that the guard got smarter.
    expect(unchecked.length).toBeLessThanOrEqual(2);
  });
});

// The owner standardised this vocabulary on 2026-08-08, in two steps. First the
// two "Environments" destinations were disambiguated; then "Images" was rejected
// too: "'Images' is a technical term." Both are "Templates" now, which is the
// word a non-technical user can carry between them.
//
// A more universal word is also a vaguer one, so the trade is paid for at the
// point of use: each page states, under its own heading, what its templates
// actually are. Without that note "Workbench Templates" says less than
// "Workbench Images" did, not more.
describe("Templates, not Environments", () => {
  const page = (rel: string) => readFileSync(join(SRC, rel), "utf8");
  const WORKBENCH = "app/(app)/environments/page.tsx";
  const PIPELINE = "app/(app)/pipelines/environments/page.tsx";

  it("names both destinations 'Templates'", () => {
    const labels = navConfig.flatMap((s) => s.children ?? []).map((c) => c.label);
    expect(labels).toContain("Workbench Templates");
    expect(labels).toContain("Pipeline Templates");
  });

  it("retires the words that came before", () => {
    // "Environments" was ambiguous across two destinations; "Images" was
    // technical. Neither should survive as a LABEL, in the nav or as a heading.
    const all = [page(WORKBENCH), page(PIPELINE), page("lib/navConfig.ts")].join("\n");
    for (const dead of ["Workbench Images", "Pipeline Environments", "New Workbench Image", "New Pipeline Environment"]) {
      expect(all).not.toContain(dead);
    }
  });

  it("explains what each kind of template is, under its own heading", () => {
    // A note that exists but says nothing would pass a mere presence check, so
    // each is required to name the concrete thing it configures.
    const workbench = page(WORKBENCH);
    expect(workbench).toMatch(/Notebook Sessions/);
    expect(workbench).toMatch(/Work Nodes/);

    const pipeline = page(PIPELINE);
    expect(pipeline).toMatch(/custom pipelines?/i);
    expect(pipeline).toMatch(/Conda/);
  });
});
