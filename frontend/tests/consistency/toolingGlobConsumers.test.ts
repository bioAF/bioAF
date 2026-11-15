/**
 * Tooling guard: the glob and minimatch copies the security overrides select still work for the tools
 * that call them (frontend/DEPENDENCIES.md, "Overrides").
 *
 * The overrides move Jest's glob callers and test-exclude to glob 14 / minimatch 11, and
 * @typescript-eslint/typescript-estree to minimatch 11. A mismatched pair installs cleanly and audits
 * clean, yet matches nothing: glob 10 with minimatch 11 returned zero files for every pattern. This
 * suite would not notice on its own (the v8 coverage provider does not list files through glob), so
 * each consumer's own copy is resolved from where the consumer lives and called the way it calls it.
 */

import * as path from "path";

const ROOT = path.join(__dirname, "..", "..");
const KNOWN = "src/lib/experimentOptions.ts";

/** Require `request` as `consumer` sees it; `consumer` is a chain of packages, each resolved from the last. */
function requireFrom(consumer: string, request: string) {
  let base = ROOT;
  for (const name of consumer.split(" > ")) {
    base = path.dirname(require.resolve(`${name}/package.json`, { paths: [base] }));
  }
  return require(require.resolve(request, { paths: [base] }));
}

describe("glob consumers selected by the overrides", () => {
  it.each(["@jest/reporters", "jest-runtime", "@jest/core > jest-config", "jest-cli > jest-config"])("%s's glob finds source files", (consumer) => {
    // jest-runtime and @jest/reporters call glob.sync(pattern, { cwd, ... }).
    const { glob } = requireFrom(consumer, "glob");
    const found: string[] = glob.sync("src/lib/*.ts", { cwd: ROOT });
    expect(found.map((f) => f.split(path.sep).join("/"))).toContain(KNOWN);
  });

  it("test-exclude lists source files and applies its excludes", () => {
    const TestExclude = requireFrom("ts-jest > @jest/transform > babel-plugin-istanbul", "test-exclude");
    const exclude = new TestExclude({ cwd: ROOT, include: ["src/lib/**/*.ts"], exclude: ["**/*.d.ts"] });
    const files: string[] = exclude.globSync();
    expect(files.map((f) => f.split(path.sep).join("/"))).toContain(KNOWN);
    expect(exclude.shouldInstrument(path.join(ROOT, KNOWN))).toBe(true);
    expect(exclude.shouldInstrument(path.join(ROOT, "src/lib/thing.d.ts"))).toBe(false);
  });
});

describe("minimatch consumer selected by the overrides", () => {
  it("typescript-estree's minimatch matches its default-project patterns", () => {
    // useProgramFromProjectService: minimatch(filePath, pattern, { dot: true }).
    const { minimatch } = requireFrom("eslint-config-next > @typescript-eslint/parser > @typescript-eslint/typescript-estree", "minimatch");
    expect(minimatch("src/lib/experimentOptions.ts", "src/**/*.ts", { dot: true })).toBe(true);
    expect(minimatch(".eslintrc.js", "*.js", { dot: true })).toBe(true);
    expect(minimatch("src/lib/experimentOptions.ts", "tests/**/*.ts", { dot: true })).toBe(false);
  });
});
