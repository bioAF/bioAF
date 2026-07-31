import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import LiteraturePage from "./page";
import { literature } from "@/lib/literature";
import { api } from "@/lib/api";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
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
