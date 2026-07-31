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

test("labels paper origin consistently in the filter and the table column (no Provenance/Source split)", async () => {
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
  // Both the filter label and the table column header read "Origin".
  expect(screen.getAllByText("Origin")).toHaveLength(2);
  // The old split names are gone: no "Provenance" label, no "Source" column header.
  expect(screen.queryByText("Provenance")).not.toBeInTheDocument();
  const headers = screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());
  expect(headers).toContain("Origin");
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
