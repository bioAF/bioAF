import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DataReferenceDetailPage from "./page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useParams: () => ({ id: "1" }),
}));

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <nav data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <header data-testid="header" />,
}));

const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "comp_bio", sub: "1", org_id: "1" }),
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const REF_DETAIL = {
  id: 1,
  organization_id: 1,
  name: "GRCh38 GENCODE",
  category: "genome",
  scope: "public",
  version: "v45",
  source_url: null,
  gcs_prefix: "genome/grch38-gencode/v45/",
  total_size_bytes: 100,
  file_count: 1,
  status: "active",
  deprecation_note: null,
  superseded_by_id: null,
  created_at: "2026-05-01T00:00:00Z",
  files: [],
  uploaded_by: { id: 1, name: "alice", email: "alice@test.com" },
  approved_by: null,
};

beforeEach(() => {
  mockPush.mockReset();
  mockGet.mockReset();
  mockPost.mockReset();
});

describe("Reference Detail — versioning UX", () => {
  it("Upload new version button navigates with locked name + category + scope", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.startsWith("/api/references/1")) return Promise.resolve(REF_DETAIL);
      return Promise.resolve({ references: [], total: 0 });
    });
    render(<DataReferenceDetailPage />);
    const btn = await screen.findByRole("button", { name: /upload new version/i });
    fireEvent.click(btn);
    expect(mockPush).toHaveBeenCalled();
    const target = mockPush.mock.calls[0][0] as string;
    expect(target).toContain("/data/references/add?");
    expect(target).toContain("mode=upload");
    expect(target).toContain("name=GRCh38+GENCODE");
    expect(target).toContain("category=genome");
    expect(target).toContain("scope=public");
  });

  it("Versions tab fetches /by-name and renders sibling versions", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.startsWith("/api/references/by-name")) {
        return Promise.resolve({
          total: 2,
          references: [
            REF_DETAIL,
            {
              ...REF_DETAIL,
              id: 2,
              version: "v44",
              status: "deprecated",
              deprecation_note: "old",
            },
          ],
        });
      }
      if (url.startsWith("/api/references/1")) return Promise.resolve(REF_DETAIL);
      return Promise.resolve({ references: [], total: 0 });
    });

    render(<DataReferenceDetailPage />);
    fireEvent.click(await screen.findByRole("button", { name: /versions/i }));

    await waitFor(() => {
      const calls = mockGet.mock.calls.map((c) => c[0]);
      expect(calls.some((u: string) => u.includes("/api/references/by-name"))).toBe(true);
    });

    // Wait for the version-list table to render the deprecated v44 row
    expect(await screen.findByText(/v44/)).toBeInTheDocument();
    // Versions tab marks the current row with a "current" badge
    expect(screen.getByText(/current/i)).toBeInTheDocument();
  });
});

describe("Reference Detail - in-flight URL import", () => {
  it("Shows progress and a Cancel import button when status is uploading", async () => {
    const inflight = {
      ...REF_DETAIL,
      status: "uploading",
    };
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/references/1") return Promise.resolve(inflight);
      if (url === "/api/references/1/import-status") {
        return Promise.resolve({
          reference_id: 1,
          status: "downloading",
          progress_pct: 37,
          bytes_downloaded: 370_000,
          total_bytes: 1_000_000,
          error_message: null,
          import_job_id: "refimport-1-inproc",
          updated_at: "2026-06-01T15:00:00Z",
        });
      }
      return Promise.resolve({ references: [], total: 0 });
    });

    render(<DataReferenceDetailPage />);

    // The in-flight banner shows progress text + a Cancel button.
    expect(await screen.findByText(/import in progress/i)).toBeInTheDocument();
    expect(await screen.findByText(/37%/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel import/i })).toBeInTheDocument();
  });

  it("Cancel import POSTs import-cancel and navigates back to the list", async () => {
    const inflight = {
      ...REF_DETAIL,
      status: "uploading",
    };
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/references/1") return Promise.resolve(inflight);
      if (url === "/api/references/1/import-status") {
        return Promise.resolve({
          reference_id: 1,
          status: "downloading",
          progress_pct: 10,
          bytes_downloaded: 100,
          total_bytes: 1000,
          error_message: null,
          import_job_id: null,
          updated_at: null,
        });
      }
      return Promise.resolve({ references: [], total: 0 });
    });
    mockPost.mockResolvedValue({});

    render(<DataReferenceDetailPage />);

    const cancelBtn = await screen.findByRole("button", { name: /cancel import/i });
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/api/references/1/import-cancel");
    });
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/data/references");
    });
  });

  it("Shows error message and a Delete button when status is failed", async () => {
    const failed = {
      ...REF_DETAIL,
      status: "failed",
      deprecation_note: "404 from upstream",
    };
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/references/1") return Promise.resolve(failed);
      return Promise.resolve({ references: [], total: 0 });
    });

    render(<DataReferenceDetailPage />);

    expect(await screen.findByText(/import failed/i)).toBeInTheDocument();
    expect(screen.getByText(/404 from upstream/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /delete/i })).toBeInTheDocument();
  });

  it("Shows a Finalize button when the importer reports done but the dataset is still uploading", async () => {
    const stuck = { ...REF_DETAIL, status: "uploading" };
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/references/1") return Promise.resolve(stuck);
      if (url === "/api/references/1/import-status") {
        return Promise.resolve({
          reference_id: 1,
          status: "finalizing",
          progress_pct: 100,
          bytes_downloaded: 10_660_000_000,
          total_bytes: 10_660_000_000,
          error_message: null,
          import_job_id: null,
          updated_at: null,
        });
      }
      return Promise.resolve({ references: [], total: 0 });
    });

    render(<DataReferenceDetailPage />);

    expect(await screen.findByRole("button", { name: /finalize/i })).toBeInTheDocument();
  });

  it("Finalize button POSTs recover-finalize and reloads the reference", async () => {
    const stuck = { ...REF_DETAIL, status: "uploading" };
    let finalizedCalled = false;
    const finalized = { ...REF_DETAIL, status: "active", file_count: 1, total_size_bytes: 10_660_000_000 };
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/references/1") {
        return Promise.resolve(finalizedCalled ? finalized : stuck);
      }
      if (url === "/api/references/1/import-status") {
        return Promise.resolve({
          reference_id: 1,
          status: "finalizing",
          progress_pct: 100,
          bytes_downloaded: 10_660_000_000,
          total_bytes: 10_660_000_000,
          error_message: null,
          import_job_id: null,
          updated_at: null,
        });
      }
      return Promise.resolve({ references: [], total: 0 });
    });
    mockPost.mockImplementation(async (url: string) => {
      if (url === "/api/references/1/recover-finalize") {
        finalizedCalled = true;
      }
      return {};
    });

    render(<DataReferenceDetailPage />);

    const finalizeBtn = await screen.findByRole("button", { name: /finalize/i });
    fireEvent.click(finalizeBtn);

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/api/references/1/recover-finalize");
    });
    // After the explicit reload triggered by Finalize, the dataset
    // transitions to 'active' and the in-flight banner is gone.
    await waitFor(() => {
      expect(screen.queryByText(/import in progress/i)).not.toBeInTheDocument();
    });
  });

  it("Delete on a failed dataset POSTs import-cancel and navigates back to the list", async () => {
    const failed = {
      ...REF_DETAIL,
      status: "failed",
      deprecation_note: "boom",
    };
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/references/1") return Promise.resolve(failed);
      return Promise.resolve({ references: [], total: 0 });
    });
    mockPost.mockResolvedValue({});

    render(<DataReferenceDetailPage />);
    const deleteBtn = await screen.findByRole("button", { name: /delete/i });
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/api/references/1/import-cancel");
    });
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/data/references");
    });
  });
});
