import { readFileSync, readdirSync } from "fs";
import { join } from "path";

// `text-gray-400` is #9ca3af, which is 2.54:1 on white. AA wants 4.5:1 for
// normal text and 3:1 even for large text, so it failed both, and it was used
// 361 times for real content: paragraphs, definition terms, table empty cells,
// and empty-state headings.
//
// `text-gray-500` is #6b7280 = 4.83:1 on white and 4.66:1 on gray-50, so it
// clears AA on the backgrounds this app actually uses while staying visibly
// lighter than body text, which is what the shade was chosen for.
//
// Dark mode is unaffected: globals.css already maps `.dark .text-gray-500` and
// `.dark .text-gray-400` to the same token.

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

test("no text uses the shade that fails AA", () => {
  const offenders: string[] = [];
  for (const file of tsxFiles(SRC)) {
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => {
        // `disabled:text-gray-400` is allowed. WCAG 1.4.3 exempts inactive
        // controls, and a disabled field that looks as solid as an active one
        // is its own usability problem.
        const bare = line.replace(/disabled:text-gray-400/g, "");
        if (bare.includes("text-gray-400")) {
          offenders.push(`${file.replace(SRC, "src")}:${i + 1}`);
        }
      });
  }
  expect(offenders).toEqual([]);
});
