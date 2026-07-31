import { render, waitFor, screen, fireEvent } from "@testing-library/react";
import EnvironmentsPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/environments",
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    delete: jest.fn(),
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

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockResolvedValue({ environments: [], total: 0 });
});

describe("Workbench EnvironmentsPage filter", () => {
  test("'All' filter excludes pipeline envs (does not call unfiltered list)", async () => {
    render(<EnvironmentsPage />);

    // Initial load should request notebook + work_node, never the bare
    // /api/v1/environments which would include pipeline envs.
    await waitFor(() => {
      expect(mockGet).toHaveBeenCalled();
    });

    expect(mockGet).not.toHaveBeenCalledWith("/api/v1/environments");
    expect(mockGet).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/v1\/environments\?type=notebook$/)
    );
    expect(mockGet).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/v1\/environments\?type=work_node$/)
    );
  });
});

describe("Workbench EnvironmentsPage build confirmation", () => {
  const envSummary = {
    id: 1,
    name: "Default Notebook",
    description: null,
    visibility: "organization",
    environment_type: "notebook",
    version_count: 1,
    latest_version: {
      id: 10,
      version_number: 1,
      build_number: 1,
      status: "draft",
      definition_format: "conda",
      image_uri: null,
      created_at: "2026-05-07T10:00:00Z",
    },
    created_by: null,
    created_at: "2026-05-07T10:00:00Z",
    updated_at: "2026-05-07T10:00:00Z",
  };

  const envDetail = {
    ...envSummary,
    versions: [
      {
        id: 10,
        version_number: 1,
        build_number: 1,
        status: "draft",
        definition_format: "conda",
        image_uri: null,
        created_at: "2026-05-07T10:00:00Z",
      },
    ],
  };

  beforeEach(() => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/v1/environments?type=notebook") {
        return Promise.resolve({ environments: [envSummary], total: 1 });
      }
      if (url === "/api/v1/environments?type=work_node") {
        return Promise.resolve({ environments: [], total: 0 });
      }
      if (url === "/api/v1/environments/1") return Promise.resolve(envDetail);
      return Promise.resolve({});
    });
  });

  test("clicking Build Image opens a friendly confirmation modal (no native confirm, no Cloud Build jargon)", async () => {
    const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(true);

    render(<EnvironmentsPage />);

    // Open the env so the version detail / Build button is visible.
    await waitFor(() => {
      expect(screen.getByText("Default Notebook")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Default Notebook"));

    const buildButton = await screen.findByText("Build");
    fireEvent.click(buildButton);

    // Modal text should explain background build + that the user can keep
    // working, and must NOT mention "Cloud Build" or use the native dialog.
    expect(await screen.findByText(/in the background/i)).toBeInTheDocument();
    expect(screen.getByText(/continue using bioaf/i)).toBeInTheDocument();
    expect(screen.queryByText(/Cloud Build/)).not.toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
  });
});
