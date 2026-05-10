import "@testing-library/jest-dom";
import React from "react";

// react-markdown and remark-gfm ship as ESM, which next/jest does not transform
// by default. Tests don't exercise markdown rendering, so a passthrough mock is
// sufficient and avoids a brittle transformIgnorePatterns allowlist.
jest.mock("react-markdown", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}));
jest.mock("remark-gfm", () => ({ __esModule: true, default: () => {} }));
