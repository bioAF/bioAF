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
const mockUploadProxied = jest.fn();
const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    uploadSigned: (...args: unknown[]) => mockUploadSigned(...args),
    uploadProxied: (...args: unknown[]) => mockUploadProxied(...args),
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

const mockHas = jest.fn();
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({
    has: (flag: string) => mockHas(flag),
    capabilities: {},
    loading: false,
  }),
}));

const projectsResponse = {
  projects: [{ id: 1, name: "Alpha Project", status: "active" }],
  total: 1,
};

const experimentsResponse = {
  experiments: [
    { id: 10, name: "RNA-seq Batch A", status: "registered" },
    { id: 20, name: "CRISPR Screen", status: "processing" },
  ],
  total: 2,
  page: 1,
  page_size: 100,
};

beforeEach(() => {
  mockHas.mockReset();
  mockHas.mockReturnValue(true);
  mockUploadSigned.mockReset();
  mockUploadSigned.mockResolvedValue({ id: 1, filename: "sample.fastq.gz" });
  mockUploadProxied.mockReset();
  mockUploadProxied.mockResolvedValue({ id: 1, filename: "sample.fastq.gz" });
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path.startsWith("/api/projects")) return Promise.resolve(projectsResponse);
    if (path.startsWith("/api/experiments")) return Promise.resolve(experimentsResponse);
    return Promise.resolve([]);
  });
});

describe("DataUploadPage", () => {
  it("uploads via the proxied path when signed_url_upload is absent", async () => {
    mockHas.mockImplementation((flag: string) => flag !== "signed_url_upload");
    render(<DataUploadPage />);

    fireEvent.change(screen.getByRole("combobox", { name: /scope/i }), {
      target: { value: "global" },
    });
    const file = new File(["data"], "sample.fastq.gz", { type: "application/gzip" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => screen.getByText(/Upload 1 file/));
    fireEvent.click(screen.getByText(/Upload 1 file/));

    await waitFor(() => expect(mockUploadProxied).toHaveBeenCalledTimes(1));
    expect(mockUploadSigned).not.toHaveBeenCalled();
    const [, opts] = mockUploadProxied.mock.calls[0];
    expect(opts.isGlobal).toBe(true);
  });

  it("calls uploadSigned when uploading a file", async () => {
    render(<DataUploadPage />);

    fireEvent.change(screen.getByRole("combobox", { name: /scope/i }), {
      target: { value: "global" },
    });

    const file = new File(["data"], "sample.fastq.gz", { type: "application/gzip" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => screen.getByText(/Upload 1 file/));
    fireEvent.click(screen.getByText(/Upload 1 file/));

    await waitFor(() => expect(mockUploadSigned).toHaveBeenCalledTimes(1));
    const [calledFile, calledOptions] = mockUploadSigned.mock.calls[0];
    expect(calledFile).toBe(file);
    expect(calledOptions.experimentId).toBeUndefined();
    expect(calledOptions.isGlobal).toBe(true);
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

    await waitFor(() => {
      expect(screen.getByText("RNA-seq Batch A")).toBeInTheDocument();
    });
  });

  it("passes selected experiment id when uploading", async () => {
    render(<DataUploadPage />);

    await waitFor(() => screen.getByRole("combobox", { name: /experiment/i }));

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

  const withExperimentCode = (path: string) => {
    if (path.startsWith("/api/projects")) return Promise.resolve(projectsResponse);
    if (path.startsWith("/api/experiments"))
      return Promise.resolve({
        experiments: [{ id: 10, name: "RNA-seq Batch A", code: "EXP-A", status: "registered" }],
        total: 1,
        page: 1,
        page_size: 100,
      });
    return Promise.resolve([]);
  };

  it("keeps the original filename by default when a suggestion is not accepted", async () => {
    mockGet.mockImplementation(withExperimentCode);
    render(<DataUploadPage />);

    await waitFor(() => screen.getByRole("combobox", { name: /experiment/i }));
    fireEvent.change(screen.getByRole("combobox", { name: /experiment/i }), {
      target: { value: "10" },
    });

    const file = new File(["data"], "reads.fastq.gz", { type: "application/gzip" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    // A suggestion is offered, but the default is to keep the original name.
    await screen.findByText(/suggested name/i);
    fireEvent.click(screen.getByText(/Upload 1 file/));

    await waitFor(() => expect(mockUploadSigned).toHaveBeenCalledTimes(1));
    expect(mockUploadSigned.mock.calls[0][1].filename).toBeUndefined();
  });

  it("applies the suggested name to every file via Accept all", async () => {
    mockGet.mockImplementation(withExperimentCode);
    render(<DataUploadPage />);

    await waitFor(() => screen.getByRole("combobox", { name: /experiment/i }));
    fireEvent.change(screen.getByRole("combobox", { name: /experiment/i }), {
      target: { value: "10" },
    });

    const file = new File(["data"], "reads.fastq.gz", { type: "application/gzip" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(await screen.findByRole("button", { name: /accept all/i }));
    fireEvent.click(screen.getByText(/Upload 1 file/));

    await waitFor(() => expect(mockUploadSigned).toHaveBeenCalledTimes(1));
    expect(mockUploadSigned.mock.calls[0][1].filename).toMatch(/^EXP-A_.*\.fastq\.gz$/);
  });

  it("shows empty state when no experiments exist", async () => {
    mockGet.mockImplementation((path: string) => {
      if (path.startsWith("/api/projects")) return Promise.resolve({ projects: [], total: 0 });
      if (path.startsWith("/api/experiments"))
        return Promise.resolve({ experiments: [], total: 0, page: 1, page_size: 100 });
      return Promise.resolve([]);
    });
    render(<DataUploadPage />);

    await waitFor(() => {
      const select = screen.getByRole("combobox", { name: /experiment/i });
      expect(select).toBeInTheDocument();
    });

    expect(screen.queryByText("RNA-seq Batch A")).not.toBeInTheDocument();
  });
});
