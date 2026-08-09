import { readFileSync } from "fs";
import { execSync } from "child_process";
import { join } from "path";
import * as ts from "typescript";

// A table wider than its container must stay REACHABLE.
//
// The defect this holds shut: 26 wrappers were written as
// `<div className="bg-white rounded-lg shadow overflow-hidden"><table className="min-w-full">`.
// The `overflow-hidden` is there for the rounded corners, but `min-w-full` lets the
// table exceed the wrapper, and `hidden` offers the user no way to reach the excess.
// Measured on the deployed app at 375px: /pipelines/runs was scrollWidth 1129 in a
// clientWidth 327 box, so 8 of 10 columns -- including Status -- could not be brought
// on screen by any gesture. Ten routes were affected at 375px, nine at 768px.
//
// TWO MEASUREMENT TRAPS, both of which produced a confident wrong answer first.
//
// 1. Do not measure DOCUMENT horizontal scroll. An earlier round did, concluded "0 of
//    18 routes overflow, 0 tables clipped", and marked the item done. `overflow-hidden`
//    is precisely what suppresses document scroll, so that method proved the inverse of
//    its conclusion: the tables were clipped BECAUSE nothing overflowed.
// 2. Do not probe with `el.scrollLeft = 99999`. Per the CSS overflow spec `hidden`
//    behaves exactly like `scroll` except that no scrolling mechanism is offered to the
//    USER -- the box is still a scroll container and the assignment succeeds. The first
//    run of verification/v5-responsive.js used that probe and reported every clipped
//    table as "scrollable". The overflow-x VALUE is the discriminator, which is why this
//    guard reads classes rather than trying to simulate a gesture.
//
// So the invariant is about the nearest ancestor that DECLARES an overflow, because
// that is the box the browser makes a scroll container: it may not clip on x.
//
// Deliberately NOT asserted: 19 tables have no overflow-declaring ancestor inside their
// own file. Those are not clipped -- the nearest scroll container is a page-level
// `overflow-x-auto`, which the user can reach -- and requiring a per-table wrapper for
// them would be a rule this evidence does not support.
//
// This walks the TSX AST rather than matching text, because the question is genuinely
// "which element ENCLOSES this table", and the previous line-adjacency approximation
// misses `<div className="...overflow-hidden"> <h3>...</h3> <table>` (two real sites in
// data/references/[id]).

const ROOT = join(__dirname, "..", "..");

const CLIPS = /\boverflow(-x)?-(hidden|clip)\b/;
const SCROLLS = /\boverflow(-x)?-(auto|scroll)\b/;

function classNameText(open: ts.JsxOpeningLikeElement): string {
  for (const attr of open.attributes.properties) {
    if (!ts.isJsxAttribute(attr)) continue;
    if (attr.name.getText() !== "className") continue;
    return attr.initializer ? attr.initializer.getText() : "";
  }
  return "";
}

interface TableSite {
  file: string;
  line: number;
  /** The nearest ancestor that declares an overflow, if any. */
  nearestOverflow: { tag: string; className: string; clips: boolean } | null;
}

function tableSites(): TableSite[] {
  const files = execSync("grep -rl '<table' src --include='*.tsx'", {
    cwd: ROOT,
    encoding: "utf8",
  })
    .trim()
    .split("\n")
    .filter(Boolean);

  const sites: TableSite[] = [];

  for (const rel of files) {
    const source = readFileSync(join(ROOT, rel), "utf8");
    const sf = ts.createSourceFile(rel, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

    const visit = (node: ts.Node): void => {
      const isTable =
        (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
        node.tagName.getText() === "table";

      if (isTable) {
        let nearest: TableSite["nearestOverflow"] = null;
        let parent: ts.Node | undefined = node.parent;
        while (parent && !nearest) {
          let open: ts.JsxOpeningLikeElement | null = null;
          if (ts.isJsxElement(parent)) open = parent.openingElement;
          else if (ts.isJsxSelfClosingElement(parent)) open = parent;

          if (open && open !== node) {
            const cls = classNameText(open);
            if (CLIPS.test(cls) || SCROLLS.test(cls)) {
              nearest = { tag: open.tagName.getText(), className: cls, clips: CLIPS.test(cls) };
            }
          }
          parent = parent.parent;
        }

        sites.push({
          file: rel,
          line: sf.getLineAndCharacterOfPosition(node.getStart()).line + 1,
          nearestOverflow: nearest,
        });
      }

      ts.forEachChild(node, visit);
    };

    visit(sf);
  }

  return sites;
}

const sites = tableSites();

describe("a table's overflow must be reachable", () => {
  it("finds the tables (guard against a scanner that silently matches nothing)", () => {
    expect(sites.length).toBeGreaterThan(50);
  });

  it("no table sits in a scroll container that clips on x", () => {
    const clipped = sites
      .filter((s) => s.nearestOverflow?.clips)
      .map((s) => `${s.file}:${s.line} -> ${s.nearestOverflow!.className}`);

    expect(clipped).toEqual([]);
  });

  it("the wrappers that were clipped now offer horizontal scroll", () => {
    // The ten routes proven unreachable in a browser at 375px, pinned by source so a
    // future edit cannot quietly put `overflow-hidden` back on one of them.
    const wasClipped = [
      "src/app/(app)/pipelines/runs/page.tsx",
      "src/app/(app)/experiments/page.tsx",
      "src/app/(app)/projects/page.tsx",
      "src/app/(app)/settings/users/page.tsx",
      "src/app/(app)/data/references/page.tsx",
      "src/app/(app)/pipelines/custom/page.tsx",
      "src/app/(app)/pipelines/environments/page.tsx",
      "src/app/(app)/settings/roles/page.tsx",
      "src/app/(app)/settings/naming-profiles/page.tsx",
      "src/components/data/DatasetBrowser.tsx",
    ];

    for (const file of wasClipped) {
      const inFile = sites.filter((s) => s.file === file);
      expect(inFile.length).toBeGreaterThan(0);
      const scrollable = inFile.filter((s) => s.nearestOverflow && !s.nearestOverflow.clips);
      expect(`${file}: ${scrollable.length} scrollable`).toBe(`${file}: ${inFile.length} scrollable`);
    }
  });
});
