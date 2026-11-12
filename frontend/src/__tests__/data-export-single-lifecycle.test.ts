import { readdirSync, readFileSync, statSync } from "fs";
import { join, relative } from "path";

/**
 * The experiment and project data exports share one lifecycle (`components/exports`). They were
 * two copies of the same state and handlers, which drifted into the same late-completion and
 * false-success defects twice. A new copy would have to build the export request's fields again,
 * so that is what this watches. The behavior itself is held by ExportLifecycle.test.tsx.
 */

const SRC = join(__dirname, "..");
const OWNER = "components/exports/useDataExport.ts";

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

test("only the shared export lifecycle builds the data export request", () => {
  const naming = sources(SRC)
    .filter((path) => /include_provenance|delivery_method/.test(readFileSync(path, "utf8")))
    .map((path) => relative(SRC, path));
  expect(naming).toEqual([OWNER]);
});
