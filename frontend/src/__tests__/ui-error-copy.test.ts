import { readFileSync, readdirSync } from "fs";
import { join, relative } from "path";

/**
 * Errors published in the UI must be readable by someone who is not an
 * engineer. The real error belongs in the logs.
 *
 * Before this guard, twelve pages rendered `Could not load X. ${loadError}`,
 * which puts whatever the backend or the runtime produced on screen: an HTTP
 * status, a FastAPI validation dump, or a TypeError from a null field. Six more
 * passed the raw error into ErrorState's `details` panel.
 */

const SRC = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next") continue;
      out.push(...sourceFiles(path));
    } else if (entry.name.endsWith(".tsx") && !entry.name.includes(".test.")) {
      out.push(path);
    }
  }
  return out;
}

const FILES = sourceFiles(SRC);

function withText(re: RegExp): string[] {
  return FILES.filter((f) => re.test(readFileSync(f, "utf8"))).map((f) => relative(SRC, f));
}

/** Every `<ErrorState ... />` element in a file, as raw text. */
function errorStateElements(src: string): string[] {
  const out: string[] = [];
  const re = /<ErrorState\b/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    const end = src.indexOf("/>", m.index);
    // A self-closing tag is the only form used; bail rather than guess if that
    // ever changes, so this cannot silently stop checking.
    if (end === -1) throw new Error("ErrorState is no longer self-closing; update this guard");
    out.push(src.slice(m.index, end));
  }
  return out;
}

test("no ErrorState headline interpolates a value", () => {
  // `message={`... ${err}`}` is the shape that leaks: it prints whatever the
  // backend or the runtime produced. A literal or a helper call is fine.
  //
  // Scoped to the ErrorState element itself, because a ConfirmDialog on the same
  // page legitimately interpolates ("Dismiss 3 papers?").
  const offenders = FILES.filter((f) =>
    errorStateElements(readFileSync(f, "utf8")).some((el) => /message=\{`[^`]*\$\{/.test(el)),
  ).map((f) => relative(SRC, f));
  expect(offenders).toEqual([]);
});

test("no ErrorState details panel is fed a caught error", () => {
  expect(withText(/details=\{\s*(loadError|error|err|e)\s*\}/)).toEqual([]);
});

test("the shared reporting helpers exist and are used by the pages that report failures", () => {
  const reporters = withText(/logError\(/);
  // Not a fixed list: it only has to be non-trivial, so the helper cannot quietly
  // fall out of use while the raw-interpolation guards above still pass.
  expect(reporters.length).toBeGreaterThan(10);
});
