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

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
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
    expect(target).toContain("/data/references/new?");
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
