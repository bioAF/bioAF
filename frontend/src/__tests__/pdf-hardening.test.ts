import { readFileSync, readdirSync } from "fs";
import { join, relative } from "path";
import { HARDENED_PDF_OPTIONS } from "@/lib/pdfSecurity";

/**
 * pdf.js executes JavaScript embedded in a PDF unless it is told not to
 * (`enableScripting` defaults to true). Every PDF this app opens is untrusted:
 * papers come from external publishers, lab documents are user uploads. On the
 * default settings a malicious file gets arbitrary script execution in our
 * origin, against a logged-in session (GHSA-hq66-cqwq-w95j).
 *
 * The per-viewer tests assert each existing viewer passes the hardened options.
 * This one is the guard for the viewer nobody has written yet: it fails if a
 * new `getDocument` call site forgets them, which is silent at runtime and
 * indistinguishable from a working viewer until someone opens a hostile PDF.
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

/** Files that open a PDF document through pdf.js. */
const CALL_SITES = FILES.filter((f) => /getDocument\s*\(/.test(readFileSync(f, "utf8")));

test("the hardened options disable PDF scripting and eval", () => {
  expect(HARDENED_PDF_OPTIONS.enableScripting).toBe(false);
  expect(HARDENED_PDF_OPTIONS.isEvalSupported).toBe(false);
});

test("every pdf.js call site opens documents with the hardened options", () => {
  expect(CALL_SITES.length).toBeGreaterThan(0);

  const offenders = CALL_SITES.filter((file) => {
    const src = readFileSync(file, "utf8");
    return !/getDocument\s*\(\s*\{[^}]*\.\.\.HARDENED_PDF_OPTIONS/.test(src);
  }).map((f) => relative(SRC, f));

  expect(offenders).toEqual([]);
});
