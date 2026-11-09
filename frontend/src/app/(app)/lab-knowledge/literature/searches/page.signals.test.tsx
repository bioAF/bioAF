/**
 * The searches list and the selected search's results are read with the resource's signal, so
 * leaving the page or choosing another search cancels the read instead of letting it finish.
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
    literature: { listSearches: jest.fn(), submitSearch: jest.fn(), getSearch: jest.fn(), getSearchResults: jest.fn() },
  };
});

import LiteratureSearchesPage from "./page";
import { literature } from "@/lib/literature";

const mockList = literature.listSearches as jest.Mock;
const mockResults = literature.getSearchResults as jest.Mock;

const search = (id: number, query: string) => ({
  id, query_text: query, sources: [], per_source_status: { pubmed: "complete" }, status: "complete",
  result_count: null, error_message: null, started_at: null, completed_at: null,
  created_at: "2026-07-31T00:00:00Z",
});

beforeEach(() => {
  mockList.mockReset();
  mockResults.mockReset();
  mockResults.mockImplementation(() => new Promise(() => {}));
});

test("the list read carries a signal that aborts when the page closes", () => {
  mockList.mockImplementation(() => new Promise(() => {}));
  const { unmount } = render(<LiteratureSearchesPage />);
  const signal = mockList.mock.calls[0][0]?.signal as AbortSignal;
  expect(signal).toBeInstanceOf(AbortSignal);
  unmount();
  expect(signal.aborted).toBe(true);
});

test("choosing another search aborts the previous search's results read", async () => {
  mockList.mockResolvedValue({ items: [search(1, "first query"), search(2, "second query")] });
  render(<LiteratureSearchesPage />);
  await act(async () => {
    await Promise.resolve();
  });
  fireEvent.click(screen.getByText("first query"));
  const first = mockResults.mock.calls[0][1]?.signal as AbortSignal;
  expect(first).toBeInstanceOf(AbortSignal);

  fireEvent.click(screen.getByText("second query"));
  expect(first.aborted).toBe(true);
  expect(mockResults).toHaveBeenLastCalledWith(2, expect.objectContaining({ signal: expect.any(AbortSignal) }));
});
