/**
 * Settings > Workbench Settings: the work node Idle Timeout no longer stops silent nodes (owner
 * decision 2026-11-25), so its help must not promise an automatic stop.
 */
import { render, screen } from "@/testing/renderWithProviders";
import WorkbenchSettingsPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), put: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockImplementation((url: string) =>
    Promise.resolve(
      url === "/api/v1/settings/work-nodes"
        ? { max_nodes_per_user: 2, idle_timeout_hours: 24, boot_disk_gb: 100, boot_disk_type: "pd-ssd" }
        : { idle_timeout_hours: 4, idle_warning_minutes: 15, max_sessions_per_user: 2 },
    ),
  );
});

it("says the work node idle timeout is paused instead of promising an automatic stop", async () => {
  render(<WorkbenchSettingsPage />);
  expect(
    await screen.findByText(
      "Paused: bioAF does not stop work nodes automatically until they can report activity (1-720).",
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Auto-stop nodes with no heartbeat/)).not.toBeInTheDocument();
});
