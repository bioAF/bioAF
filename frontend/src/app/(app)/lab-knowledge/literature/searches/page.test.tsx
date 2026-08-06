import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import LiteratureSearchesPage from "./page";
import { literature, type SearchSummary } from "@/lib/literature";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/lab-knowledge/literature/searches",
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "admin" }),
}));
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

const mockListSearches = literature.listSearches as jest.Mock;
const mockSubmitSearch = literature.submitSearch as jest.Mock;
const mockGetSearch = literature.getSearch as jest.Mock;

function runningSearch(per: Record<string, string>): SearchSummary {
  return {
    id: 9,
    query_text: "tgf-beta",
    sources: [],
    per_source_status: per,
    status: "running",
    result_count: null,
    error_message: null,
    started_at: null,
    completed_at: null,
    created_at: "2026-07-31T00:00:00Z",
  };
}

test("renders a breadcrumb back to the Literature library", async () => {
  mockListSearches.mockResolvedValue({ items: [] });
  render(<LiteratureSearchesPage />);
  const breadcrumb = await screen.findByTestId("breadcrumb");
  expect(breadcrumb).toHaveTextContent("Literature");
  expect(screen.getByTestId("breadcrumb-current")).toHaveTextContent("Searches");
});

test("shows live per-source progress while a search runs, and Stop watching ends the wait", async () => {
  mockListSearches.mockResolvedValue({ items: [] });
  mockSubmitSearch.mockResolvedValue(
    runningSearch({ pubmed: "complete", biorxiv: "running", europepmc: "queued", semantic_scholar: "queued" }),
  );
  // The poll keeps returning a still-running status, so the panel stays up until the user acts.
  mockGetSearch.mockResolvedValue(
    runningSearch({ pubmed: "complete", biorxiv: "running", europepmc: "queued", semantic_scholar: "queued" }),
  );

  render(<LiteratureSearchesPage />);
  fireEvent.change(screen.getByPlaceholderText(/TGF-beta/i), { target: { value: "tgf-beta signalling" } });
  fireEvent.click(screen.getByRole("button", { name: /^Search$/ }));

  // Progress shows the honest source count (1 of 4 done) from the created record.
  expect(await screen.findByTestId("search-progress")).toBeInTheDocument();
  expect(screen.getByText(/1 of 4 sources/i)).toBeInTheDocument();

  // Stopping returns the user to an interactive page (the search continues server-side).
  fireEvent.click(screen.getByRole("button", { name: /stop watching/i }));
  await waitFor(() => expect(screen.queryByTestId("search-progress")).not.toBeInTheDocument());
});

test("shows a retry-able error when searches fail to load", async () => {
  mockListSearches.mockRejectedValue(new Error("boom"));
  render(<LiteratureSearchesPage />);

  expect(await screen.findByTestId("error-state")).toBeInTheDocument();
  expect(screen.getByText(/searches could not be loaded/i)).toBeInTheDocument();
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
});

test("the query box is named for what it is, not for its example text", async () => {
  // The placeholder is an EXAMPLE of a query, not a label. Naming the control
  // after it made a screen reader announce the whole worked example
  // ("e.g., TGF-beta signalling in triple-negative breast cancer") every time
  // focus landed there. The example stays visible; the name says what the box
  // is for.
  mockListSearches.mockResolvedValue({ items: [] });

  render(<LiteratureSearchesPage />);

  const box = await screen.findByRole("textbox", { name: "Literature Search" });
  expect(box).toHaveAttribute(
    "placeholder",
    "e.g., TGF-beta signalling in triple-negative breast cancer",
  );
});
