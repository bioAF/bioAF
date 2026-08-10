import { titleForPath, APP_NAME } from "@/lib/pageTitle";
import { navConfig } from "@/lib/navConfig";

// Measured on the deployed app at f62fd98e: seven routes were loaded and every
// one of them reported document.title === "bioAF". One distinct title across the
// whole app, while every <h1> differed. A browser tab, a history entry and a
// bookmark could not tell one page from another, and a screen reader got no
// announcement that the page had changed at all.

test("a page's title is its own name, not just the app's", () => {
  expect(titleForPath("/pipelines/runs")).toBe(`Pipeline Runs - ${APP_NAME}`);
  expect(titleForPath("/dashboard")).toBe(`Dashboard - ${APP_NAME}`);
});

test("every nav destination resolves to a distinct, named title", () => {
  const paths: string[] = [];
  for (const section of navConfig) {
    if (section.path) paths.push(section.path);
    for (const child of section.children ?? []) paths.push(child.path);
  }
  expect(paths.length).toBeGreaterThan(10);

  const titles = paths.map(titleForPath);
  for (const t of titles) {
    expect(t.endsWith(` - ${APP_NAME}`)).toBe(true);
    expect(t).not.toBe(APP_NAME);
    // The name must carry something beyond the app's own.
    expect(t.replace(` - ${APP_NAME}`, "").trim().length).toBeGreaterThan(0);
  }
  // The whole point is that tabs are told apart, so the titles must differ.
  expect(new Set(titles).size).toBeGreaterThan(paths.length * 0.9);
});

test("a title is the nav label verbatim, so tab, breadcrumb and heading agree", () => {
  // nav-label-agreement.test.ts already holds "nav label == the page's h1".
  // Deriving the title from the same labels makes the tab agree with both.
  for (const section of navConfig) {
    if (section.path) {
      expect(titleForPath(section.path)).toBe(`${section.label} - ${APP_NAME}`);
    }
    for (const child of section.children ?? []) {
      expect(titleForPath(child.path)).toBe(`${child.label} - ${APP_NAME}`);
    }
  }
});

test("a detail page under a known section is named after that section", () => {
  expect(titleForPath("/pipelines/runs/21")).toBe(`Pipeline Runs - ${APP_NAME}`);
});

test("an unknown route is titled from its own path rather than left bare", () => {
  expect(titleForPath("/some-unknown/deep-page")).toBe(`Deep page - ${APP_NAME}`);
  expect(titleForPath("/login")).toBe(`Login - ${APP_NAME}`);
});

test("the root path is the dashboard, matching how the nav resolves it", () => {
  expect(titleForPath("/")).toBe(`Dashboard - ${APP_NAME}`);
});

test("no title contains an em-dash", () => {
  // Repo-wide rule. The separator is an ASCII hyphen.
  for (const section of navConfig) {
    if (section.path) expect(titleForPath(section.path)).not.toMatch(/—/);
  }
});
