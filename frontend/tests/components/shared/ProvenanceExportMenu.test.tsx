import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProvenanceExportMenu } from "@/components/shared/ProvenanceExportMenu";

jest.mock("@/lib/api", () => ({
  api: { download: jest.fn().mockResolvedValue(undefined) },
}));

import { api } from "@/lib/api";

const mockDownload = api.download as jest.Mock;

beforeEach(() => {
  mockDownload.mockReset();
  mockDownload.mockResolvedValue(undefined);
});

describe("ProvenanceExportMenu", () => {
  it("renders a custom label when provided", () => {
    render(<ProvenanceExportMenu entityType="validation-studies" entityId={7} label="Export Report" />);
    expect(screen.getByRole("button", { name: /export report/i })).toBeInTheDocument();
  });

  it("defaults to the provenance label", () => {
    render(<ProvenanceExportMenu entityType="experiments" entityId={1} />);
    expect(screen.getByRole("button", { name: /export provenance/i })).toBeInTheDocument();
  });

  it("downloads a validation-study report from the correct endpoint", async () => {
    render(<ProvenanceExportMenu entityType="validation-studies" entityId={7} label="Export Report" />);
    fireEvent.click(screen.getByRole("button", { name: /export report/i }));
    fireEvent.click(screen.getByText("PDF"));
    await waitFor(() =>
      expect(mockDownload).toHaveBeenCalledWith("/api/validation-studies/7/provenance/report?format=pdf"),
    );
  });
});
