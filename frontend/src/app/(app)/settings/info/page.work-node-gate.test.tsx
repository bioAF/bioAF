/**
 * Plan 03A first-update gate: an update deferred because older work nodes may still need the running
 * backend says so plainly and offers View work nodes and Check again. It never offers to stop nodes.
 */
import { render, screen, act, fireEvent } from "@testing-library/react";

jest.mock("next/navigation", () => ({
  usePathname: () => "/settings/info",
  useRouter: () => ({ push: jest.fn() }),
}));
const grants = { deploy: true, viewNodes: true };
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (resource: string, action: string) =>
      resource === "work_nodes" ? grants.viewNodes && action === "view" : grants.deploy,
  }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import SettingsInfoPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const check = { current_version: "2026.11.45", latest_version: "2026.12.1", update_available: true, changelog: null, release_url: null };
const OLDER =
  "Some older work nodes are still running. Finish saving your work and stop them when convenient, then retry the update. bioAF will leave them running.";
const UNCHECKED = "bioAF could not check whether this update is safe. Your current installation is still running. Try again shortly.";

async function flush() {
  await act(async () => {
    for (let n = 0; n < 20; n++) await Promise.resolve();
  });
}

function serve(status: Record<string, unknown>) {
  let first = true;
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/upgrades/check") return Promise.resolve(check);
    if (url === "/api/upgrades/history") return Promise.resolve({ upgrades: [] });
    if (url === "/api/upgrades/status") {
      if (first) {
        first = false;
        return Promise.resolve({ status: "idle" });
      }
      return Promise.resolve(status);
    }
    return Promise.resolve({});
  });
}

async function installAndObserve() {
  render(<SettingsInfoPage />);
  await flush();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Install Update" }));
  });
  await flush();
  await act(async () => {
    jest.advanceTimersByTime(3000);
  });
  await flush();
}

beforeEach(() => {
  jest.useFakeTimers();
  grants.deploy = true;
  grants.viewNodes = true;
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockResolvedValue({});
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("a deferral for older work nodes is plain and offers View work nodes and Check again", async () => {
  serve({ status: "failed", step: "work_node_readiness", to_version: "2026.12.1", error: OLDER });
  await installAndObserve();

  expect(screen.getByText(OLDER)).toBeInTheDocument();
  expect(screen.queryByText(/Update failed/)).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View work nodes" })).toHaveAttribute("href", "/workbench/work-nodes");
  expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();

  mockPost.mockClear();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  });
  expect(mockPost).toHaveBeenCalledWith("/api/upgrades/execute", { target_version: "2026.12.1" });
});

test("an unchecked update says the installation is still running and offers Check again", async () => {
  serve({ status: "failed", step: "work_node_readiness", to_version: "2026.12.1", error: UNCHECKED });
  await installAndObserve();
  expect(screen.getByText(UNCHECKED)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Check again" })).toBeInTheDocument();
});

test("the actions follow the caller's permissions", async () => {
  grants.viewNodes = false;
  serve({ status: "failed", step: "work_node_readiness", to_version: "2026.12.1", error: OLDER });
  await installAndObserve();
  expect(screen.queryByRole("link", { name: "View work nodes" })).not.toBeInTheDocument();
});

test("the gate's steps have plain progress labels", async () => {
  serve({ status: "in_progress", step: "work_node_readiness", to_version: "2026.12.1" });
  await installAndObserve();
  expect(screen.getByText("Checking running work nodes...")).toBeInTheDocument();
});
