/**
 * Literature searches. The watch used one shared cancel flag, so a new submission
 * revived a stopped watch; results loaded in arrival order, so an earlier click's late answer
 * replaced the search the user chose; and a watched search finishing took over the selection.
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
    },
  };
});

import LiteratureSearchesPage from "./page";
import { literature } from "@/lib/literature";

const mockList = literature.listSearches as jest.Mock;
const mockSubmit = literature.submitSearch as jest.Mock;
const mockGetSearch = literature.getSearch as jest.Mock;
const mockResults = literature.getSearchResults as jest.Mock;

function search(id: number, query: string, status = "running") {
  return {
    id, query_text: query, sources: [], per_source_status: { pubmed: status }, status,
    result_count: null, error_message: null, started_at: null, completed_at: null,
    created_at: "2026-07-31T00:00:00Z",
  };
}
function paper(id: number, title: string) {
  return { id, title, authors: [], in_library: false, abstract: null, journal: null, publication_date: null, doi: null };
}

let status: Record<number, string>;
type Pending = { id: number; resolve: (v: unknown) => void };
let resultReads: Pending[];

async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}

async function submit(id: number, query: string) {
  mockSubmit.mockResolvedValueOnce(search(id, query));
  fireEvent.change(screen.getByRole("textbox", { name: "Literature Search" }), { target: { value: query } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /^Search$/ }));
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  status = {};
  resultReads = [];
  mockList.mockReset();
  mockSubmit.mockReset();
  mockGetSearch.mockReset();
  mockResults.mockReset();
  mockList.mockResolvedValue({ items: [search(1, "first query", "complete"), search(2, "second query", "complete")] });
  mockGetSearch.mockImplementation((id: number) => Promise.resolve(search(id, "q", status[id] ?? "running")));
  mockResults.mockImplementation((id: number) => new Promise((resolve) => resultReads.push({ id, resolve })));
});
afterEach(() => jest.useRealTimers());

test("a stopped watch stays stopped when a new search is submitted", async () => {
  render(<LiteratureSearchesPage />);
  await submit(7, "alpha");
  await tick(1000);
  fireEvent.click(screen.getByRole("button", { name: /stop watching/i }));
  await submit(8, "beta");
  for (let i = 0; i < 5; i++) await tick(1000);

  expect(mockGetSearch.mock.calls.filter(([id]) => id === 7)).toHaveLength(1);
  expect(mockGetSearch.mock.calls.filter(([id]) => id === 8).length).toBeGreaterThan(1);
});

test("an earlier search's late results do not replace the search the user chose", async () => {
  render(<LiteratureSearchesPage />);
  await act(async () => {
    await Promise.resolve();
  });
  fireEvent.click(screen.getByText("first query"));
  fireEvent.click(screen.getByText("second query"));

  const second = resultReads.find((r) => r.id === 2)!;
  const first = resultReads.find((r) => r.id === 1)!;
  await act(async () => {
    second.resolve({ items: [paper(20, "Second paper")] });
  });
  await act(async () => {
    first.resolve({ items: [paper(10, "First paper")] });
  });

  expect(screen.getByText("Second paper")).toBeInTheDocument();
  expect(screen.queryByText("First paper")).not.toBeInTheDocument();
});

test("a watched search finishing does not take over the search the user is viewing", async () => {
  render(<LiteratureSearchesPage />);
  await submit(7, "alpha");
  fireEvent.click(screen.getByText("first query"));
  await act(async () => {
    resultReads.find((r) => r.id === 1)!.resolve({ items: [paper(10, "First paper")] });
  });

  status[7] = "complete";
  await tick(1000);
  await act(async () => {
    await Promise.resolve();
  });

  expect(screen.getByText("First paper")).toBeInTheDocument();
  expect(resultReads.some((r) => r.id === 7)).toBe(false);
});
