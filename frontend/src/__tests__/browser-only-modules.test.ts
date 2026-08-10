import { readFileSync, readdirSync } from "fs";
import { join, relative } from "path";

/**
 * `pdfjs-dist` runs its own module-level setup the moment it is imported, and
 * that setup reaches for browser globals (`DOMMatrix`). A `"use client"`
 * component is still rendered on the server for the first response, so a plain
 * import of a PDF viewer drags pdf.js into Node: the deployed frontend logged
 * `DOMMatrix is not defined` twice on every start, and a dev server prints
 * "Please use the `legacy` build in Node.js environments" on each render of
 * those routes.
 *
 * The fix is not to make pdf.js server-safe, it is to never load it there:
 * `next/dynamic(..., { ssr: false })`. This guard keeps it that way, because
 * the failing import is silent in the browser and only shows up in a log
 * nobody reads.
 */

const SRC = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next") continue;
      out.push(...sourceFiles(path));
    } else if (
      (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) &&
      !entry.name.includes(".test.")
    ) {
      out.push(path);
    }
  }
  return out;
}

const FILES = sourceFiles(SRC);

/** Files that pull pdf.js into their module graph directly. */
const BROWSER_ONLY = FILES.filter((f) => /from "pdfjs-dist"/.test(readFileSync(f, "utf8")));

/** `PaperPdfViewer` -> the file that exports it. */
function exportedNames(file: string): string[] {
  const src = readFileSync(file, "utf8");
  return [...src.matchAll(/export function (\w+)/g)].map((m) => m[1]);
}

test("the browser-only modules are the two PDF viewers, and they are found", () => {
  expect(BROWSER_ONLY.map((f) => relative(SRC, f)).sort()).toEqual([
    "components/lab-knowledge/LabDocumentViewer.tsx",
    "components/literature/PaperPdfViewer.tsx",
  ]);
});

test("nothing imports a PDF viewer in a way that would render it on the server", () => {
  const offenders: string[] = [];

  for (const viewer of BROWSER_ONLY) {
    for (const name of exportedNames(viewer)) {
      for (const file of FILES) {
        if (file === viewer) continue;
        const src = readFileSync(file, "utf8");
        // A static import is what runs on the server. `next/dynamic` with
        // ssr:false references the module inside a callback instead.
        const staticImport = new RegExp(`import\\s*\\{[^}]*\\b${name}\\b[^}]*\\}\\s*from`);
        if (staticImport.test(src)) {
          offenders.push(`${relative(SRC, file)} statically imports ${name}`);
        }
      }
    }
  }

  expect(offenders).toEqual([]);
});

test("every place that renders a PDF viewer loads it with ssr disabled", () => {
  const names = BROWSER_ONLY.flatMap(exportedNames);
  const missing: string[] = [];

  for (const file of FILES) {
    if (BROWSER_ONLY.includes(file)) continue;
    const src = readFileSync(file, "utf8");
    for (const name of names) {
      if (!new RegExp(`<${name}\\b`).test(src)) continue;
      const loader = new RegExp(
        `const ${name}\\s*=\\s*dynamic\\([\\s\\S]*?ssr:\\s*false`,
      );
      if (!loader.test(src)) {
        missing.push(`${relative(SRC, file)} renders ${name} without a dynamic ssr:false loader`);
      }
    }
  }

  expect(missing).toEqual([]);
});
