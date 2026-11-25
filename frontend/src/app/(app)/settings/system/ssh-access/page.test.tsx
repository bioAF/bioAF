/**
 * Settings > System Settings > Security and SSH access (plan 03B).
 *
 * Work Node ranges (login rule, future launches), the application-host SSH filter, protected cloud
 * recovery access with honest evidence labels, and the Restore SSH access console link. Each
 * section appears only for callers whose permissions the API returned it for.
 */
import { fireEvent, render, screen, waitFor, within } from "@/testing/renderWithProviders";
import SshAccessPage from "./page";

const mockGet = jest.fn();
const mockPut = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...a: unknown[]) => mockGet(...a),
    put: (...a: unknown[]) => mockPut(...a),
    post: (...a: unknown[]) => mockPost(...a),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

const workNodes = {
  draft: { version: 2, enabled: true, ranges: [{ id: "a", name: "Lab VPN", cidr: "10.20.0.0/16" }] },
  applied: { revision: 1, enabled: true, ranges: [{ id: "a", name: "Lab VPN", cidr: "10.20.0.0/16" }] },
  applied_at: "2026-11-24T10:00:00Z",
  can_roll_back: false,
};
const host = {
  filter: {
    draft: { version: 0, enabled: false, ranges: [] },
    applied: { revision: 0, enabled: false, ranges: [], applied_at: null },
    can_roll_back: false,
    scope: "SSH only. Web and API access are not affected.",
  },
  recovery: { status: "configured_automatic", problems: [], sources: [{ kind: "gcp_iap", source: "35.235.240.0/20", priority: 850, ports: "tcp:22" }], checked_at: "2026-11-24T10:00:00Z" },
  recovery_caption: "Cloud recovery access is always allowed. Your cloud-account permissions are still required.",
  restore: {
    url: "https://console.cloud.google.com/compute/instancesDetail/zones/us-central1-b/instances/bioaf?project=lab",
    instruction: "Open Cloud Shell on this server's page, paste the recovery command below, and press Enter.",
    command: "gcloud compute ssh bioaf --project=lab --zone=us-central1-b --tunnel-through-iap",
    server: { provider: "gcp", account: "lab", zone: "us-central1-b", instance: "bioaf", instance_id: "4815" },
    os_username: null,
    message: null,
  },
};

function serve(body: Record<string, unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/v1/settings/ssh-access") return Promise.resolve(body);
    if (url === "/api/v1/settings/ssh-access/observed-address") return Promise.resolve({ address: "203.0.113.7/32", message: null });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
  mockPost.mockReset();
});

test("the heading matches the navigation label", async () => {
  serve({ work_nodes: workNodes });
  render(<SshAccessPage />);
  expect(await screen.findByRole("heading", { level: 1, name: "Security and SSH access" })).toBeInTheDocument();
});

test("work node ranges explain the login rule and apply to future launches", async () => {
  serve({ work_nodes: workNodes });
  render(<SshAccessPage />);
  const section = await screen.findByRole("region", { name: "Work node SSH login" });
  expect(within(section).getByText(/inside a range.*password or key.*elsewhere.*key only/i)).toBeInTheDocument();
  expect(within(section).getByText(/work nodes launched after/i)).toBeInTheDocument();
  expect(within(section).getByDisplayValue("10.20.0.0/16")).toBeInTheDocument();
  expect(within(section).getByText(/Applied revision 1/)).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Application server SSH" })).not.toBeInTheDocument();
});

test("Save changes sends the draft with its version; Apply changes applies that version", async () => {
  serve({ work_nodes: workNodes });
  mockPut.mockResolvedValue({ draft: { ...workNodes.draft, version: 3 } });
  mockPost.mockResolvedValue({ applied: { revision: 2 } });
  render(<SshAccessPage />);
  const section = await screen.findByRole("region", { name: "Work node SSH login" });
  fireEvent.click(within(section).getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(mockPut).toHaveBeenCalledWith("/api/v1/settings/ssh-access/work-nodes/draft", {
      enabled: true,
      ranges: [{ id: "a", name: "Lab VPN", cidr: "10.20.0.0/16" }],
      base_version: 2,
    }),
  );
  fireEvent.click(within(section).getByRole("button", { name: "Apply changes" }));
  await waitFor(() =>
    // The draft just saved (version 3) is what gets applied.
    expect(mockPost).toHaveBeenCalledWith("/api/v1/settings/ssh-access/work-nodes/apply", { draft_version: 3 }),
  );
});

test("Use this computer's address suggests the observed address in the draft only", async () => {
  serve({ work_nodes: { ...workNodes, draft: { version: 0, enabled: false, ranges: [] } } });
  render(<SshAccessPage />);
  const section = await screen.findByRole("region", { name: "Work node SSH login" });
  fireEvent.click(within(section).getByRole("button", { name: "Use this computer's address" }));
  expect(await within(section).findByDisplayValue("203.0.113.7/32")).toBeInTheDocument();
  expect(within(section).getByText(/This suggests the address bioAF can see for this computer/)).toBeInTheDocument();
  expect(mockPut).not.toHaveBeenCalled();
  expect(mockPost).not.toHaveBeenCalled();
});

test("cloud recovery shows protected sources, the caption and the honest evidence label", async () => {
  serve({ application_host: host });
  render(<SshAccessPage />);
  const section = await screen.findByRole("region", { name: "Application server SSH" });
  expect(within(section).getByText("Cloud recovery access is always allowed. Your cloud-account permissions are still required.")).toBeInTheDocument();
  expect(within(section).getByText(/35\.235\.240\.0\/20/)).toBeInTheDocument();
  expect(within(section).getByText("Configured (automatic checks)")).toBeInTheDocument();
  expect(within(section).queryByText("Login tested")).not.toBeInTheDocument();
  expect(within(section).getByText("SSH only. Web and API access are not affected.")).toBeInTheDocument();
});

test("recovery that needs attention lists the reasons", async () => {
  serve({ application_host: { ...host, recovery: { status: "needs_attention", problems: ["The Google guest environment is not running on the server, so browser SSH cannot add its key."], sources: [], checked_at: null } } });
  render(<SshAccessPage />);
  const section = await screen.findByRole("region", { name: "Application server SSH" });
  expect(within(section).getByText("Needs attention")).toBeInTheDocument();
  expect(within(section).getByText(/guest environment is not running/)).toBeInTheDocument();
});

test("Restore SSH access opens the exact console page in a new tab with the Cloud Shell command", async () => {
  serve({ application_host: host });
  render(<SshAccessPage />);
  const link = await screen.findByRole("link", { name: "Restore SSH access" });
  expect(link).toHaveAttribute("href", host.restore.url);
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  expect(screen.getByText(host.restore.instruction)).toBeInTheDocument();
  expect(screen.getByText(host.restore.command)).toBeInTheDocument();
  expect(screen.getByText(/bioaf.*us-central1-b.*lab/)).toBeInTheDocument();
});

test("Check access on the server shows the result", async () => {
  serve({ application_host: host });
  mockPost.mockResolvedValue({ recovery: { ...host.recovery, status: "login_tested", login_tested_at: "2026-11-24T09:00:00Z" } });
  render(<SshAccessPage />);
  const section = await screen.findByRole("region", { name: "Application server SSH" });
  fireEvent.click(within(section).getByRole("button", { name: "Check access" }));
  expect(await within(section).findByText("Login tested")).toBeInTheDocument();
});
