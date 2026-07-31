import { render, screen, waitFor } from "@testing-library/react";
import ValidationStudiesListPage from "@/app/lab-knowledge/validation-studies/page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => null }));
jest.mock("@/components/layout/Header", () => ({ Header: () => null }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockPush.mockReset();
});

describe("ValidationStudiesListPage", () => {
  it("lists studies with their outcome and links each to its detail page", async () => {
    mockGet.mockResolvedValue([
      { id: 7, state: "classified", classification: "validated", confidence: 100, source_doi: "10.1/x", created_at: "2026-07-06T00:00:00Z" },
      { id: 8, state: "running", classification: null, confidence: null, source_accession: "GSE9", created_at: "2026-07-07T00:00:00Z" },
    ]);

    render(<ValidationStudiesListPage />);

    await waitFor(() => expect(screen.getByText("Fully Validated")).toBeInTheDocument());
    // The running study shows its stage, not a validation verdict.
    expect(screen.getByText("Running analysis")).toBeInTheDocument();
    expect(screen.queryByText("Could Not Reproduce")).not.toBeInTheDocument();
    // Each row links to the detail page.
    expect(screen.getByRole("link", { name: /Study #7/ })).toHaveAttribute("href", "/lab-knowledge/validation-studies/7");
    expect(screen.getByRole("link", { name: /Study #8/ })).toHaveAttribute("href", "/lab-knowledge/validation-studies/8");
  });

  it("shows an empty state when there are no studies", async () => {
    mockGet.mockResolvedValue([]);
    render(<ValidationStudiesListPage />);
    await waitFor(() => expect(screen.getByText(/no validation studies/i)).toBeInTheDocument());
  });
});
