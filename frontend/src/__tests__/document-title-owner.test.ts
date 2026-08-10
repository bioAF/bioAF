import { readFileSync } from "fs";
import { join } from "path";

// The root layout must NOT declare `metadata.title`.
//
// Next re-asserts a metadata title asynchronously after hydration. With one
// declared, DocumentTitle's effect ran, set the right title, and had it
// reverted a moment later: measured on the deployed app, the tab was correct
// after a client-side nav ("Profile - bioAF") and still said "bioAF" on all
// seven routes after a full page load. Nothing errored, and both the unit tests
// and the effect itself reported success, so only the browser showed it.
//
// The pre-hydration title is a plain `<title>` element in the layout's own
// `<head>` instead. React renders it once and never touches it again, so
// DocumentTitle owns the title from hydration onwards.

const LAYOUT = join(__dirname, "..", "app", "layout.tsx");

function metadataBlock(src: string): string {
  const start = src.indexOf("export const metadata");
  if (start === -1) return "";
  const end = src.indexOf("};", start);
  return src.slice(start, end);
}

test("the root layout does not declare a metadata title", () => {
  const src = readFileSync(LAYOUT, "utf8");
  expect(metadataBlock(src)).not.toMatch(/(^|\s)title\s*:/);
});

test("the root layout still renders a pre-hydration title element", () => {
  const src = readFileSync(LAYOUT, "utf8");
  expect(src).toMatch(/<title>[^<]+<\/title>/);
});

test("the root layout mounts DocumentTitle", () => {
  const src = readFileSync(LAYOUT, "utf8");
  expect(src).toMatch(/<DocumentTitle\s*\/>/);
});
