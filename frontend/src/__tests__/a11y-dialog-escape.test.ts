import { readFileSync, readdirSync } from "fs";
import { join } from "path";

// A `fixed inset-0` layer with an onClick is a click-outside-to-dismiss
// backdrop. It gives a mouse user a way out of the dialog that a keyboard user
// does not have, and that asymmetry is the defect: the keyboard equivalent is
// Escape, not a focusable invisible sheet over the viewport (which is why these
// were excluded from the clickable-elements sweep rather than made focusable).
//
// The overlay marks the dialog; the handler belongs to the component that owns
// it, so this checks the whole file.

const SRC = join(__dirname, "..");

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next") continue;
      out.push(...tsxFiles(path));
    } else if (entry.name.endsWith(".tsx") && !entry.name.includes(".test.")) {
      out.push(path);
    }
  }
  return out;
}

function tagBodyEnd(src: string, i: number): number {
  let depth = 0;
  let quote: string | null = null;
  for (; i < src.length; i++) {
    const c = src[i];
    if (quote) {
      if (c === "\\") i++;
      else if (c === quote) quote = null;
    } else if (c === '"' || c === "'" || c === "`") quote = c;
    else if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (depth === 0 && c === ">") return i;
  }
  return -1;
}

function onClickExpr(body: string): string {
  const m = /onClick=\{/.exec(body);
  if (!m) return "";
  let i = m.index + m[0].length;
  const start = i;
  let depth = 1;
  while (i < body.length && depth > 0) {
    if (body[i] === "{") depth++;
    else if (body[i] === "}") depth--;
    i++;
  }
  return body.slice(start, i - 1).replace(/\s+/g, " ").trim();
}

// Either the shared hook, or a hand-rolled listener that names the key.
const HANDLES_ESCAPE = /useDismissOnEscape|(["'])Escape\1|keyCode\s*===\s*27/;

function scan() {
  const offenders: string[] = [];
  let overlays = 0;
  for (const file of tsxFiles(SRC)) {
    const src = readFileSync(file, "utf8");
    const handled = HANDLES_ESCAPE.test(src);
    const tag = /<div(?=[\s/>])/g;
    let m: RegExpExecArray | null;
    while ((m = tag.exec(src))) {
      const end = tagBodyEnd(src, m.index + 4);
      if (end < 0) continue;
      const body = src.slice(m.index, end + 1);
      if (!body.includes("onClick")) continue;
      if (!body.includes("inset-0") || !body.includes("fixed")) continue;
      // The inner panel's stopPropagation guard is not a dismiss control.
      if (/^\(?e\)?\s*=>\s*e\.stopPropagation\(\)$/.test(onClickExpr(body))) continue;
      overlays++;
      if (handled) continue;
      offenders.push(`${file.replace(SRC, "src")}:${src.slice(0, m.index).split("\n").length}`);
    }
  }
  return { offenders, overlays };
}

test("the scan still finds dismiss overlays", () => {
  // Guard-the-guard: with a broken matcher every dialog looks compliant.
  expect(scan().overlays).toBeGreaterThanOrEqual(20);
});

test("every dialog that closes on backdrop click also closes on Escape", () => {
  expect(scan().offenders).toEqual([]);
});
