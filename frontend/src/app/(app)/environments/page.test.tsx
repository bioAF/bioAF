import { render, waitFor, screen, fireEvent } from "@/testing/renderWithProviders";
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

// The catch used to be `setRebuildTemplateContent(""); setRebuildTemplateMatch(false);`.
// `rebuildTemplateMatch === false` is the value that selects the GREEN branch of the
// modal: "The template has been updated since your last build. The new version will
// include the latest changes." So a failed template fetch reassured the user there was
// something to pick up, left the build button live, and POSTed definition_content: "".
// "Could not read it" and "it differs" are not the same answer.
describe("Workbench EnvironmentsPage rebuild when the template cannot be read", () => {
  const envSummary = {
    id: 1,
    name: "Default Notebook",
    description: null,
    visibility: "organization",
    environment_type: "notebook",
    version_count: 1,
    latest_version: null,
    created_by: null,
    created_at: "2026-05-07T10:00:00Z",
    updated_at: "2026-05-07T10:00:00Z",
  };
  const envDetail = {
    ...envSummary,
    versions: [
      {
        id: 10,
        version_number: 3,
        build_number: 1,
        status: "built",
        definition_format: "conda",
        image_uri: "img",
        created_at: "2026-05-07T10:00:00Z",
      },
    ],
  };

  function mountWith(templateResult: () => Promise<unknown>) {
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/v1/environments?type=notebook")
        return Promise.resolve({ environments: [envSummary], total: 1 });
      if (url === "/api/v1/environments?type=work_node")
        return Promise.resolve({ environments: [], total: 0 });
      if (url === "/api/v1/environments/1") return Promise.resolve(envDetail);
      if (url.includes("/template/")) return templateResult();
      if (url.includes("/versions/10")) return Promise.resolve({ definition_content: "FROM x" });
      return Promise.resolve({});
    });
  }

  async function openRebuild() {
    render(<EnvironmentsPage />);
    await waitFor(() => expect(screen.getByText("Default Notebook")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Default Notebook"));
    fireEvent.click(await screen.findByText("Rebuild from Latest Template"));
  }

  beforeEach(() => {
    jest.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    (console.error as jest.Mock).mockRestore?.();
  });

  test("does not claim the template has been updated", async () => {
    mountWith(() => Promise.reject(new Error("500")));
    await openRebuild();

    expect(await screen.findByTestId("rebuild-template-load-failed")).toBeInTheDocument();
    expect(screen.queryByText(/has been updated since your last build/i)).not.toBeInTheDocument();
  });

  test("cannot build an empty definition from a failed read", async () => {
    mountWith(() => Promise.reject(new Error("500")));
    await openRebuild();
    await screen.findByTestId("rebuild-template-load-failed");

    expect(screen.getByRole("button", { name: /build new version/i })).toBeDisabled();
  });

  test("puts the real error in the logs", async () => {
    mountWith(() => Promise.reject(new Error("500")));
    await openRebuild();
    await screen.findByTestId("rebuild-template-load-failed");

    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining("environment template"),
      expect.any(Error)
    );
  });

  test("a template that really did change still reads as changed, and builds", async () => {
    mountWith(() => Promise.resolve({ definition_content: "FROM y" }));
    await openRebuild();

    expect(await screen.findByText(/has been updated since your last build/i)).toBeInTheDocument();
    expect(screen.queryByTestId("rebuild-template-load-failed")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /build new version/i })).not.toBeDisabled();
  });
});
