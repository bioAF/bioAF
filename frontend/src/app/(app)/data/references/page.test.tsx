import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ReferencesPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const calls = () => mockGet.mock.calls.map((c) => String(c[0]));

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/filter-options")) {
      return Promise.resolve({
        categories: ["genome", "annotation", "index", "atlas", "markers", "other"],
        scopes: ["public", "internal"],
      });
    }
    return Promise.resolve({ references: [], total: 0 });
  });
});

test("search uses the query parameter the API actually reads", async () => {
  render(<ReferencesPage />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());

  fireEvent.change(screen.getByPlaceholderText(/search by name/i), {
    target: { value: "GRCh38" },
  });

  // The endpoint declares `name_search`; sending `search` was silently discarded
  // by FastAPI, so the box did nothing at all.
  await waitFor(() => expect(calls().some((u) => u.includes("name_search=GRCh38"))).toBe(true));
  expect(calls().some((u) => /[?&]search=/.test(u))).toBe(false);
});

test("scope options come from the API, not a hard-coded list that never matched", async () => {
  render(<ReferencesPage />);
  await waitFor(() => expect(calls().some((u) => u.includes("/filter-options"))).toBe(true));

  await waitFor(() => expect(screen.getByRole("option", { name: "Public" })).toBeInTheDocument());
  expect(screen.getByRole("option", { name: "Internal" })).toBeInTheDocument();
  // These were the old hard-coded values. REFERENCE_SCOPES has never contained
  // them, so selecting either always returned zero rows.
  expect(screen.queryByRole("option", { name: "Global" })).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "Organization" })).not.toBeInTheDocument();
});

test("category options cover everything the upload form can create", async () => {
  render(<ReferencesPage />);
  await waitFor(() => expect(calls().some((u) => u.includes("/filter-options"))).toBe(true));

  await waitFor(() => expect(screen.getByRole("option", { name: "Atlas" })).toBeInTheDocument());
  expect(screen.getByRole("option", { name: "Markers" })).toBeInTheDocument();
  // Not a real category: the model rejects it, so filtering by it returned nothing.
  expect(screen.queryByRole("option", { name: "Transcriptome" })).not.toBeInTheDocument();
});
