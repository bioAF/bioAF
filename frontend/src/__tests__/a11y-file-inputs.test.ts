import { readFileSync, readdirSync } from "fs";
import { join } from "path";

// Uploading data is bioAF's primary ingestion path, and it was impossible with a
// keyboard. Four drop zones hid their <input type="file"> behind Tailwind's
// `hidden` (display:none), which removes an element from the tab order entirely,
// and put the click handling on a <div> or a <label>, neither of which is
// focusable. There was no key sequence that reached the file picker.
//
// `sr-only` is the fix: it clips the input to a 1px box so the styled drop zone
// is still what you see, but the input stays in the tree, stays focusable, and
// keeps its native Enter/Space behaviour of opening the picker. `hidden` cannot
// be made to work no matter what is wrapped around it, which is why this guard
// bans the class outright rather than checking for a keyboard handler.

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

/** Every `<input ...>` opening tag, read across newlines with brace tracking. */
function inputTags(src: string): { body: string; line: number }[] {
  const found: { body: string; line: number }[] = [];
  const start = /<input(?=[\s/>])/g;
  let m: RegExpExecArray | null;
  while ((m = start.exec(src))) {
    let i = m.index + 6;
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
      else if (depth === 0 && c === ">") break;
    }
    found.push({
      body: src.slice(m.index, i + 1),
      line: src.slice(0, m.index).split("\n").length,
    });
  }
  return found;
}

function fileInputs() {
  const out: { file: string; body: string; line: number }[] = [];
  for (const file of tsxFiles(SRC)) {
    const src = readFileSync(file, "utf8");
    for (const { body, line } of inputTags(src)) {
      if (/type=["']file["']/.test(body)) {
        out.push({ file: file.replace(SRC, "src"), body, line });
      }
    }
  }
  return out;
}

test("the app has file inputs to check, so an empty pass means nothing", () => {
  // Guard-the-guard: if the matcher silently stops finding inputs, every
  // assertion below passes vacuously and the regression ships.
  expect(fileInputs().length).toBeGreaterThanOrEqual(10);
});

test("no file input is display:none, which would drop it from the tab order", () => {
  const offenders = fileInputs()
    // `hidden` as a bare Tailwind class. Guarded against matching `sr-only`,
    // `hidden md:block`, or an unrelated attribute containing the word.
    .filter(({ body }) => /className=["'][^"']*\bhidden\b[^"']*["']/.test(body))
    .map(({ file, line }) => `${file}:${line}`);

  expect(offenders).toEqual([]);
});

test("a visually hidden file input carries its own name", () => {
  // Scoped to `sr-only` inputs on purpose. Once an input is clipped out of
  // sight, its accessible name is the only thing a user has to go on: there is
  // no visible control next to it to infer meaning from. A *visible* file input
  // with no label is also a defect, but it belongs to the form-labels sweep,
  // which is a separate piece of work and not in scope here.
  const offenders = fileInputs()
    .filter(({ body }) => /className=["'][^"']*\bsr-only\b/.test(body))
    .filter(({ body }) => !/aria-label/.test(body) && !/\bid=/.test(body))
    .map(({ file, line }) => `${file}:${line}`);

  expect(offenders).toEqual([]);
});
