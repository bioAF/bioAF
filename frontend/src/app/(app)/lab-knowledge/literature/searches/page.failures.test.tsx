/**
 * Owner review: the results resource's failures never rendered. A failed results load showed
 * nothing, and after Add to Library a failed refresh left the old, still-actionable results on
 * screen with no warning. Both are now said.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/lab-knowledge/literature/searches",
}));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true, getCurrentUser: () => ({ role_name: "admin" }) }));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return {
    ...actual,
    literature: {
      listSearches: jest.fn(),
      submitSearch: jest.fn(),
      getSearch: jest.fn(),
      getSearchResults: jest.fn(),
      addToLibrary: jest.fn(),
      bulkAddToLibrary: jest.fn(),
    },
  };
});

import LiteratureSearchesPage from "./page";
import { literature } from "@/lib/literature";

const mockList = literature.listSearches as jest.Mock;
const mockResults = literature.getSearchResults as jest.Mock;
const mockAdd = literature.addToLibrary as jest.Mock;

const SEARCH = {
  id: 1, query_text: "crispr screens", sources: [], per_source_status: { pubmed: "complete" }, status: "complete",
  result_count: 1, error_message: null, started_at: null, completed_at: null, created_at: "2026-07-31T00:00:00Z",
};
const PAPER = { id: 5, title: "A screen paper", authors: [], in_library: false, abstract: null, journal: null, publication_date: null, doi: null };

async function flush() {
  await act(async () => {
    for (let n = 0; n < 10; n++) await Promise.resolve();
  });
}

async function openSearch() {
  render(<LiteratureSearchesPage />);
  await flush();
  fireEvent.click(screen.getByText("crispr screens"));
  await flush();
}

beforeEach(() => {
  mockList.mockReset();
  mockResults.mockReset();
  mockAdd.mockReset();
  mockList.mockResolvedValue({ items: [SEARCH] });
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("a failed results load is said, not shown as no results", async () => {
  mockResults.mockRejectedValue(new Error("502"));
  await openSearch();

  expect(screen.getByTestId("search-results-error")).toHaveTextContent(/could not be loaded/i);
  mockResults.mockResolvedValue({ items: [PAPER] });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await flush();
  expect(screen.queryByTestId("search-results-error")).not.toBeInTheDocument();
  expect(screen.getByText("A screen paper")).toBeInTheDocument();
});

test("a failed refresh after Add to Library keeps the results, marked out of date, and Retry recovers", async () => {
  mockResults.mockResolvedValueOnce({ items: [PAPER] });
  await openSearch();
  expect(screen.getByText("A screen paper")).toBeInTheDocument();

  mockAdd.mockResolvedValue({});
  mockResults.mockRejectedValueOnce(new Error("502"));
  await act(async () => {
    // The paper's own button (the bulk "Add N to Library" button is disabled with nothing selected).
    fireEvent.click(screen.getAllByRole("button", { name: "Add to Library" }).find((b) => !b.hasAttribute("disabled"))!);
  });
  await flush();

  expect(screen.getByText("A screen paper")).toBeInTheDocument();
  expect(screen.getByTestId("stale-notice")).toHaveTextContent(/Search results could not be refreshed/);

  mockResults.mockResolvedValueOnce({ items: [{ ...PAPER, in_library: true }] });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await flush();
  expect(screen.queryByTestId("stale-notice")).not.toBeInTheDocument();
  expect(screen.getByText("In Library")).toBeInTheDocument();
});
