import { readFileSync, readdirSync } from "fs";
import { join } from "path";

// An onClick on a <div>, <tr>, <li>, <th> or <img> is mouse-only: none of those
// elements is in the tab order and none has built-in key behaviour, so there is
// no sequence a keyboard user can press to reach them. Opening a record, a
// notification, a dataset or a lab document all worked this way.
//
// Two shapes are deliberately NOT defects and are excluded below, because
// treating them as defects is worse than leaving them alone:
//
//   - A handler whose whole body is `e.stopPropagation()` is a bubbling guard.
//     There is no action to activate, and making it focusable would put a dead
//     stop in the tab order.
//   - A `fixed inset-0` layer is a click-outside-to-dismiss overlay. Its
//     keyboard equivalent is Escape on the dialog, not a focusable invisible
//     sheet covering the viewport.

const SRC = join(__dirname, "..");
const CLICKABLE = ["div", "tr", "li", "td", "th", "img", "span", "section", "article"];

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

/** The balanced `{...}` value of the onClick attribute, or "". */
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

// Spreading a helper from @/lib/a11y IS the keyboard path: it supplies onClick,
// onKeyDown and tabIndex together. The literal attributes are therefore absent
// from the tag body, so the scan has to recognise the call or it would both
// re-flag every element it just fixed and stop counting them as scanned.
const HELPER_SPREAD = /\{\s*\.\.\.\s*clickable(Row|Card)\s*\(/;

// Documented exceptions. Each needs a reason that says why a keyboard path here
// would be WRONG, not merely inconvenient.
const EXCEPTIONS: Record<string, string> = {
  // The compute-stack cards are a radio group, not buttons. role="button" would
  // be a knowingly wrong semantic, and the step is already completable by
  // keyboard through its "Continue with ..." button, so nothing is blocked.
  // Belongs to the radio-semantics fix, which is separate work.
  "src/components/auth/SetupWizard.tsx:1221 <div>": "radio group, not a button",
};

function mouseOnly() {
  const offenders: string[] = [];
  let scanned = 0;
  for (const file of tsxFiles(SRC)) {
    const src = readFileSync(file, "utf8");
    const tag = /<([a-z][a-zA-Z0-9]*)(?=[\s/>])/g;
    let m: RegExpExecArray | null;
    while ((m = tag.exec(src))) {
      if (!CLICKABLE.includes(m[1])) continue;
      const end = tagBodyEnd(src, m.index + m[0].length);
      if (end < 0) continue;
      const body = src.slice(m.index, end + 1);
      const viaHelper = HELPER_SPREAD.test(body);
      if (!body.includes("onClick") && !viaHelper) continue;
      scanned++;
      if (viaHelper) continue;
      if (/onKeyDown|onKeyPress|onKeyUp|tabIndex/.test(body)) continue;
      const expr = onClickExpr(body);
      if (/^\(?e\)?\s*=>\s*e\.stopPropagation\(\)$/.test(expr)) continue;
      if (body.includes("inset-0") && body.includes("fixed")) continue;
      const line = src.slice(0, m.index).split("\n").length;
      const ref = `${file.replace(SRC, "src")}:${line} <${m[1]}>`;
      if (ref in EXCEPTIONS) continue;
      offenders.push(ref);
    }
  }
  return { offenders, scanned };
}

test("the scan still finds clickable elements at all", () => {
  // Guard-the-guard: if the tag matcher breaks, every offender disappears and
  // the suite reports a clean codebase.
  expect(mouseOnly().scanned).toBeGreaterThanOrEqual(50);
});

test("nothing is clickable by mouse only", () => {
  expect(mouseOnly().offenders).toEqual([]);
});
