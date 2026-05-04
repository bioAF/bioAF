import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ReferencePicker } from "./ReferencePicker";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

beforeEach(() => mockGet.mockReset());

describe("ReferencePicker", () => {
  it("queries /api/references with the right category and renders active rows", async () => {
    mockGet.mockResolvedValueOnce({
      total: 2,
      references: [
        {
          id: 1,
          organization_id: 1,
          name: "GRCh38",
          category: "genome",
          scope: "public",
          version: "v45",
          source_url: null,
          gcs_prefix: "genome/grch38/v45/",
          total_size_bytes: null,
          file_count: 1,
          status: "active",
          deprecation_note: null,
          superseded_by_id: null,
          created_at: "2026-05-01T00:00:00Z",
        },
        {
          id: 2,
          organization_id: 1,
          name: "GRCh38",
          category: "genome",
          scope: "public",
          version: "v44",
          source_url: null,
          gcs_prefix: "genome/grch38/v44/",
          total_size_bytes: null,
          file_count: 1,
          status: "deprecated",
          deprecation_note: "old",
          superseded_by_id: 1,
          created_at: "2026-04-01T00:00:00Z",
        },
      ],
    });

    const onChange = jest.fn();
    render(<ReferencePicker category="genome" value="" onChange={onChange} />);

    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/references?category=genome"));

    // active version visible by default
    expect(await screen.findByText(/GRCh38 \(v45\)/)).toBeInTheDocument();
    // deprecated version is hidden until the toggle is checked
    expect(screen.queryByText(/v44.*deprecated/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/include deprecated/i));
    expect(await screen.findByText(/v44.*deprecated/)).toBeInTheDocument();
  });

  it("emits the dataset path when a row is selected", async () => {
    mockGet.mockResolvedValueOnce({
      total: 1,
      references: [
        {
          id: 1,
          organization_id: 1,
          name: "GENCODE",
          category: "annotation",
          scope: "public",
          version: "v45",
          source_url: null,
          gcs_prefix: "annotation/gencode/v45/",
          total_size_bytes: null,
          file_count: 1,
          status: "active",
          deprecation_note: null,
          superseded_by_id: null,
          created_at: "2026-05-01T00:00:00Z",
        },
      ],
    });

    const onChange = jest.fn();
    render(<ReferencePicker category="annotation" value="" onChange={onChange} />);

    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    fireEvent.change(screen.getByRole("combobox", { name: "" }), {
      target: { value: "/data/references/annotation/gencode/v45/" },
    });
    expect(onChange).toHaveBeenCalledWith("/data/references/annotation/gencode/v45/");
  });

  it("requests all categories when category='any'", async () => {
    mockGet.mockResolvedValueOnce({ total: 0, references: [] });
    render(<ReferencePicker category="any" value="" onChange={() => {}} />);
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/references"));
  });
});
