import { render, screen, waitFor } from "@testing-library/react";
import ValidationStudiesListPage from "@/app/(app)/lab-knowledge/validation-studies/page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/lab-knowledge/validation-studies",
}));

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));

let litBeta = { available: true, flags: { lit_validation: true } as Record<string, boolean>, loading: false };
jest.mock("@/hooks/useBetaFeatures", () => ({ useBetaFeatures: () => litBeta }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockPush.mockReset();
  litBeta = { available: true, flags: { lit_validation: true }, loading: false };
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

  it("labels a study by its resolved title, keeping the id as a secondary qualifier", async () => {
    mockGet.mockResolvedValue([
      { id: 7, state: "requested", title: "Batched Title Paper", created_at: "2026-07-06T00:00:00Z" },
      { id: 8, state: "requested", title: "Study #8", created_at: "2026-07-07T00:00:00Z" },
    ]);

    render(<ValidationStudiesListPage />);

    const link = await screen.findByRole("link", { name: /Batched Title Paper/i });
    expect(link).toHaveAttribute("href", "/lab-knowledge/validation-studies/7");
    expect(screen.getByText("#7")).toBeInTheDocument();
    // a study with only the fallback title does not read "Study #8 #8"
    expect(screen.queryByText("#8")).not.toBeInTheDocument();
  });

  it("renders a breadcrumb locating the page under Lab Knowledge", async () => {
    mockGet.mockResolvedValue([]);
    render(<ValidationStudiesListPage />);
    const breadcrumb = await screen.findByTestId("breadcrumb");
    expect(breadcrumb).toHaveTextContent("Lab Knowledge");
    expect(screen.getByTestId("breadcrumb-current")).toHaveTextContent("Literature Validation");
  });

  it("shows an empty state when there are no studies", async () => {
    mockGet.mockResolvedValue([]);
    render(<ValidationStudiesListPage />);
    await waitFor(() => expect(screen.getByText(/no validation studies/i)).toBeInTheDocument());
  });

  it("surfaces a retry-able error, not the empty state, when the load fails", async () => {
    mockGet.mockRejectedValue(new Error("network down"));
    render(<ValidationStudiesListPage />);
    expect(await screen.findByTestId("error-state")).toBeInTheDocument();
    expect(screen.getByText(/validation studies could not be loaded/i)).toBeInTheDocument();
    expect(screen.getByTestId("error-retry")).toBeInTheDocument();
    expect(screen.queryByText(/no validation studies/i)).not.toBeInTheDocument();
  });

  it("shows the not-enabled notice, not studies, when the lit_validation flag is off", async () => {
    litBeta = { available: true, flags: {}, loading: false };
    mockGet.mockResolvedValue([
      { id: 7, state: "classified", classification: "validated", confidence: 100, created_at: "2026-07-06T00:00:00Z" },
    ]);
    render(<ValidationStudiesListPage />);
    expect(await screen.findByText(/isn't enabled/i)).toBeInTheDocument();
    expect(screen.queryByText("Fully Validated")).not.toBeInTheDocument();
  });
});
