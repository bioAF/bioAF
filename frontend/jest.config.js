const nextJest = require("next/jest.js");

const createJestConfig = nextJest({
  dir: "./",
});

/** @type {import('jest').Config} */
const config = {
  testEnvironment: "jsdom",
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
  },
  testPathIgnorePatterns: ["<rootDir>/node_modules/", "<rootDir>/.next/"],
  modulePathIgnorePatterns: ["<rootDir>/.next/"],
  // With --coverage, measure every source file, not only those a test imports. The v8 provider
  // avoids Babel instrumentation, which slowed suites past their timeouts.
  coverageProvider: "v8",
  collectCoverageFrom: ["src/**/*.{ts,tsx}", "!src/**/*.d.ts", "!src/**/__tests__/**", "!src/testing/**"],
};

// The real Markdown renderer and its parser ship as ESM only. Security tests must exercise that
// renderer (a passthrough mock proves nothing), so these packages are transformed like source.
// A new ESM-only dependency of react-markdown fails loudly with "Cannot use import statement".
const MARKDOWN_ESM_PACKAGES = [
  "react-markdown",
  "remark-[^/]+",
  "micromark[^/]*",
  "mdast-util-[^/]+",
  "hast-util-[^/]+",
  "unist-util-[^/]+",
  "estree-util-[^/]+",
  "unified",
  "vfile[^/]*",
  "bail",
  "ccount",
  "character-[^/]+",
  "comma-separated-tokens",
  "decode-named-character-reference",
  "devlop",
  "html-url-attributes",
  "is-alphabetical",
  "is-alphanumerical",
  "is-decimal",
  "is-hexadecimal",
  "is-plain-obj",
  "longest-streak",
  "parse-entities",
  "property-information",
  "space-separated-tokens",
  "stringify-entities",
  "trim-lines",
  "trough",
  "zwitch",
  "@ungap/structured-clone",
];

module.exports = async () => {
  const resolved = await createJestConfig(config)();
  const nextDefault = "/node_modules/(?!.pnpm)(?!(geist)/)";
  const index = resolved.transformIgnorePatterns.indexOf(nextDefault);
  if (index === -1) throw new Error("next/jest changed its node_modules transform pattern; update jest.config.js");
  resolved.transformIgnorePatterns[index] = `/node_modules/(?!.pnpm)(?!(geist|${MARKDOWN_ESM_PACKAGES.join("|")})/)`;
  return resolved;
};
