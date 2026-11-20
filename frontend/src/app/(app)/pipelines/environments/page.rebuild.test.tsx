/**
 * "Start rebuild" promises the image will be built in the background. The rebuild call only
 * creates a new draft build of the version, so the page must also start that draft's build;
 * before this it stopped at the draft and nothing was built.
 */
import { render, screen, fireEvent, waitFor } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  isAuthenticated: () => true,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));

import PipelineEnvironmentsPage from "./page";
import { api } from "@/lib/api";
const { toastMock } = jest.requireMock("@/components/shared/Toast");

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const READY = { id: 10, version_number: 1, build_number: 1, status: "ready", definition_format: "conda", created_at: "2026-01-01T00:00:00Z" };
const DRAFT = { id: 11, version_number: 1, build_number: 2, status: "draft", definition_format: "conda", created_at: "2026-01-02T00:00:00Z" };

function environment(versions: unknown[]) {
  return {
    id: 1, name: "Template 1", description: null, environment_type: "pipeline",
    latest_version: versions[0], created_by: null,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    versions,
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation(async (url: string) => {
    if (url === "/api/v1/environments?type=pipeline") return { environments: [environment([READY])] };
    if (url === "/api/v1/environments/1") return environment([READY]);
    if (url.endsWith("/logs")) return { build_id: null, status: "ready", logs_url: null };
    return environment([READY]);
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

async function confirmRebuild() {
  render(<PipelineEnvironmentsPage />);
  fireEvent.click(await screen.findByText("Template 1"));
  const rebuild = await screen.findAllByRole("button", { name: "Rebuild" });
  fireEvent.click(rebuild[0]);
  fireEvent.click(await screen.findByRole("button", { name: "Start rebuild" }));
}

test("Start rebuild creates the new build and starts building it", async () => {
  mockPost.mockImplementation(async (url: string) => {
    if (url === "/api/v1/environments/1/versions/10/rebuild") return DRAFT;
    if (url === "/api/v1/environments/1/versions/11/build") return { ...DRAFT, status: "building" };
    throw new Error(`unexpected ${url}`);
  });

  await confirmRebuild();

  await waitFor(() =>
    expect(mockPost.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/environments/1/versions/10/rebuild",
      "/api/v1/environments/1/versions/11/build",
    ]),
  );
});

test("when the new build cannot start, the user is told the draft was kept and can be built", async () => {
  mockPost.mockImplementation(async (url: string) => {
    if (url.endsWith("/rebuild")) return DRAFT;
    throw new Error("Cloud Build quota exceeded: internal detail");
  });

  await confirmRebuild();

  await waitFor(() =>
    expect(toastMock.error).toHaveBeenCalledWith(
      "The rebuild was created, but its build did not start. Use Build on the new draft to try again.",
    ),
  );
  expect(toastMock.error.mock.calls.flat().join(" ")).not.toMatch(/internal detail/);
  expect(console.error).toHaveBeenCalled();
});

test("when the rebuild itself fails, no build is attempted", async () => {
  mockPost.mockImplementation(async () => {
    throw new Error("Version not found");
  });

  await confirmRebuild();

  await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Version not found"));
  expect(mockPost).toHaveBeenCalledTimes(1);
});
