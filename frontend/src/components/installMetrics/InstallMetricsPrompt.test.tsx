/**
 * The installation metrics prompt on authenticated app entry (ADR-075). It appears only for an admin who
 * can configure infrastructure, only while the installation's choice is pending. Closing leaves the
 * choice pending; a saved choice resolves it for every admin. A failed read never blocks the app.
 */
import { fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import { InstallMetricsPrompt } from "./InstallMetricsPrompt";

const mockGet = jest.fn();
const mockPut = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    put: (...args: unknown[]) => mockPut(...args),
  },
}));
let canConfigure = true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    loading: false,
    canAccess: (resource: string, action: string) =>
      canConfigure && resource === "infrastructure" && action === "configure",
  }),
}));

function settings(overrides: Record<string, unknown> = {}) {
  return {
    install_metrics: {
      preference: "pending",
      prompt_required: true,
      effective: false,
      operator_override: false,
      setup_completed: true,
      installation_id: null,
      notice_version: "2026-11-15",
      decided_at: null,
      revision: 4,
      ...overrides,
    },
  };
}

beforeEach(() => {
  canConfigure = true;
  mockGet.mockReset();
  mockPut.mockReset();
  mockGet.mockResolvedValue(settings());
  mockPut.mockImplementation((_url: string, body: { choice: string }) =>
    Promise.resolve(settings({ preference: body.choice, prompt_required: false, revision: 5 })),
  );
});

test("a pending installation asks an admin with three direct actions", async () => {
  render(<InstallMetricsPrompt />);

  expect(await screen.findByRole("dialog", { name: "Help improve bioAF" })).toBeInTheDocument();
  expect(screen.getByText(/Nothing is sent unless you agree/)).toBeInTheDocument();
  expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  for (const name of ["Share", "Don't share", "Decide later"]) {
    expect(screen.getByRole("button", { name })).toBeEnabled();
  }
});

test.each([
  ["Share", "enabled"],
  ["Don't share", "disabled"],
])("%s saves the choice against the revision read and closes", async (label, choice) => {
  render(<InstallMetricsPrompt />);
  await screen.findByRole("dialog", { name: "Help improve bioAF" });

  fireEvent.click(screen.getByRole("button", { name: label }));

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(mockPut).toHaveBeenCalledWith("/api/system-settings/install-metrics", {
    choice,
    notice_version: "2026-11-15",
    revision: 4,
  });
});

test("closing leaves the choice pending and saves nothing", async () => {
  render(<InstallMetricsPrompt />);
  await screen.findByRole("dialog", { name: "Help improve bioAF" });

  fireEvent.click(screen.getByRole("button", { name: "Decide later" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(mockPut).not.toHaveBeenCalled();
});

test("a failed save stays open, says so, and can be retried", async () => {
  mockPut.mockRejectedValueOnce(Object.assign(new Error("boom"), { status: 500 }));
  render(<InstallMetricsPrompt />);
  await screen.findByRole("dialog", { name: "Help improve bioAF" });

  fireEvent.click(screen.getByRole("button", { name: "Don't share" }));

  expect(await screen.findByText(/was not saved/)).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Don't share" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("a choice another admin saved first is respected and the prompt closes", async () => {
  mockPut.mockRejectedValueOnce(Object.assign(new Error("revision_conflict"), { status: 409 }));
  mockGet.mockResolvedValueOnce(settings()).mockResolvedValueOnce(
    settings({ preference: "disabled", prompt_required: false, revision: 5 }),
  );
  render(<InstallMetricsPrompt />);
  await screen.findByRole("dialog", { name: "Help improve bioAF" });

  fireEvent.click(screen.getByRole("button", { name: "Share" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(mockPut).toHaveBeenCalledTimes(1);
});

test("a committed choice never prompts", async () => {
  mockGet.mockResolvedValue(settings({ preference: "disabled", prompt_required: false }));
  render(<InstallMetricsPrompt />);

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("someone who cannot configure the installation is never asked or read for", async () => {
  canConfigure = false;
  render(<InstallMetricsPrompt />);

  await new Promise((r) => setTimeout(r, 0));
  expect(mockGet).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a failed read does not block the app or show a prompt", async () => {
  mockGet.mockRejectedValue(new Error("down"));
  render(<InstallMetricsPrompt />);

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
