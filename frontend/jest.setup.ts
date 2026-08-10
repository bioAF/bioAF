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

// useToast() throws outside a ToastProvider on purpose: a no-op fallback would
// recreate the exact bug the toast layer exists to fix (an error that goes
// nowhere). That guard is right for the app but hostile to rendering a page in
// isolation, so tests get a working provider-free implementation by default.
// Toast's own test unmocks this to exercise the real component.
// Stable mock fns (not fresh per render) so a test can assert what was raised.
// Reach them with `import { toastMock } from "@/components/shared/Toast"`.
const toastMock = { error: jest.fn(), success: jest.fn(), info: jest.fn() };
jest.mock("@/components/shared/Toast", () => {
  const actual = jest.requireActual("@/components/shared/Toast");
  return { ...actual, useToast: () => toastMock, toastMock };
});
beforeEach(() => {
  toastMock.error.mockClear();
  toastMock.success.mockClear();
  toastMock.info.mockClear();
});
