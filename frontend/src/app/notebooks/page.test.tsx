import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import NotebooksPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/notebooks",
}));

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin", email: "admin@test.com" }),
  isAuthenticated: () => true,
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

const mockEnvironments = {
  environments: [
    { id: 1, name: "Default scRNA-seq", description: null, version_count: 1, latest_version: 1, visibility: "team", created_at: "2026-03-12T10:00:00Z" },
  ],
  total: 1,
};

const mockEnvDetail = {
  id: 1,
  name: "Default scRNA-seq",
  description: null,
  visibility: "team",
  created_by: { id: 1, name: "Admin", email: "admin@test.com" },
  versions: [
    { id: 1, version_number: 1, status: "ready", definition_format: "dockerfile", image_uri: "us-central1-docker.pkg.dev/proj/bioaf-images/default-scrna:1", created_at: "2026-03-12T10:00:00Z" },
  ],
  created_at: "2026-03-12T10:00:00Z",
  updated_at: "2026-03-12T10:00:00Z",
};

function setupMocks(buildStatus: {
  build_id: string | null;
  build_status: string | null;
  image_uri: string | null;
}) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("sessions")) return Promise.resolve({ sessions: [] });
    if (url.includes("experiments")) return Promise.resolve({ experiments: [] });
    if (url.includes("projects")) return Promise.resolve({ projects: [] });
    if (url.includes("build-status")) return Promise.resolve(buildStatus);
    if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
    if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
    return Promise.resolve({});
  });
}

describe("NotebooksPage build status", () => {
  test("shows building banner when image build is in progress", async () => {
    setupMocks({
      build_id: "abc-123",
      build_status: "WORKING",
      image_uri: null,
    });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Notebook image is building")).toBeInTheDocument();
    });
    expect(screen.getByText(/abc-123/)).toBeInTheDocument();
  });

  test("shows failure banner when last build failed", async () => {
    setupMocks({
      build_id: "fail-456",
      build_status: "FAILURE",
      image_uri: null,
    });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Notebook image build failed")).toBeInTheDocument();
    });
  });

  test("shows launch button when build succeeded", async () => {
    setupMocks({
      build_id: "ok-789",
      build_status: "SUCCESS",
      image_uri: "us-central1-docker.pkg.dev/proj/bioaf-images/bioaf-scrna:latest",
    });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });
    expect(screen.queryByText("Notebook image is building")).not.toBeInTheDocument();
    expect(screen.queryByText("Notebook image build failed")).not.toBeInTheDocument();
  });

  test("shows launch button when no build exists", async () => {
    setupMocks({
      build_id: null,
      build_status: null,
      image_uri: null,
    });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });
    expect(screen.queryByText("Notebook image is building")).not.toBeInTheDocument();
    expect(screen.queryByText("Notebook image build failed")).not.toBeInTheDocument();
  });
});

describe("NotebooksPage launch modal", () => {
  test("shows launch modal with options when button clicked", async () => {
    setupMocks({ build_id: null, build_status: null, image_uri: null });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch Session"));

    await waitFor(() => {
      expect(screen.getByText("Launch Notebook Session")).toBeInTheDocument();
      expect(screen.getByText("Launch RStudio")).toBeInTheDocument();
      expect(screen.getByText("Launch Jupyter")).toBeInTheDocument();
    });
  });

  test("shows error in modal on launch failure", async () => {
    setupMocks({ build_id: null, build_status: null, image_uri: null });
    mockPost.mockRejectedValue(new Error("The notebook image is currently building."));

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch Session"));

    await waitFor(() => {
      expect(screen.getByText("Launch RStudio")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch RStudio"));

    await waitFor(() => {
      expect(screen.getByText("The notebook image is currently building.")).toBeInTheDocument();
    });
  });
});
