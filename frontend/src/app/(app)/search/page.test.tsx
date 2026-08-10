import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockPush = jest.fn();
const mockGetParam = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => ({ get: (k: string) => mockGetParam(k) }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import SearchPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

function fileHit(over: Record<string, unknown> = {}) {
  return {
    entity_type: "file",
    entity_id: 5,
    title: "results.csv",
    snippet: "csv · from salmon · CTX-9",
    url: "/data/files?file=5",
    experiment_id: 1,
    relevance_score: null,
    ...over,
  };
}

function result(over: Record<string, unknown> = {}) {
  return {
    results: [fileHit()],
    total: 1,
    page: 1,
    page_size: 25,
    type_counts: { file: 1, experiment: 2 },
    ...over,
  };
}

beforeEach(() => {
  mockPush.mockReset();
  mockGet.mockReset();
  mockGetParam.mockReset();
  mockGetParam.mockReturnValue("results.csv");
});

test("pre-fills the query from the url and renders result cards", async () => {
  mockGet.mockResolvedValue(result());
  render(<SearchPage />);

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(mockGet.mock.calls[0][0]).toContain("query=results.csv");
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("results.csv");

  expect(await screen.findByText("results.csv")).toBeInTheDocument();
  expect(screen.getByText("File")).toBeInTheDocument(); // type badge
  expect(screen.getByText(/from salmon/)).toBeInTheDocument(); // disambiguation context
});

test("clicking a result navigates straight to its url", async () => {
  mockGet.mockResolvedValue(result());
  render(<SearchPage />);

  fireEvent.click(await screen.findByText("results.csv"));
  expect(mockPush).toHaveBeenCalledWith("/data/files?file=5");
});

test("selecting a type re-fetches with the entity_types filter", async () => {
  mockGet.mockResolvedValue(result());
  render(<SearchPage />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());

  fireEvent.change(screen.getByLabelText("Filter by type"), { target: { value: "file" } });

  await waitFor(() =>
    expect(mockGet.mock.calls.some((c) => String(c[0]).includes("entity_types=file"))).toBe(true),
  );
});

test("the Next button requests the following page", async () => {
  mockGet.mockResolvedValue(result({ total: 60 }));
  render(<SearchPage />);
  await screen.findByText("results.csv");

  fireEvent.click(screen.getByRole("button", { name: /next/i }));

  await waitFor(() =>
    expect(mockGet.mock.calls.some((c) => String(c[0]).includes("page=2"))).toBe(true),
  );
});

test("with no query it prompts instead of searching", async () => {
  mockGetParam.mockReturnValue("");
  render(<SearchPage />);

  expect(screen.getByText(/enter a .*term/i)).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 20));
  expect(mockGet).not.toHaveBeenCalled();
});

test("shows a no-results message when nothing matches", async () => {
  mockGet.mockResolvedValue(result({ results: [], total: 0, type_counts: {} }));
  render(<SearchPage />);

  expect(await screen.findByText(/no results/i)).toBeInTheDocument();
});

test("shows an error state when the request fails", async () => {
  mockGet.mockRejectedValue(new Error("boom"));
  render(<SearchPage />);

  expect(await screen.findByText(/something went wrong/i)).toBeInTheDocument();
});
