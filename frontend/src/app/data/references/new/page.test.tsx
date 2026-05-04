import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import NewReferencePage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <nav data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <header data-testid="header" />,
}));

jest.mock("@/lib/api", () => ({
  api: {
    post: jest.fn(),
  },
}));

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ role_name: "comp_bio", sub: "1", org_id: "1" }),
}));

jest.mock("@/lib/resumableUpload", () => ({
  uploadFileResumable: jest.fn(async () => undefined),
  pickChunkSize: () => 8 * 1024 * 1024,
}));

import { api } from "@/lib/api";
import { uploadFileResumable } from "@/lib/resumableUpload";

const mockPost = api.post as jest.Mock;
const mockUpload = uploadFileResumable as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
  mockUpload.mockReset().mockResolvedValue(undefined);
});

function makeFile(name: string, bytes: number): File {
  return new File([new Uint8Array(bytes)], name, { type: "application/octet-stream" });
}

describe("NewReferencePage", () => {
  it("renders the upload form fields", () => {
    render(<NewReferencePage />);
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/version/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/category/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/scope/i)).toBeInTheDocument();
  });

  it("calls upload-init with the form values then PUTs each file then upload-complete", async () => {
    mockPost
      .mockResolvedValueOnce({
        reference_id: 42,
        gcs_prefix: "genome/grch38/v45/",
        uploads: [
          {
            filename: "genome.fa",
            session_url: "https://gcs.example/session/genome.fa",
            expires_at: "2026-05-10T00:00:00Z",
          },
          {
            filename: "genes.gtf",
            session_url: "https://gcs.example/session/genes.gtf",
            expires_at: "2026-05-10T00:00:00Z",
          },
        ],
      })
      .mockResolvedValueOnce({
        id: 42,
        organization_id: 1,
        name: "GRCh38",
        category: "genome",
        scope: "public",
        version: "v45",
        gcs_prefix: "genome/grch38/v45/",
        status: "pending_approval",
        files: [],
      });

    render(<NewReferencePage />);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "GRCh38" } });
    fireEvent.change(screen.getByLabelText(/version/i), { target: { value: "v45" } });
    fireEvent.change(screen.getByLabelText(/category/i), { target: { value: "genome" } });
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: "public" } });

    const fileInput = screen.getByLabelText(/files/i) as HTMLInputElement;
    const f1 = makeFile("genome.fa", 1024);
    const f2 = makeFile("genes.gtf", 2048);
    fireEvent.change(fileInput, { target: { files: [f1, f2] } });

    fireEvent.click(screen.getByRole("button", { name: /start upload/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalled());

    expect(mockPost.mock.calls[0][0]).toBe("/api/references/upload-init");
    const initBody = mockPost.mock.calls[0][1];
    expect(initBody.name).toBe("GRCh38");
    expect(initBody.version).toBe("v45");
    expect(initBody.category).toBe("genome");
    expect(initBody.scope).toBe("public");
    expect(initBody.files).toHaveLength(2);
    expect(initBody.files[0]).toMatchObject({ filename: "genome.fa", size_bytes: 1024 });

    await waitFor(() => expect(mockUpload).toHaveBeenCalledTimes(2));
    expect(mockUpload).toHaveBeenCalledWith(
      "https://gcs.example/session/genome.fa",
      f1,
      expect.any(Object),
    );

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/references/42/upload-complete"),
    );
  });

  it("calls /abort on init failure cleanup", async () => {
    mockPost
      .mockResolvedValueOnce({
        reference_id: 99,
        gcs_prefix: "genome/x/v1/",
        uploads: [
          {
            filename: "genome.fa",
            session_url: "https://gcs.example/session/genome.fa",
            expires_at: "2026-05-10T00:00:00Z",
          },
        ],
      })
      .mockResolvedValueOnce({}); // /abort

    mockUpload.mockRejectedValueOnce(new Error("network down"));

    render(<NewReferencePage />);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "X" } });
    fireEvent.change(screen.getByLabelText(/version/i), { target: { value: "v1" } });
    fireEvent.change(screen.getByLabelText(/category/i), { target: { value: "genome" } });
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: "internal" } });

    const fileInput = screen.getByLabelText(/files/i) as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [makeFile("genome.fa", 1024)] } });

    fireEvent.click(screen.getByRole("button", { name: /start upload/i }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/references/99/abort"),
    );
    expect(await screen.findByText(/network down|upload failed/i)).toBeInTheDocument();
  });
});
