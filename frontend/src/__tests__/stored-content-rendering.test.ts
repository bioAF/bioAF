import { readFileSync, readdirSync } from "fs";
import { join, relative } from "path";

/**
 * Pipeline reports, QC summaries and provenance metadata are written by
 * pipelines and users, and used to reach the page as HTML: each one could
 * run script as the signed-in user. They now render as React text, through
 * react-markdown with raw HTML skipped, or inside a sandboxed frame.
 *
 * The consumer tests prove each renderer is safe. This guard keeps new HTML
 * sinks from appearing unreviewed: the only `dangerouslySetInnerHTML` is the
 * static theme bootstrap, and every `srcDoc` frame withholds the app's origin.
 */

const SRC = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next" || entry.name === "__tests__") continue;
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

const FILES = sourceFiles(SRC).map((f) => ({ path: relative(SRC, f), text: readFileSync(f, "utf8") }));

/** Reviewed exceptions: constant content that never includes stored data. */
const INNER_HTML_EXCEPTIONS = new Set(["app/layout.tsx"]);

test("no stored content is inserted as HTML", () => {
  const offenders = FILES.filter(
    (f) => /dangerouslySetInnerHTML/.test(f.text) && !INNER_HTML_EXCEPTIONS.has(f.path),
  ).map((f) => f.path);
  expect(offenders).toEqual([]);
});

test("the theme bootstrap is the only inner-HTML script and it is a constant", () => {
  const layout = FILES.find((f) => f.path === "app/layout.tsx")!.text;
  expect(layout.match(/dangerouslySetInnerHTML=\{\{ __html: ([^}]+) \}\}/g)).toEqual([
    "dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }}",
  ]);
});

test("every srcDoc frame is sandboxed without the app's origin", () => {
  const frames = FILES.flatMap((f) =>
    Array.from(f.text.matchAll(/<iframe\b[^>]*>/gs)).map((m) => ({ path: f.path, tag: m[0] })),
  ).filter((frame) => /srcDoc=/.test(frame.tag));
  expect(frames.length).toBeGreaterThan(0);
  for (const frame of frames) {
    const sandbox = frame.tag.match(/sandbox="([^"]*)"/);
    expect({ path: frame.path, sandbox: sandbox?.[1] ?? null }).toEqual({
      path: frame.path,
      sandbox: expect.not.stringMatching(/allow-same-origin|allow-top-navigation/),
    });
    expect(sandbox).not.toBeNull();
  }
});
