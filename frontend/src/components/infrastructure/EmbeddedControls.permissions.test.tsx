/**
 * The infrastructure page's embedded controls follow their endpoints' permissions:
 * initializing Terraform (bootstrap) needs infrastructure `create`; checking and applying
 * infrastructure updates and deploying or updating storage need infrastructure `deploy`; turning
 * auto-ingest on or off and saving its settings need pipelines `configure`. Without the permission
 * (or while it is loading or unreadable) the control stays visible, disabled with the reason, and
 * sends nothing.
 */
import { fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import { BootstrapCard } from "./BootstrapCard";
import { InfraUpdatesCard } from "./InfraUpdatesCard";
import { StorageSection } from "@/components/components/StorageSection";
import { AutoIngestControls } from "@/components/components/AutoIngestControls";

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a), post: (...a: unknown[]) => mockPost(...a) },
}));
jest.mock("@/lib/infrastructure", () => ({ infrastructure: { checkUpdates: jest.fn(), applyUpdates: jest.fn() } }));
jest.mock("@/hooks/useStackOptions", () => ({
  useStackOptions: () => ({ kubernetesOption: null, cloudProvider: "gcp", loading: false }),
}));
const mockPermissions = { granted: new Set<string>(), loading: false, failed: false };
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (resource: string, action: string) => mockPermissions.granted.has(`${resource}:${action}`),
    loading: mockPermissions.loading,
    failed: mockPermissions.failed,
  }),
}));

import { infrastructure } from "@/lib/infrastructure";
const checkUpdates = infrastructure.checkUpdates as jest.Mock;

function grant(...permissions: string[]) {
  mockPermissions.granted = new Set(["infrastructure:view", "pipelines:view", ...permissions]);
}

const AUTO_INGEST = {
  enabled: false,
  cleanup_policy: "delete_after_copy",
  listener_running: false,
  pubsub_topic: "t",
  pubsub_subscription: "s",
  messages_processed_24h: 0,
  messages_failed_24h: 0,
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  checkUpdates.mockReset();
  mockPermissions.loading = false;
  mockPermissions.failed = false;
  grant();
  mockGet.mockImplementation((url: string) =>
    url.includes("auto-ingest") ? Promise.resolve(AUTO_INGEST) : Promise.resolve({ buckets: [] }),
  );
  mockPost.mockResolvedValue({});
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

describe("bootstrap", () => {
  test("needs create", () => {
    const onStart = jest.fn();
    grant("infrastructure:configure", "infrastructure:deploy");
    render(<BootstrapCard terraformInitialized={false} gcpCredentialsConfigured onBootstrapStart={onStart} />);
    const button = screen.getByRole("button", { name: "Initialize Infrastructure" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(/infrastructure create permission/);
    fireEvent.click(button);
    expect(onStart).not.toHaveBeenCalled();
  });

  test("with create it starts", () => {
    const onStart = jest.fn();
    grant("infrastructure:create");
    render(<BootstrapCard terraformInitialized={false} gcpCredentialsConfigured onBootstrapStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: "Initialize Infrastructure" }));
    expect(onStart).toHaveBeenCalledTimes(1);
  });
});

describe("infrastructure updates", () => {
  test.each([
    ["configure only", () => grant("infrastructure:configure"), /infrastructure deploy permission/],
    ["loading", () => (mockPermissions.loading = true), /Checking your permissions/],
  ])("%s: checking is disabled and sends nothing", (_label, setup, reason) => {
    setup();
    render(<InfraUpdatesCard />);
    const check = screen.getByRole("button", { name: "Check for Infrastructure Updates" });
    expect(check).toBeDisabled();
    expect(check).toHaveAccessibleDescription(reason);
    fireEvent.click(check);
    expect(checkUpdates).not.toHaveBeenCalled();
  });

  test("with deploy it checks", async () => {
    grant("infrastructure:deploy");
    checkUpdates.mockResolvedValue({ has_changes: false, applying: false });
    render(<InfraUpdatesCard />);
    fireEvent.click(screen.getByRole("button", { name: "Check for Infrastructure Updates" }));
    await waitFor(() => expect(checkUpdates).toHaveBeenCalledTimes(1));
  });
});

describe("storage", () => {
  test("deploying storage needs deploy", async () => {
    const onDeploy = jest.fn();
    grant("infrastructure:configure");
    render(<StorageSection storageDeployed={false} terraformInitialized onDeploy={onDeploy} />);
    const deploy = await screen.findByRole("button", { name: /Deploy/ });
    expect(deploy).toBeDisabled();
    expect(deploy).toHaveAccessibleDescription(/infrastructure deploy permission/);
    fireEvent.click(deploy);
    expect(onDeploy).not.toHaveBeenCalled();
  });

  test("updating storage for auto-ingest needs deploy", async () => {
    grant("pipelines:configure", "infrastructure:configure");
    render(<AutoIngestControls storageDeployed pubsubConfigured={false} />);
    const update = await screen.findByRole("button", { name: "Update Storage Infrastructure" });
    expect(update).toBeDisabled();
    expect(update).toHaveAccessibleDescription(/infrastructure deploy permission/);
    fireEvent.click(update);
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("with deploy, updating storage sends the update", async () => {
    grant("infrastructure:deploy");
    render(<AutoIngestControls storageDeployed pubsubConfigured={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "Update Storage Infrastructure" }));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/storage/update"));
  });
});

describe("auto-ingest settings", () => {
  test("turning auto-ingest on needs pipelines configure, not an infrastructure grant", async () => {
    grant("infrastructure:configure", "infrastructure:deploy", "infrastructure:create");
    render(<AutoIngestControls storageDeployed pubsubConfigured />);
    const toggle = await screen.findByTestId("auto-ingest-toggle");
    await waitFor(() => expect(toggle).toHaveAccessibleDescription(/pipelines configure permission/));
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("with pipelines configure the toggle sends the change", async () => {
    grant("pipelines:configure");
    render(<AutoIngestControls storageDeployed pubsubConfigured />);
    const toggle = await screen.findByTestId("auto-ingest-toggle");
    await waitFor(() => expect(toggle).toBeEnabled());
    fireEvent.click(toggle);
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/settings/auto-ingest", expect.objectContaining({ enabled: true })));
  });
});
