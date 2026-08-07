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
 * Scoped to `app/`, because that is what was converted. The remaining
 * component-level overlays (FileBrowser, LabGlossaryBrowser and friends) are a
 * separate, unstarted piece of work, and a guard that fails on unstarted work
 * is one people delete.
 */

const APP = join(__dirname, "..", "app");

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
