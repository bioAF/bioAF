import { render, screen, waitFor } from "@testing-library/react";
import ValidationStudyPage from "@/app/validation-studies/[id]/page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
  useParams: () => ({ id: "5" }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), download: jest.fn() },
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

// Keep the test focused on the page body, not the app chrome.
jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => null }));
jest.mock("@/components/layout/Header", () => ({ Header: () => null }));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

describe("ValidationStudyPage", () => {
  it("renders the validation badge for a classified, validated study", async () => {
    mockGet.mockResolvedValue({
      id: 5,
      state: "classified",
      classification: "validated",
      confidence: 100,
      source_doi: "10.3390/jfb17020057",
    });

    render(<ValidationStudyPage />);

    await waitFor(() => expect(screen.getByText("Fully Validated")).toBeInTheDocument());
    expect(screen.getByText(/10\.3390\/jfb17020057/)).toBeInTheDocument();
    expect(screen.queryByText("Could Not Reproduce")).not.toBeInTheDocument();
  });

  it("renders the pipeline stage (not a validation verdict) while the study is still running", async () => {
    mockGet.mockResolvedValue({ id: 5, state: "running", confidence: null });

    render(<ValidationStudyPage />);

    await waitFor(() => expect(screen.getByText("Running analysis")).toBeInTheDocument());
    expect(screen.getByText("Step 7 of 9")).toBeInTheDocument();
    expect(screen.queryByText("Could Not Reproduce")).not.toBeInTheDocument();
  });

  it("shows the computed-vs-claimed evidence at the comparing gate", async () => {
    mockGet.mockResolvedValue({
      id: 5,
      state: "comparing",
      confidence: null,
      evidence: {
        comparison_targets: [{ metric_key: "total_sequences", claimed_value: 7000000, unit: "reads" }],
        computed_metrics: { total_sequences: 6600000 },
      },
    });

    render(<ValidationStudyPage />);

    await waitFor(() => expect(screen.getByText("total_sequences")).toBeInTheDocument());
    expect(screen.getByText("6600000")).toBeInTheDocument();
  });

  it("exposes the approve/decline gate on the detail page at plan_ready", async () => {
    mockGet.mockResolvedValue({
      id: 5,
      state: "plan_ready",
      confidence: null,
      plan: { pipeline_key: "nf-core/rnaseq", accessions: ["GSE1"], reference_genome: "GRCh38" },
    });

    render(<ValidationStudyPage />);

    await waitFor(() => expect(screen.getByRole("button", { name: /approve plan/i })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /decline/i })).toBeInTheDocument();
  });

  it("offers an Export Report control once the study has been read (F3)", async () => {
    mockGet.mockResolvedValue({
      id: 5,
      state: "classified",
      classification: "validated",
      confidence: 100,
      source_doi: "10.3390/jfb17020057",
    });

    render(<ValidationStudyPage />);

    await waitFor(() => expect(screen.getByRole("button", { name: /export report/i })).toBeInTheDocument());
  });

  it("hides the Export Report control before the paper has been read", async () => {
    mockGet.mockResolvedValue({ id: 5, state: "requested", confidence: null });

    render(<ValidationStudyPage />);

    await waitFor(() => expect(screen.getByText(/Validation Study #5/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /export report/i })).not.toBeInTheDocument();
  });

  it("shows a not-found message when the study cannot be loaded", async () => {
    mockGet.mockRejectedValue(new Error("404"));

    render(<ValidationStudyPage />);

    await waitFor(() => expect(screen.getByText(/not found/i)).toBeInTheDocument());
  });
});
