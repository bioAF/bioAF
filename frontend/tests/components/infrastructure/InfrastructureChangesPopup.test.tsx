/**
 * The popup admins see after a bioAF update brings infrastructure changes (storage lifecycle 11I;
 * signed-off mockup M17, decision M17-a: once per admin per release).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { InfrastructureChangesPopup } from "@/components/infrastructure/InfrastructureChangesPopup";

let permissions: Record<string, boolean> = {};
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (r: string, a: string) => Boolean(permissions[`${r}:${a}`]),
    loading: false,
    failed: false,
  }),
}));
let busyReason: string | null = null;
jest.mock("@/hooks/useInfrastructureOperations", () => ({
  useInfrastructureOperations: () => ({ refresh: jest.fn(), open: mockOpen, showConflict: () => false }),
  useInfrastructureAvailability: () => ({ reason: busyReason }),
}));
const mockOpen = jest.fn();
const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  ApiError: jest.requireActual("@/lib/api").ApiError,
  api: { get: (...a: unknown[]) => mockGet(...a), post: (...a: unknown[]) => mockPost(...a) },
}));

function notice(extra: Record<string, unknown> = {}, seen = false) {
  return {
    pending: true,
    notice: { id: 3, version: "2026.12.0", unknown_baseline: false, modules: ["storage"], ...extra },
    seen,
    destructive_only_modules: [],
  };
}

beforeEach(() => {
  permissions = { "infrastructure:view": true, "infrastructure:deploy": true };
  busyReason = null;
  mockGet.mockReset();
  mockPost.mockReset();
  mockOpen.mockReset();
});

test("admins see the changes once, with the version", async () => {
  mockGet.mockResolvedValue(notice());
  render(<InfrastructureChangesPopup />);
  const dialog = await screen.findByRole("dialog", { name: /Infrastructure changes are ready to apply/ });
  expect(dialog).toHaveTextContent("v2026.12.0");
  expect(dialog).toHaveTextContent(
    "bioAF was updated to v2026.12.0. This version includes changes to your cloud infrastructure, such as new storage. Some new features need them.",
  );
  expect(dialog).toHaveTextContent(
    "Applying adds or updates resources. Existing resources and data are never deleted or replaced by this step.",
  );
  expect(dialog).toHaveTextContent(
    "You can also do this later from Infrastructure > Components > Infrastructure updates. While it runs, other infrastructure actions are unavailable to every administrator.",
  );
});

test("Later records only this admin's acknowledgment", async () => {
  mockGet.mockResolvedValue(notice());
  mockPost.mockResolvedValue(notice({}, true));
  render(<InfrastructureChangesPopup />);
  const dialog = await screen.findByRole("dialog", { name: /Infrastructure changes are ready to apply/ });
  fireEvent.click(within(dialog).getByRole("button", { name: "Later" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/definition-notice/3/seen", {}));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("Apply now starts the confirmed update and opens its status", async () => {
  mockGet.mockResolvedValue(notice());
  mockPost.mockImplementation((path: string) =>
    path.endsWith("/seen")
      ? Promise.resolve(notice({}, true))
      : Promise.resolve({ message: "Infrastructure update started", operation_id: 77 }),
  );
  render(<InfrastructureChangesPopup />);
  fireEvent.click(await screen.findByRole("button", { name: "Apply now" }));
  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/stack/check-updates", undefined, {
      idempotencyKey: expect.any(String),
    }),
  );
  await waitFor(() => expect(mockOpen).toHaveBeenCalledWith(77));
});

test("an unknown baseline asks for a check instead", async () => {
  mockGet.mockResolvedValue(notice({ unknown_baseline: true }));
  render(<InfrastructureChangesPopup />);
  const dialog = await screen.findByRole("dialog", { name: "Check your infrastructure for this version" });
  expect(dialog).toHaveTextContent(
    "bioAF can't tell which infrastructure changes were applied before this update. Run a check to add anything this version needs. Existing resources and data are never deleted or replaced by this step.",
  );
  expect(within(dialog).getByRole("button", { name: "Check now" })).toBeInTheDocument();
});

test("while another operation runs, Apply now is unavailable with the shared reason", async () => {
  busyReason = "Unavailable while Compute teardown (Operation #57) runs.";
  mockGet.mockResolvedValue(notice());
  render(<InfrastructureChangesPopup />);
  const apply = await screen.findByRole("button", { name: "Apply now" });
  expect(apply).toBeDisabled();
  expect(apply).toHaveAttribute("title", "Unavailable while Compute teardown (Operation #57) runs.");
});

test("an acknowledged notice or no pending changes shows nothing", async () => {
  mockGet.mockResolvedValue(notice({}, true));
  const { container } = render(<InfrastructureChangesPopup />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test("admins without infrastructure deploy are not asked", () => {
  permissions = { "infrastructure:view": true, "infrastructure:configure": true };
  render(<InfrastructureChangesPopup />);
  expect(mockGet).not.toHaveBeenCalled();
});
