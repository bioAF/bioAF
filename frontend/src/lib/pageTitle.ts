import { findNavMatch } from "@/lib/navConfig";

export const APP_NAME = "bioAF";

/**
 * The name of the page at `pathname`, for the browser tab.
 *
 * Every route reported `document.title === "bioAF"`: one distinct title across
 * the whole app, measured over seven routes on the deployed build. Tabs, history
 * entries and bookmarks could not be told apart, and a title is also the change a
 * screen reader announces on navigation, so nothing announced one.
 *
 * The name comes from `findNavMatch`, which is what the Sidebar and the
 * Breadcrumb already resolve a path with. `nav-label-agreement.test.ts` holds
 * "a nav label is its page's <h1> verbatim", so deriving the title from the same
 * labels makes the tab, the breadcrumb and the heading say one thing rather than
 * three. A route the nav does not know (a detail page outside any section,
 * `/login`) is named from its own last path segment instead of falling back to
 * the bare app name, because "bioAF" is exactly the answer that was wrong.
 */
export function titleForPath(pathname: string): string {
  return `${pageNameForPath(pathname)} - ${APP_NAME}`;
}

function pageNameForPath(pathname: string): string {
  const match = findNavMatch(pathname);
  if (match) return match.child ? match.child.label : match.section.label;

  const last = pathname.split("/").filter(Boolean).pop();
  if (!last) return APP_NAME;
  const words = last.replace(/-/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
