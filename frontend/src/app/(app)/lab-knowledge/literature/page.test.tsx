import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import LiteraturePage from "./page";
import { literature } from "@/lib/literature";
import { api } from "@/lib/api";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/lab-knowledge/literature",
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "admin" }),
}));
jest.mock("@/components/literature/AssociatePaperModal", () => ({ AssociatePaperModal: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
// Keep the real helper functions (cleanText, formatAuthors, ...) the page renders with; only
// override the api-calling `literature` object.
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return { ...actual, literature: { listPapers: jest.fn(), bulkDismiss: jest.fn() } };
});

const mockListPapers = literature.listPapers as jest.Mock;
const mockApiGet = api.get as jest.Mock;

beforeEach(() => {
  mockApiGet.mockResolvedValue({ projects: [], experiments: [] });
  mockListPapers.mockReset();
});

test("makes each paper keyboard-openable via a title link and labels the bulk checkboxes", async () => {
  mockListPapers.mockResolvedValue({
    items: [
      {
        id: 42,
        title: "CRISPR screen paper",
        authors: [],
        publication_date: null,
        journal: null,
        provenance: "user_upload",
        reading_status: "unread",
        dismissed: false,
        has_full_text: false,
        associations: [{ id: 1, scope_type: "experiment", scope_id: 7, label: "Exp 7" }],
        comment_count: 0,
      },
    ],
    total: 1,
  });
  render(<LiteraturePage />);

  // The title is a real link (focusable + keyboard-activatable), not a div behind a row onClick.
  const link = await screen.findByRole("link", { name: /CRISPR screen paper/ });
  expect(link).toHaveAttribute("href", "/lab-knowledge/literature/papers/42");

  // The select-all and per-row checkboxes carry accessible names for screen readers.
  expect(screen.getByRole("checkbox", { name: /select all/i })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: /select CRISPR screen paper/i })).toBeInTheDocument();
});

test("labels paper provenance consistently in the filter and the table column (no Provenance/Source split)", async () => {
  mockListPapers.mockResolvedValue({
    items: [
      {
        id: 1,
        title: "A paper",
        authors: [],
        publication_date: null,
        journal: null,
        provenance: "user_upload",
        reading_status: "unread",
        dismissed: false,
        has_full_text: false,
        associations: [],
        comment_count: 0,
      },
    ],
    total: 1,
  });
  render(<LiteraturePage />);

  await screen.findByText("A paper");
  // "Provenance" is the domain-correct term; the filter and the column now agree on it.
  expect(screen.getAllByText("Provenance")).toHaveLength(2);
  // The split is resolved: the column no longer reads "Source".
  const headers = screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());
  expect(headers).toContain("Provenance");
  expect(headers).not.toContain("Source");
});

test("renders a breadcrumb locating the page under Lab Knowledge", async () => {
  mockListPapers.mockResolvedValue({ items: [], total: 0 });
  render(<LiteraturePage />);

  const breadcrumb = await screen.findByTestId("breadcrumb");
  expect(breadcrumb).toHaveTextContent("Lab Knowledge");
  expect(breadcrumb).toHaveTextContent("Literature");
});

test("shows a retry-able error, not the empty state, when the library fails to load", async () => {
  mockListPapers.mockRejectedValue(new Error("kaboom"));
  render(<LiteraturePage />);

  expect(await screen.findByTestId("error-state")).toBeInTheDocument();
  expect(screen.getByText(/couldn't load the library/i)).toBeInTheDocument();
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
  expect(screen.queryByText(/no papers match/i)).not.toBeInTheDocument();
});

test("recovers to the list when Retry succeeds", async () => {
  mockListPapers
    .mockRejectedValueOnce(new Error("kaboom"))
    .mockResolvedValueOnce({ items: [], total: 0 });
  render(<LiteraturePage />);

  fireEvent.click(await screen.findByTestId("error-retry"));

  await waitFor(() => expect(screen.getByText(/no papers match/i)).toBeInTheDocument());
  expect(screen.queryByTestId("error-state")).not.toBeInTheDocument();
});
