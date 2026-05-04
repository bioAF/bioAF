import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ImportReferencePage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <nav data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <header data-testid="header" />,
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ role_name: "comp_bio", sub: "1", org_id: "1" }),
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

describe("ImportReferencePage", () => {
  it("renders the import form fields", () => {
    render(<ImportReferencePage />);
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/version/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/source url/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/extract/i)).toBeInTheDocument();
  });

  it("submits to /api/references/import with form values", async () => {
    mockPost.mockResolvedValueOnce({
      reference_id: 7,
      import_job_id: "refimport-7-stub",
      status: "pending",
    });
    mockGet.mockResolvedValue({
      reference_id: 7,
      status: "downloading",
      progress_pct: 10,
      bytes_downloaded: 10,
      total_bytes: 100,
      error_message: null,
      import_job_id: "refimport-7-stub",
      updated_at: null,
    });

    render(<ImportReferencePage />);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "GENCODE" } });
    fireEvent.change(screen.getByLabelText(/version/i), { target: { value: "v45" } });
    fireEvent.change(screen.getByLabelText(/category/i), { target: { value: "annotation" } });
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: "internal" } });
    fireEvent.change(screen.getByLabelText(/source url/i), {
      target: { value: "https://ftp.example/file.gz" },
    });
    fireEvent.change(screen.getByLabelText(/extract/i), { target: { value: "gzip" } });

    fireEvent.click(screen.getByRole("button", { name: /start import/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][0]).toBe("/api/references/import");
    const body = mockPost.mock.calls[0][1];
    expect(body.name).toBe("GENCODE");
    expect(body.source_url).toBe("https://ftp.example/file.gz");
    expect(body.extract).toBe("gzip");
  });

  it("shows server error when import-init fails", async () => {
    mockPost.mockRejectedValueOnce(new Error("References bucket not configured"));

    render(<ImportReferencePage />);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "GENCODE" } });
    fireEvent.change(screen.getByLabelText(/version/i), { target: { value: "v45" } });
    fireEvent.change(screen.getByLabelText(/category/i), { target: { value: "annotation" } });
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: "internal" } });
    fireEvent.change(screen.getByLabelText(/source url/i), {
      target: { value: "https://ftp.example/file.gz" },
    });

    fireEvent.click(screen.getByRole("button", { name: /start import/i }));

    expect(await screen.findByText(/bucket not configured/i)).toBeInTheDocument();
  });
});
