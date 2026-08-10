import { readFileSync, readdirSync } from "fs";
import { join } from "path";

// Every form control needs a programmatic name. A placeholder is not one: it
// disappears the moment the user types, and assistive tech is not required to
// announce it.
//
// A control counts as named if ANY of the four things a browser looks at is
// present:
//   1. aria-label
//   2. aria-labelledby
//   3. an id that some <label htmlFor=...> in the same file points at
//   4. an enclosing <label> (implicit association)
//
// Note the ORDER matters in the app even though it does not here: aria-label
// OVERRIDES an enclosing label. Adding one to a control that already had a
// wrapping label replaces a real name with whatever you passed, which is how
// "Hostname" briefly became "app". Prefer 3 and 4; reach for 1 only when there
// is no visible label to point at.

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

function tagEnd(src: string, i: number): number {
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

/** Walk every tag, tracking how deep we are inside <label> elements. */
function scan(src: string) {
  const controls: { body: string; line: number; inLabel: boolean }[] = [];
  const htmlForTargets = new Set<string>();
  const tag = /<(\/?)([a-zA-Z][a-zA-Z0-9.]*)(?=[\s/>])/g;
  let m: RegExpExecArray | null;
  let labelDepth = 0;

  while ((m = tag.exec(src))) {
    const closing = m[1] === "/";
    const name = m[2];
    if (closing) {
      if (name === "label") labelDepth = Math.max(0, labelDepth - 1);
      continue;
    }
    const end = tagEnd(src, m.index + m[0].length);
    if (end < 0) continue;
    const body = src.slice(m.index, end + 1);
    const selfClosing = body.trimEnd().endsWith("/>");

    if (name === "label") {
      const f = /htmlFor=(?:["']([^"']+)["']|\{`?([^}`]+)`?\})/.exec(body);
      if (f) htmlForTargets.add((f[1] || f[2]).trim());
      if (!selfClosing) labelDepth++;
      continue;
    }
    if (name === "input" || name === "select" || name === "textarea") {
      controls.push({ body, line: src.slice(0, m.index).split("\n").length, inLabel: labelDepth > 0 });
    }
    tag.lastIndex = end + 1;
  }
  return { controls, htmlForTargets };
}

function unnamed() {
  const offenders: string[] = [];
  let total = 0;
  for (const file of tsxFiles(SRC)) {
    const src = readFileSync(file, "utf8");
    const { controls, htmlForTargets } = scan(src);
    for (const { body, line, inLabel } of controls) {
      if (/type=["']hidden["']/.test(body)) continue;
      total++;
      if (/aria-label|aria-labelledby/.test(body)) continue;
      if (inLabel) continue;
      const idm = /\bid=(?:["']([^"']+)["']|\{`?([^}`]+)`?\})/.exec(body);
      const id = idm ? (idm[1] || idm[2]).trim() : null;
      if (id && htmlForTargets.has(id)) continue;
      offenders.push(`${file.replace(SRC, "src")}:${line}`);
    }
  }
  return { offenders, total };
}

test("the scan still finds form controls", () => {
  // Guard-the-guard: a broken matcher reports a perfectly labelled codebase.
  expect(unnamed().total).toBeGreaterThanOrEqual(400);
});

test("every form control has a programmatic name", () => {
  expect(unnamed().offenders).toEqual([]);
});
