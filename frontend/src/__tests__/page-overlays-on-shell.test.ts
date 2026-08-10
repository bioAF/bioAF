import { readFileSync, readdirSync } from "fs";
import { join, relative } from "path";

/**
 * Dialogs written inline inside a page file.
 *
 * Eighteen page files hand-rolled 38 full-screen overlays between them. Of
 * those, almost none carried `role="dialog"`, none trapped focus, and most had
 * no Escape: identical to a sighted mouse user, and a different app to everyone
 * else. They are on the shared `Modal` shell now, which brings all four.
 *
 * `components/` is covered now too. It was the "separate, unstarted piece of
 * work" this file used to defer: 19 overlays across 12 component files, with
 * **0 focus traps and 0 scroll locks between them**, and no `role="dialog"` on
 * 17 of the 19.
 *
 * Measured on the deployed demo, DatasetBrowser's "Add to Project" against the
 * reference page's shared-Modal dialog, same script:
 *
 *   shared Modal : role=dialog, aria-modal, named "Deprecate Reference
 *                  Dataset", body overflow hidden, Escape closes,
 *                  0 of 12 Tab stops outside the panel
 *   hand-rolled  : no role, no accessible name, no scroll lock, Escape dead,
 *                  **12 of 12 Tab stops outside the panel**
 *
 * 12 of 12 is the finding. A keyboard user who opened that dialog could not
 * reach a single control inside it; their focus was in the page behind, which
 * they could not see.
 */

const APP = join(__dirname, "..", "app");
const COMPONENTS = join(__dirname, "..", "components");

/**
 * Files that legitimately hold a `fixed inset-0` and are NOT dialogs.
 * Each needs a reason, not just an entry.
 */
const ALLOWED = new Map<string, string>([
  // The shell itself, and the three sibling dialog primitives. All four already
  // carry role="dialog" and a focus trap; they are the thing being adopted, not
  // a one-off avoiding it.
  ["shared/Modal.tsx", "is the shared shell"],
  ["shared/ConfirmDialog.tsx", "dialog primitive: role + focus trap of its own"],
  ["shared/DetailModal.tsx", "dialog primitive: role + focus trap of its own"],
  ["shared/InputDialog.tsx", "dialog primitive: role + focus trap of its own"],
  // Not dialogs.
  ["layout/BootSplash.tsx", "full-screen cover; asks nothing and has nothing behind it"],
  ["layout/Sidebar.tsx", "the mobile drawer's backdrop, not a dialog"],
  ["provenance/ProvenanceReportPanel.tsx", "invisible click-catcher closing a dropdown"],
  ["shared/ProvenanceExportMenu.tsx", "invisible click-catcher closing a dropdown"],
  // Excluded by Modal's own documentation: these report the state of something
  // already running rather than asking for a decision, so a focus trap would
  // take the keyboard hostage for a dialog the user cannot answer.
  ["infrastructure/TerraformProgressModal.tsx", "progress, not a decision (see Modal.tsx)"],
  ["infrastructure/DeployProgressModal.tsx", "progress, not a decision (see Modal.tsx)"],
]);

function pageFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...pageFiles(path));
    else if (entry.name.endsWith(".tsx") && !entry.name.includes(".test.")) out.push(path);
  }
  return out;
}

const FILES = pageFiles(APP);

test("the sweep is looking at the app directory", () => {
  expect(FILES.length).toBeGreaterThan(50);
});

test("no page hand-rolls a dialog overlay any more", () => {
  const offenders: string[] = [];
  for (const file of FILES) {
    const src = readFileSync(file, "utf8");
    if (!/fixed inset-0/.test(src)) continue;
    // The (app) layout's boot splash is a full-screen cover, not a dialog: it
    // asks nothing, and there is nothing behind it to trap focus away from.
    if (/data-testid="app-loading"/.test(src)) continue;
    offenders.push(relative(APP, file));
  }
  expect(offenders).toEqual([]);
});

const COMPONENT_FILES = pageFiles(COMPONENTS);

test("the sweep is looking at the components directory", () => {
  expect(COMPONENT_FILES.length).toBeGreaterThan(100);
});

test("no component hand-rolls a dialog overlay any more", () => {
  const offenders: string[] = [];
  for (const file of COMPONENT_FILES) {
    const rel = relative(COMPONENTS, file);
    if (ALLOWED.has(rel)) continue;
    if (!/fixed inset-0/.test(readFileSync(file, "utf8"))) continue;
    offenders.push(rel);
  }
  expect(offenders).toEqual([]);
});

test("every allowance still points at a file that exists", () => {
  // An allowance for a deleted file is how an exemption list quietly grows
  // permission it no longer needs.
  const present = new Set(COMPONENT_FILES.map((f) => relative(COMPONENTS, f)));
  expect([...ALLOWED.keys()].filter((k) => !present.has(k))).toEqual([]);
});
