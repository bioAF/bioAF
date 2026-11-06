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

module.exports = createJestConfig(config);
