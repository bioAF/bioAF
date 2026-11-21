/**
 * The shared infrastructure lock in the UI (storage lifecycle 11D; mockups M2-M6, M9, M10).
 *
 * One source answers whether an operation runs: the banner, every conflicting control and the
 * status popup read it. A failed or pending read never enables a competing action.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  InfrastructureOperationsProvider,
  useInfrastructureOperations,
} from "@/components/infrastructure/InfrastructureOperationsProvider";
import { InfrastructureBanner } from "@/components/infrastructure/InfrastructureBanner";
import { useInfrastructureActions } from "@/hooks/useInfrastructureActions";
import { useStartInfrastructureAction } from "@/hooks/useStartInfrastructureAction";
import type { OperationView } from "@/lib/infrastructureOperations";

let permissions: Record<string, boolean> = {};
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (resource: string, action: string) => Boolean(permissions[`${resource}:${action}`]),
    loading: false,
    failed: false,
  }),
}));

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  ApiError: jest.requireActual("@/lib/api").ApiError,
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

const ADMIN = { "infrastructure:view": true, "infrastructure:deploy": true, "infrastructure:configure": true };

function op(extra: Partial<OperationView> = {}): OperationView {
  return {
    id: 57,
    action: "compute_teardown",
    label: "Compute teardown",
    status: "running",
    phase: "Removing compute resources",
    started_by: "Admin A",
    accepted_at: "2026-11-21T14:02:00Z",
    started_at: "2026-11-21T14:02:01Z",
    finished_at: null,
    steps: [
      { key: "accepted", label: "Accepted", state: "done" },
      { key: "prerequisites", label: "Checking prerequisites", state: "done" },
      { key: "compute_teardown", label: "Removing compute resources", state: "running", completed: 19, total: 31 },
    ],
    message: null,
    error_category: null,
    partial_effects: null,
    cancel_requested_at: null,
    cancel_requested_by: null,
    cancel_deadline: null,
    recovered_at: null,
    outcome_seen_at: null,
    resources: { planned: ["google_container_cluster.main", "google_container_node_pool.pipeline"], completed: [] },
    ...extra,
  } as OperationView;
}

function respond(active: unknown, detail?: OperationView) {
  mockGet.mockImplementation((path: string) => {
    if (path === "/api/v1/infrastructure/operations/active") {
      return active instanceof Error ? Promise.reject(active) : Promise.resolve(active);
    }
    if (detail && path === `/api/v1/infrastructure/operations/${detail.id}`) return Promise.resolve(detail);
    return Promise.reject(new Error(`unexpected ${path}`));
  });
}

function DeployButton() {
  const gate = useInfrastructureActions().gate("deploy");
  return <button {...gate}>Deploy</button>;
}

function Shell({ children }: { children?: React.ReactNode }) {
  return (
    <InfrastructureOperationsProvider>
      <InfrastructureBanner />
      <DeployButton />
      {children}
    </InfrastructureOperationsProvider>
  );
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  permissions = { ...ADMIN };
});

test("nothing running: no banner, controls enabled", async () => {
  respond({ busy: false, state: null, operation: null, interrupted: [] });
  render(<Shell />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Deploy" })).toBeEnabled());
  expect(screen.queryByTestId("infrastructure-banner")).not.toBeInTheDocument();
});

test("controls stay unavailable until the first answer arrives", () => {
  mockGet.mockImplementation(() => new Promise(() => {}));
  render(<Shell />);
  const deploy = screen.getByRole("button", { name: "Deploy" });
  expect(deploy).toBeDisabled();
  expect(deploy).toHaveAttribute("title", "Unavailable until bioAF can confirm no other infrastructure action is running.");
});

test("another admin's operation grays out controls and the banner opens its status (M3, M5)", async () => {
  respond({ busy: true, state: "running", operation: op(), interrupted: [] }, op());
  render(<Shell />);

  const banner = await screen.findByTestId("infrastructure-banner");
  expect(banner).toHaveTextContent(/^Compute teardown in progress, started by Admin A at .+\. Other infrastructure actions are unavailable until it finishes\./);
  const deploy = screen.getByRole("button", { name: "Deploy" });
  expect(deploy).toBeDisabled();
  expect(deploy).toHaveAttribute("title", "Unavailable while Compute teardown (Operation #57) runs.");

  fireEvent.click(within(banner).getByRole("button", { name: "View status" }));
  const popup = await screen.findByRole("dialog", { name: "Compute teardown" });
  expect(within(popup).getByText("Operation #57")).toBeInTheDocument();
  expect(within(popup).getByText(/^Started by Admin A at /)).toBeInTheDocument();
  expect(within(popup).getByText("19 of 31")).toBeInTheDocument();
  expect(within(popup).getByText("Opened from the banner. Started by another administrator.")).toBeInTheDocument();
});

test("users without infrastructure actions see only the maintenance notice (M4)", async () => {
  permissions = { "experiments:view": true };
  respond({ busy: true, state: "running", operation: null, interrupted: [] });
  render(<Shell />);
  const banner = await screen.findByTestId("infrastructure-banner");
  expect(banner).toHaveTextContent(
    "Infrastructure maintenance in progress. Pipeline runs, notebooks, and uploads may be unavailable until it finishes.",
  );
  expect(within(banner).queryByRole("button")).not.toBeInTheDocument();
});

test("a failed status read keeps actions unavailable and says so (M3)", async () => {
  respond(new Error("network"));
  render(<Shell />);
  expect(await screen.findByTestId("infrastructure-banner")).toHaveTextContent(
    "bioAF cannot confirm whether an infrastructure action is running. Actions stay unavailable until it can. bioAF keeps checking.",
  );
  expect(screen.getByRole("button", { name: "Deploy" })).toBeDisabled();
});

test("an interrupted outcome is announced until an admin opens it (M9)", async () => {
  const interrupted = op({
    id: 60,
    action: "compute_deploy",
    label: "Compute deployment",
    status: "interrupted",
    finished_at: "2026-11-21T13:52:00Z",
    message: "The bioAF server restarted while this was running, and Terraform stopped with it.",
    steps: [{ key: "compute", label: "Creating compute resources", state: "interrupted", completed: 18, total: 31 }],
    resources: null,
  });
  respond({ busy: false, state: null, operation: null, interrupted: [interrupted] }, interrupted);
  mockPost.mockResolvedValue(interrupted);
  render(<Shell />);

  const banner = await screen.findByTestId("infrastructure-banner");
  expect(banner).toHaveTextContent(/^Compute deployment was interrupted by a server restart at /);
  fireEvent.click(within(banner).getByRole("button", { name: "View details" }));

  const popup = await screen.findByRole("dialog", { name: "Compute deployment interrupted" });
  expect(
    within(popup).getByText("The bioAF server restarted while this was running, and Terraform stopped with it."),
  ).toBeInTheDocument();
  expect(within(popup).getByText("Interrupted")).toBeInTheDocument();
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/operations/60/seen", {}));
});

test("an admin records recovery with a reason (M10)", async () => {
  const paused = op({ id: 61, action: "storage_destroy", label: "Storage destroy", status: "recovery_required" });
  respond({ busy: true, state: "recovery_required", operation: paused, interrupted: [] }, paused);
  mockPost.mockResolvedValue({ ...paused, status: "interrupted" });
  render(<Shell />);

  const banner = await screen.findByTestId("infrastructure-banner");
  expect(banner).toHaveTextContent(
    "Infrastructure actions are paused. Storage destroy (Operation #61) needs recovery before anything else can run.",
  );
  fireEvent.click(within(banner).getByRole("button", { name: "View details" }));
  const popup = await screen.findByRole("dialog", { name: "Storage destroy needs recovery" });
  const mark = within(popup).getByRole("button", { name: "Mark as recovered" });
  expect(mark).toBeDisabled();
  fireEvent.change(within(popup).getByLabelText("Reason (saved in the audit log)"), {
    target: { value: "VM checked at 16:05, no terraform process." },
  });
  fireEvent.click(within(popup).getByLabelText("I confirmed that this Terraform process is no longer running."));
  fireEvent.click(mark);

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/operations/61/recover", {
      reason: "VM checked at 16:05, no terraform process.",
      confirm: true,
    }),
  );
});

test("recovery is refused while bioAF still sees the process (M10)", async () => {
  const paused = op({ id: 61, action: "storage_destroy", label: "Storage destroy", status: "recovery_required" });
  respond({ busy: true, state: "recovery_required", operation: paused, interrupted: [] }, paused);
  const { ApiError } = jest.requireActual("@/lib/api");
  mockPost.mockRejectedValue(
    new ApiError(
      409,
      "bioAF still sees this Terraform process running, so it cannot be marked as recovered. Wait for it to finish or stop it on the server.",
    ),
  );
  render(<Shell />);
  fireEvent.click(within(await screen.findByTestId("infrastructure-banner")).getByRole("button", { name: "View details" }));
  const popup = await screen.findByRole("dialog", { name: "Storage destroy needs recovery" });
  fireEvent.change(within(popup).getByLabelText("Reason (saved in the audit log)"), { target: { value: "x" } });
  fireEvent.click(within(popup).getByRole("checkbox"));
  fireEvent.click(within(popup).getByRole("button", { name: "Mark as recovered" }));
  expect(
    await within(popup).findByText(
      "bioAF still sees this Terraform process running, so it cannot be marked as recovered. Wait for it to finish or stop it on the server.",
    ),
  ).toBeInTheDocument();
});

function ConflictTrigger({ error }: { error: unknown }) {
  const ops = useInfrastructureOperations();
  return <button onClick={() => ops?.showConflict(error, "check for infrastructure updates")}>Trigger</button>;
}

test("a request that lost the race explains who started first (M6)", async () => {
  respond({ busy: true, state: "running", operation: op(), interrupted: [] }, op());
  const { ApiError } = jest.requireActual("@/lib/api");
  const error = new ApiError(409, "busy", "infrastructure_busy", { operation: op() });
  render(
    <Shell>
      <ConflictTrigger error={error} />
    </Shell>,
  );
  await screen.findByTestId("infrastructure-banner");
  fireEvent.click(screen.getByRole("button", { name: "Trigger" }));

  const dialog = await screen.findByRole("dialog", { name: "Another infrastructure action started first" });
  expect(dialog).toHaveTextContent(
    /Compute teardown \(Operation #57\) was started by Admin A at .+\. Your check for infrastructure updates was not started\./,
  );
  expect(dialog).toHaveTextContent("You can follow the teardown's progress and try again after it finishes.");
  fireEvent.click(within(dialog).getByRole("button", { name: "View teardown status" }));
  expect(await screen.findByRole("dialog", { name: "Compute teardown" })).toBeInTheDocument();
});

function OpenAsInitiator() {
  const ops = useInfrastructureOperations();
  return <button onClick={() => ops?.open(57)}>Open mine</button>;
}

test("the admin who confirmed sees the status popup for their operation (M2)", async () => {
  respond({ busy: true, state: "running", operation: op(), interrupted: [] }, op());
  render(
    <Shell>
      <OpenAsInitiator />
    </Shell>,
  );
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Open mine" }));
  });
  const popup = await screen.findByRole("dialog", { name: "Compute teardown" });
  expect(within(popup).getByText("Closing this window does not stop the teardown.")).toBeInTheDocument();
  expect(within(popup).getByText("google_container_cluster.main")).toBeInTheDocument();
});

function StartTeardown() {
  const start = useStartInfrastructureAction();
  return (
    <button onClick={() => void start("/api/v1/infrastructure/stack/teardown-background", { confirm: true }, "compute teardown")}>
      Confirm teardown
    </button>
  );
}

test("confirming an action opens its own operation's status (M2)", async () => {
  respond({ busy: false, state: null, operation: null, interrupted: [] }, op({ id: 58 }));
  mockPost.mockResolvedValue({ message: "Teardown started", operation_id: 58 });
  render(
    <Shell>
      <StartTeardown />
    </Shell>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Confirm teardown" }));
  const popup = await screen.findByRole("dialog", { name: "Compute teardown" });
  expect(within(popup).getByText("Operation #58")).toBeInTheDocument();
  expect(mockPost).toHaveBeenCalledWith(
    "/api/v1/infrastructure/stack/teardown-background",
    { confirm: true },
    { idempotencyKey: expect.any(String) },
  );
});

test("a confirmation that lost the race shows who started first (M6)", async () => {
  respond({ busy: false, state: null, operation: null, interrupted: [] }, op());
  const { ApiError } = jest.requireActual("@/lib/api");
  mockPost.mockRejectedValue(new ApiError(409, "busy", "infrastructure_busy", { operation: op() }));
  render(
    <Shell>
      <StartTeardown />
    </Shell>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Confirm teardown" }));
  expect(await screen.findByRole("dialog", { name: "Another infrastructure action started first" })).toHaveTextContent(
    "Your compute teardown was not started.",
  );
});
