/**
 * "Check for Infrastructure Updates" confirms first, then one operation plans and applies
 * additive changes automatically (storage lifecycle 11E; signed-off mockup M1). The card shows
 * the outcome of its own operation only.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InfraUpdatesCard } from "./InfraUpdatesCard";

jest.mock("@/lib/infrastructure", () => ({
  infrastructure: {
    checkUpdates: jest.fn(),
    applyUpdates: jest.fn(),
  },
}));
import { infrastructure } from "@/lib/infrastructure";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false, failed: false }),
}));

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({
  ApiError: jest.requireActual("@/lib/api").ApiError,
  api: { get: (...a: unknown[]) => mockGet(...a) },
}));

const checkUpdates = infrastructure.checkUpdates as jest.Mock;

function operation(extra: Record<string, unknown> = {}) {
  return {
    id: 70,
    action: "infrastructure_update",
    label: "Infrastructure update",
    status: "succeeded",
    accepted_at: "2026-11-21T14:00:00Z",
    steps: [],
    message: "Your infrastructure is up to date.",
    partial_effects: { added: 0, destructive: [] },
    ...extra,
  };
}

beforeEach(() => {
  checkUpdates.mockReset();
  mockGet.mockReset();
});

const clickCheck = () => userEvent.click(screen.getByRole("button", { name: "Check for Infrastructure Updates" }));

test("describes what the check does", () => {
  render(<InfraUpdatesCard />);
  expect(
    screen.getByText(
      "Compare deployed infrastructure with this version of bioAF and add any new resources it needs. Changes that would delete or replace existing resources are listed, never applied here.",
    ),
  ).toBeInTheDocument();
});

test("asks for confirmation before anything runs", async () => {
  render(<InfraUpdatesCard />);
  await clickCheck();

  const dialog = await screen.findByRole("dialog", { name: "Check for infrastructure updates" });
  expect(dialog).toHaveTextContent(
    "bioAF will compare your deployed infrastructure with this version of bioAF and add anything new it needs, such as a new storage bucket.",
  );
  expect(dialog).toHaveTextContent(
    "Existing resources are never deleted or replaced by this check. If an update would delete or replace something, bioAF lists it for you and leaves it unchanged.",
  );
  expect(dialog).toHaveTextContent(
    "While this runs, deploy, teardown, storage, and image build actions are unavailable to every administrator.",
  );
  expect(checkUpdates).not.toHaveBeenCalled();

  await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(checkUpdates).not.toHaveBeenCalled();
});

test("confirming starts one operation and shows its outcome", async () => {
  checkUpdates.mockResolvedValue({ message: "Infrastructure update started", operation_id: 70 });
  mockGet.mockResolvedValue(operation({ message: "Updates applied: 1 resource added.", partial_effects: { added: 1 } }));
  const onApplyStarted = jest.fn();
  render(<InfraUpdatesCard onApplyStarted={onApplyStarted} />);

  await clickCheck();
  const dialog = await screen.findByRole("dialog", { name: "Check for infrastructure updates" });
  await userEvent.click(within(dialog).getByRole("button", { name: "Check and apply updates" }));

  await waitFor(() => expect(checkUpdates).toHaveBeenCalledWith(expect.any(String)));
  expect(onApplyStarted).toHaveBeenCalled();
  expect(await screen.findByText("Updates applied: 1 resource added.")).toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledWith("/api/v1/infrastructure/operations/70", expect.anything());
});

test("a skipped delete/replace is listed with the existing red list", async () => {
  checkUpdates.mockResolvedValue({ message: "Infrastructure update started", operation_id: 71 });
  mockGet.mockResolvedValue(
    operation({
      id: 71,
      message: "1 change was not applied because it would delete or replace an existing resource.",
      partial_effects: {
        added: 0,
        destructive: [
          {
            address: "module.storage.google_storage_bucket.raw",
            type: "google_storage_bucket",
            action: "replace",
            description: "GCS bucket: raw",
            stateful: true,
          },
        ],
      },
    }),
  );
  render(<InfraUpdatesCard />);
  await clickCheck();
  await userEvent.click(
    within(await screen.findByRole("dialog", { name: "Check for infrastructure updates" })).getByRole("button", {
      name: "Check and apply updates",
    }),
  );

  expect(
    await screen.findByText("1 change was not applied because it would delete or replace an existing resource."),
  ).toBeInTheDocument();
  expect(screen.getByText(/GCS bucket: raw/)).toBeInTheDocument();
  expect(screen.getByText("(stored data)")).toBeInTheDocument();
});

test("a failed update shows its outcome", async () => {
  checkUpdates.mockResolvedValue({ message: "Infrastructure update started", operation_id: 72 });
  mockGet.mockResolvedValue(
    operation({ id: 72, status: "failed", message: "Applying storage changes failed. Nothing else was applied." }),
  );
  render(<InfraUpdatesCard />);
  await clickCheck();
  await userEvent.click(
    within(await screen.findByRole("dialog", { name: "Check for infrastructure updates" })).getByRole("button", {
      name: "Check and apply updates",
    }),
  );
  expect(await screen.findByText("Applying storage changes failed. Nothing else was applied.")).toBeInTheDocument();
});

test("a check that cannot start shows why", async () => {
  checkUpdates.mockRejectedValue(new Error("No infrastructure is deployed"));
  render(<InfraUpdatesCard />);
  await clickCheck();
  await userEvent.click(
    within(await screen.findByRole("dialog", { name: "Check for infrastructure updates" })).getByRole("button", {
      name: "Check and apply updates",
    }),
  );
  expect(await screen.findByText("No infrastructure is deployed")).toBeInTheDocument();
});

describe("pending infrastructure changes after an update (M17)", () => {
  function noticeResponse(extra: Record<string, unknown>) {
    return {
      pending: true,
      notice: { id: 3, version: "2026.12.0", unknown_baseline: false, modules: ["storage"] },
      seen: true,
      destructive_only_modules: [],
      ...extra,
    };
  }

  test("the card reminds while changes wait to be applied", async () => {
    mockGet.mockImplementation((path: string) =>
      path === "/api/v1/infrastructure/definition-notice" ? Promise.resolve(noticeResponse({})) : Promise.resolve({}),
    );
    render(<InfraUpdatesCard />);
    expect(
      await screen.findByText("Infrastructure changes from bioAF v2026.12.0 are waiting to be applied."),
    ).toBeInTheDocument();
  });

  test("an unknown baseline asks for a check", async () => {
    mockGet.mockImplementation((path: string) =>
      path === "/api/v1/infrastructure/definition-notice"
        ? Promise.resolve(noticeResponse({ notice: { id: 3, version: "2026.12.0", unknown_baseline: true, modules: [] } }))
        : Promise.resolve({}),
    );
    render(<InfraUpdatesCard />);
    expect(
      await screen.findByText(
        "bioAF can't tell which infrastructure changes were applied before this update. Run a check to add anything this version needs.",
      ),
    ).toBeInTheDocument();
  });

  test("only delete/replace changes are explained, not offered", async () => {
    mockGet.mockImplementation((path: string) =>
      path === "/api/v1/infrastructure/definition-notice"
        ? Promise.resolve({ pending: false, notice: null, seen: false, destructive_only_modules: ["storage"] })
        : Promise.resolve({}),
    );
    render(<InfraUpdatesCard />);
    expect(
      await screen.findByText(
        "Some infrastructure changes in this version would delete or replace existing resources. bioAF does not apply these automatically. They take effect the next time you tear down and redeploy.",
      ),
    ).toBeInTheDocument();
  });
});
