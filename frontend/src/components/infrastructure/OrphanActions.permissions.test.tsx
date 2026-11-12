/**
 * Orphaned-resource actions delete or adopt cloud resources, so they need infrastructure `deploy`
 * wherever they appear: cleaning up or dismissing one orphan in the card, and Start Fresh or
 * Resume Deployment in the recovery dialog. Without it (or while permissions are loading or could
 * not be read) the controls stay visible, disabled, and say why; nothing is sent. Permission is
 * checked again when a confirmation is accepted. The backend enforces the same rule (plan 1
 * ORPH-1); these tests prove only the interface.
 */
import { act, fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import { OrphanedResourcesCard } from "./OrphanedResourcesCard";
import { DeployRecoveryModal } from "./DeployRecoveryModal";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
const mockPermissions = { granted: new Set<string>(), loading: false, failed: false };
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (resource: string, action: string) => mockPermissions.granted.has(`${resource}:${action}`),
    loading: mockPermissions.loading,
    failed: mockPermissions.failed,
  }),
}));
let mockConfirmAnswer: (value: boolean) => void = () => {};
const mockConfirm = jest.fn(() => new Promise<boolean>((resolve) => (mockConfirmAnswer = resolve)));
jest.mock("@/hooks/useConfirm", () => ({
  ...jest.requireActual("@/hooks/useConfirm"),
  useConfirm: () => mockConfirm,
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const ORPHAN = {
  id: 7,
  resource_type: "gke_cluster",
  resource_name: "bioaf-old-cluster",
  gcp_project_id: "proj",
  gcp_zone: "us-central1-a",
  stack_uid: "abc",
  status: "detected",
  error_message: null,
  detected_at: "2026-01-01T00:00:00Z",
  resolved_at: null,
};

const RECOVERABLE = {
  recoverable: [
    { id: 1, resource_name: "bioaf-x", gcp_project_id: "p", gcp_zone: null, stack_uid: "x", gke_status: "RUNNING", detected_at: null },
  ],
  provisioning: [],
  dead: [],
};

function grant(...actions: string[]) {
  mockPermissions.granted = new Set(actions.map((a) => `infrastructure:${a}`));
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockConfirm.mockClear();
  mockPermissions.loading = false;
  mockPermissions.failed = false;
  grant("view");
  mockGet.mockImplementation((url: string) =>
    Promise.resolve(url.includes("recovery-check") ? RECOVERABLE : { items: [ORPHAN], total: 1 }),
  );
  mockPost.mockResolvedValue({});
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

describe("the orphaned resources card", () => {
  test.each([
    ["view only", () => grant("view"), /does not include/],
    ["configure without deploy", () => grant("view", "configure"), /does not include/],
    ["loading permissions", () => (mockPermissions.loading = true), /Checking your permissions/],
    ["failed permissions", () => (mockPermissions.failed = true), /could not be confirmed/],
  ])("%s: Clean Up and Dismiss are disabled with a reason and send nothing", async (_label, setup, reason) => {
    setup();
    render(<OrphanedResourcesCard />);
    const cleanup = await screen.findByRole("button", { name: "Clean Up" });
    const dismiss = screen.getByRole("button", { name: "Dismiss" });
    expect(cleanup).toBeDisabled();
    expect(dismiss).toBeDisabled();
    expect(cleanup).toHaveAccessibleDescription(reason);
    expect(screen.getByText(reason)).toBeInTheDocument();
    fireEvent.click(cleanup);
    fireEvent.click(dismiss);
    expect(mockConfirm).not.toHaveBeenCalled();
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("with deploy, Clean Up keeps its confirmation and then sends the cleanup", async () => {
    grant("view", "deploy");
    render(<OrphanedResourcesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Clean Up" }));
    expect(mockConfirm).toHaveBeenCalled();
    await act(async () => mockConfirmAnswer(true));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/orphaned-resources/7/cleanup"));
  });

  test("with deploy, Dismiss sends the dismissal", async () => {
    grant("view", "deploy");
    render(<OrphanedResourcesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/orphaned-resources/7/dismiss"));
  });

  test("permission lost while the confirmation is open sends nothing", async () => {
    grant("view", "deploy");
    const view = render(<OrphanedResourcesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Clean Up" }));
    grant("view");
    view.rerender(<OrphanedResourcesCard />);
    await act(async () => mockConfirmAnswer(true));
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("nothing is sent on mount", async () => {
    grant("view", "deploy");
    render(<OrphanedResourcesCard />);
    await screen.findByRole("button", { name: "Clean Up" });
    expect(mockPost).not.toHaveBeenCalled();
  });
});

describe("the deployment recovery dialog", () => {
  const props = { open: true, onClose: jest.fn(), onRecovered: jest.fn(), onStartFresh: jest.fn() };

  test("without deploy, Resume and Start Fresh are disabled with a reason and send nothing", async () => {
    grant("view", "configure");
    render(<DeployRecoveryModal {...props} />);
    const resume = await screen.findByRole("button", { name: "Resume Deployment" });
    const fresh = screen.getByRole("button", { name: "Start Fresh" });
    expect(resume).toBeDisabled();
    expect(fresh).toBeDisabled();
    expect(resume).toHaveAccessibleDescription(/does not include/);
    fireEvent.click(resume);
    fireEvent.click(fresh);
    expect(mockConfirm).not.toHaveBeenCalled();
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("with deploy, Resume adopts the resource", async () => {
    grant("view", "deploy");
    render(<DeployRecoveryModal {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: "Resume Deployment" }));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/orphaned-resources/1/adopt"));
  });

  test("permission lost while Start Fresh's confirmation is open sends nothing", async () => {
    grant("view", "deploy");
    const view = render(<DeployRecoveryModal {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start Fresh" }));
    grant("view");
    view.rerender(<DeployRecoveryModal {...props} />);
    await act(async () => mockConfirmAnswer(true));
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("a recovery check the role may not make says so", async () => {
    grant("view");
    mockGet.mockRejectedValue(Object.assign(new Error("Forbidden"), { status: 403 }));
    render(<DeployRecoveryModal {...props} />);
    expect(await screen.findByText(/Your role cannot check or recover deployments/)).toBeInTheDocument();
  });
});
