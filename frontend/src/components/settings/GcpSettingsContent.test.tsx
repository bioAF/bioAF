import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";
import { GcpSettingsContent } from "./GcpSettingsContent";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPut = api.put as jest.Mock;
const mockPost = api.post as jest.Mock;

const STORED = {
  gcp_project_id: "real-project-42",
  gcp_region: "europe-west4",
  gcp_zone: "europe-west4-b",
  org_slug: "acme",
  gcp_credential_source: "vm_default",
  gcp_service_account_email: "svc@real-project-42.iam.gserviceaccount.com",
};

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
  mockPost.mockReset();
  mockPut.mockResolvedValue({});
  mockPost.mockResolvedValue({ checks: [], ok: true });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  (console.error as jest.Mock).mockRestore?.();
});

test("shows the stored configuration when the read succeeds", async () => {
  mockGet.mockResolvedValue(STORED);
  render(<GcpSettingsContent />);

  await waitFor(() => expect(screen.getByDisplayValue("real-project-42")).toBeInTheDocument());
  expect(screen.getByTestId("save-gcp-config-btn")).not.toBeDisabled();
});

// The form's initial state is `region = "us-central1"`, `zone = "us-central1-a"`.
// Those are hardcoded component defaults, not anything the org chose. The load had no
// .catch() at all, so a failed read left them on screen looking exactly like stored
// configuration, and Save & Validate PUTs `gcp_region`/`gcp_zone` unconditionally: one
// click wrote us-central1 over a real europe-west4 deployment.
describe("when the stored configuration cannot be read", () => {
  test("says so, instead of presenting its own defaults as the org's settings", async () => {
    mockGet.mockRejectedValue(new Error("boom"));
    render(<GcpSettingsContent />);

    expect(await screen.findByTestId("gcp-config-load-failed")).toHaveTextContent(
      /could not be loaded/i
    );
  });

  test("cannot be saved, because saving would overwrite what was not read", async () => {
    mockGet.mockRejectedValue(new Error("boom"));
    render(<GcpSettingsContent />);

    await screen.findByTestId("gcp-config-load-failed");
    expect(screen.getByTestId("save-gcp-config-btn")).toBeDisabled();

    fireEvent.click(screen.getByTestId("save-gcp-config-btn"));
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(mockPut).not.toHaveBeenCalled();
  });

  test("puts the real error in the logs", async () => {
    mockGet.mockRejectedValue(new Error("HTTP 500"));
    render(<GcpSettingsContent />);

    await screen.findByTestId("gcp-config-load-failed");
    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining("GCP configuration"),
      expect.any(Error)
    );
  });
});
