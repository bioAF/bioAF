/**
 * Settings > System Settings (ADR-075): the installation metrics disclosure, the stored choice, whether
 * reporting is in effect, any operator override, and changing or withdrawing the choice.
 */
import { fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import SystemSettingsPage from "./page";

const mockGet = jest.fn();
const mockPut = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    put: (...args: unknown[]) => mockPut(...args),
  },
}));

function settings(overrides: Record<string, unknown> = {}) {
  return {
    install_metrics: {
      preference: "enabled",
      prompt_required: false,
      effective: true,
      operator_override: false,
      setup_completed: true,
      installation_id: "6d3f0a7e-0000-4000-8000-000000000001",
      notice_version: "2026-11-15",
      decided_at: "2026-11-20T15:30:00Z",
      revision: 2,
      ...overrides,
    },
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
  mockGet.mockResolvedValue(settings());
  mockPut.mockImplementation((_url: string, body: { choice: string }) =>
    Promise.resolve(
      settings({ preference: body.choice, effective: body.choice === "enabled", revision: 3 }),
    ),
  );
});

test("shows the stored choice, that reporting is on, and the installation ID", async () => {
  render(<SystemSettingsPage />);

  expect(screen.getByRole("heading", { level: 1, name: "System Settings" })).toBeInTheDocument();
  expect(await screen.findByRole("radio", { name: "Share installation metrics" })).toBeChecked();
  expect(screen.getByText("Reporting is on.")).toBeInTheDocument();
  expect(screen.getByText("6d3f0a7e-0000-4000-8000-000000000001")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

test("withdrawing saves Do not share against the stored revision", async () => {
  render(<SystemSettingsPage />);
  await screen.findByText("Reporting is on.");

  fireEvent.click(screen.getByRole("radio", { name: "Do not share" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("Reporting is off.")).toBeInTheDocument();
  expect(mockPut).toHaveBeenCalledWith("/api/system-settings/install-metrics", {
    choice: "disabled",
    notice_version: "2026-11-15",
    revision: 2,
  });
  expect(screen.getByText(/Pending reports were cancelled/)).toBeInTheDocument();
});

test("the operator override is shown and explains reporting is off whatever is chosen", async () => {
  mockGet.mockResolvedValue(settings({ operator_override: true, effective: false }));
  render(<SystemSettingsPage />);

  expect(await screen.findByText(/BIOAF_INSTALL_METRICS_DISABLED/)).toBeInTheDocument();
  expect(screen.getByText("Reporting is off.")).toBeInTheDocument();
});

test("a pending installation shows neither choice selected", async () => {
  mockGet.mockResolvedValue(
    settings({ preference: "pending", prompt_required: true, effective: false, installation_id: null, decided_at: null }),
  );
  render(<SystemSettingsPage />);

  await screen.findByText("No choice has been made yet.");
  expect(screen.getByRole("radio", { name: "Share installation metrics" })).not.toBeChecked();
  expect(screen.getByRole("radio", { name: "Do not share" })).not.toBeChecked();
});

test("a stale revision reloads the current choice and says another admin changed it", async () => {
  mockPut.mockRejectedValueOnce(Object.assign(new Error("revision_conflict"), { status: 409 }));
  mockGet.mockResolvedValueOnce(settings()).mockResolvedValueOnce(
    settings({ preference: "disabled", effective: false, revision: 3 }),
  );
  render(<SystemSettingsPage />);
  await screen.findByText("Reporting is on.");

  fireEvent.click(screen.getByRole("radio", { name: "Do not share" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText(/changed by another administrator/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("Reporting is off.")).toBeInTheDocument());
});

test("a failed load says so without offering a save", async () => {
  mockGet.mockRejectedValue(new Error("down"));
  render(<SystemSettingsPage />);

  expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
});
