import { readFileSync, readdirSync } from "fs";
import { join } from "path";

// bioAF is a table-heavy app: 291 <th> across projects, experiments, runs,
// users, files, datasets and settings. Not one of them carried `scope`, so no
// column header was ever associated with the cells beneath it and a screen
// reader read every data table as an undifferentiated grid of values.
//
// This is a source guard rather than a render test on purpose. The defect is a
// mechanical property of ~60 files, so a per-component test would assert it in
// the handful of places someone remembered and let the rest drift straight back.
// A guard fails the moment a new headerless <th> is added anywhere.

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

/**
 * Yield every `<th ...>` opening tag with its body intact.
 *
 * A line-level regex is not good enough here: JSX splits attributes across
 * newlines, and the earlier pass on this codebase undercounted `<tr onClick>`
 * by 4x for exactly that reason. So walk to the matching `>` while tracking
 * brace depth and string state, which keeps `className={`...`}` expressions
 * from ending the tag early.
 */
function thTags(src: string): { body: string; line: number }[] {
  const found: { body: string; line: number }[] = [];
  const start = /<th(?=[\s/>])/g;
  let m: RegExpExecArray | null;
  while ((m = start.exec(src))) {
    let i = m.index + 3;
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

test("every table header cell declares what it labels", () => {
  const offenders: string[] = [];

  for (const file of tsxFiles(SRC)) {
    const src = readFileSync(file, "utf8");
    for (const { body, line } of thTags(src)) {
      if (!/\bscope=/.test(body)) {
        offenders.push(`${file.replace(SRC, "src")}:${line}`);
      }
    }
  }

  expect(offenders).toEqual([]);
});

test("the guard can actually see a headerless th", () => {
  // Guard-the-guard: an empty offender list is only meaningful if the scanner
  // finds a real one. Without this, a broken matcher reads as a clean codebase.
  const sample = `
    <table>
      <thead>
        <tr>
          <th scope="col" className={\`px-4 ${"${x > 1 ? 'a' : 'b'}"}\`}>Name</th>
          <th
            className="px-4"
          >Status</th>
        </tr>
      </thead>
    </table>`;

  const tags = thTags(sample);
  expect(tags).toHaveLength(2);
  expect(tags.filter((t) => !/\bscope=/.test(t.body))).toHaveLength(1);
});
