import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DataUploadPage from "@/app/data/upload/page";

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <div data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <div data-testid="header" />,
}));
jest.mock("@/lib/auth", () => ({
  getToken: () => "test-token",
  removeToken: jest.fn(),
}));

const mockUploadSigned = jest.fn();
const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    uploadSigned: (...args: unknown[]) => mockUploadSigned(...args),
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

beforeEach(() => {
  mockUploadSigned.mockReset();
  mockUploadSigned.mockResolvedValue({ id: 1, filename: "sample.fastq.gz" });
  mockGet.mockReset();
  mockGet.mockResolvedValue({
    experiments: [
      { id: 10, name: "RNA-seq Batch A", status: "registered" },
      { id: 20, name: "CRISPR Screen", status: "processing" },
    ],
    total: 2,
    page: 1,
    page_size: 100,
  });
});

describe("DataUploadPage", () => {
  it("calls uploadSigned when uploading a file", async () => {
    render(<DataUploadPage />);

    const file = new File(["data"], "sample.fastq.gz", { type: "application/gzip" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => screen.getByText(/Upload 1 file/));
    fireEvent.click(screen.getByText(/Upload 1 file/));

    await waitFor(() => expect(mockUploadSigned).toHaveBeenCalledTimes(1));
    const [calledFile, calledOptions] = mockUploadSigned.mock.calls[0];
    expect(calledFile).toBe(file);
    expect(calledOptions.experimentId).toBeUndefined();
  });

  it("fetches experiments and displays them in a dropdown", async () => {
    render(<DataUploadPage />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.stringContaining("/api/experiments"),
      );
    });

    const select = screen.getByRole("combobox", { name: /experiment/i });
    expect(select).toBeInTheDocument();

    // Options should include experiments by name
    await waitFor(() => {
      expect(screen.getByText("RNA-seq Batch A")).toBeInTheDocument();
    });
  });

  it("passes selected experiment id when uploading", async () => {
    render(<DataUploadPage />);

    // Wait for experiments to load
    await waitFor(() => screen.getByRole("combobox", { name: /experiment/i }));

    // Select an experiment from the dropdown
    fireEvent.change(screen.getByRole("combobox", { name: /experiment/i }), {
      target: { value: "10" },
    });

    const file = new File(["data"], "sample.fastq.gz", { type: "application/gzip" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => screen.getByText(/Upload 1 file/));
    fireEvent.click(screen.getByText(/Upload 1 file/));

    await waitFor(() => expect(mockUploadSigned).toHaveBeenCalledTimes(1));
    const [, calledOptions] = mockUploadSigned.mock.calls[0];
    expect(calledOptions.experimentId).toBe(10);
  });

  it("shows empty state when no experiments exist", async () => {
    mockGet.mockResolvedValue({ experiments: [], total: 0, page: 1, page_size: 100 });
    render(<DataUploadPage />);

    await waitFor(() => {
      const select = screen.getByRole("combobox", { name: /experiment/i });
      expect(select).toBeInTheDocument();
    });

    // Should have a placeholder option but no experiment options
    expect(screen.queryByText("RNA-seq Batch A")).not.toBeInTheDocument();
  });
});
