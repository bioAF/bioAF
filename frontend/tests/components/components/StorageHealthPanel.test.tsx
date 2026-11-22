/**
 * Storage health on the Components page (storage lifecycle 11C; signed-off mockups M14 and M15).
 *
 * Each bucket shows one health chip. Admins (infrastructure deploy or configure) get Check again;
 * admins with configure get the bucket confirmation notice and dialog. Everyone else with
 * infrastructure view sees the chips only.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StorageHealthPanel } from "@/components/components/StorageHealthPanel";

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
const COMP_BIO = { "infrastructure:view": true };

function store(bucket: string, health: string, extra: Record<string, unknown> = {}) {
  return {
    store: bucket.split("-")[1],
    bucket_name: bucket,
    health,
    basis: null,
    observed_owner: null,
    observed_created_at: null,
    ...extra,
  };
}

function health(stores: ReturnType<typeof store>[], checkedMinutesAgo = 2) {
  return {
    checked_at: new Date(Date.now() - checkedMinutesAgo * 60_000).toISOString(),
    stores,
    unconfirmed: stores.filter((s) => s.health === "not_confirmed").length,
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  permissions = { ...ADMIN };
});

it("shows one approved chip per bucket", async () => {
  mockGet.mockResolvedValue(
    health([
      store("bioaf-raw-acme-4bd459", "available"),
      store("bioaf-results-acme-4bd459", "not_found"),
      store("bioaf-references-acme-4bd459", "no_access"),
      store("bioaf-literature-acme-4bd459", "not_responding"),
      store("bioaf-working-acme-4bd459", "not_confirmed"),
      store("bioaf-ingest-acme-4bd459", "wrong_owner"),
      store("bioaf-config-acme-4bd459", "not_mounted"),
      store("bioaf-backups-acme-4bd459", "cannot_check"),
    ]),
  );
  render(<StorageHealthPanel />);

  const chip = async (bucket: string) =>
    within(await screen.findByTestId(`store-health-${bucket}`)).getByTestId("health-chip").textContent;
  expect(await chip("bioaf-raw-acme-4bd459")).toBe("Available");
  expect(await chip("bioaf-results-acme-4bd459")).toBe("Not found");
  expect(await chip("bioaf-references-acme-4bd459")).toBe("No access");
  expect(await chip("bioaf-literature-acme-4bd459")).toBe("Not responding");
  expect(await chip("bioaf-working-acme-4bd459")).toBe("Not confirmed");
  expect(await chip("bioaf-ingest-acme-4bd459")).toBe("Does not match bioAF's records");
  expect(await chip("bioaf-config-acme-4bd459")).toBe("Not mounted");
  expect(await chip("bioaf-backups-acme-4bd459")).toBe("Cannot check");
  expect(mockGet).toHaveBeenCalledWith("/api/v1/infrastructure/storage/health");
});

it("shows when it was checked, and Check again inspects now", async () => {
  mockGet.mockResolvedValue(health([store("bioaf-results-acme-4bd459", "not_found")], 2));
  mockPost.mockResolvedValue(health([store("bioaf-results-acme-4bd459", "available")], 0));
  render(<StorageHealthPanel />);

  expect(await screen.findByText("Checked 2 minutes ago")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/storage/health/check", {}),
  );
  expect(
    await within(screen.getByTestId("store-health-bioaf-results-acme-4bd459")).findByText("Available"),
  ).toBeInTheDocument();
});

it("gives comp_bio the chips only", async () => {
  permissions = { ...COMP_BIO };
  mockGet.mockResolvedValue(health([store("bioaf-raw-acme-4bd459", "not_confirmed")]));
  render(<StorageHealthPanel />);

  expect(await screen.findByText("Not confirmed")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review and confirm buckets" })).not.toBeInTheDocument();
});

it("explains a bucket that was not found", async () => {
  mockGet.mockResolvedValue(health([store("bioaf-results-acme-4bd459", "not_found")]));
  render(<StorageHealthPanel />);

  expect(
    await screen.findByText(
      "1 bucket was not found. Files stored there cannot be read and new results cannot be saved. To create it again, use Check for Infrastructure Updates.",
    ),
  ).toBeInTheDocument();
});

it("explains a bucket that does not match bioAF's records", async () => {
  mockGet.mockResolvedValue(health([store("bioaf-raw-acme-4bd459", "wrong_owner")]));
  render(<StorageHealthPanel />);

  expect(
    await screen.findByText(
      "This storage does not match bioAF's records. bioAF will not change or delete it.",
    ),
  ).toBeInTheDocument();
});

it("lets an admin confirm unconfirmed buckets with a reason", async () => {
  mockGet.mockResolvedValue(
    health([
      store("bioaf-raw-acme-4bd459", "not_confirmed", {
        observed_owner: "123456789012",
        observed_created_at: "2026-03-02T10:00:00Z",
      }),
      store("bioaf-results-acme-4bd459", "not_confirmed"),
      store("bioaf-working-acme-4bd459", "available"),
    ]),
  );
  mockPost.mockResolvedValue(
    health([
      store("bioaf-raw-acme-4bd459", "available", { basis: "enrolled" }),
      store("bioaf-results-acme-4bd459", "available", { basis: "enrolled" }),
      store("bioaf-working-acme-4bd459", "available"),
    ]),
  );
  render(<StorageHealthPanel />);

  expect(
    await screen.findByText(
      "bioAF could not confirm that 2 storage buckets are the ones it created. Uploads, downloads, and pipelines keep working. Destroy Storage and automatic repairs are unavailable for these buckets until an administrator confirms them.",
    ),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Review and confirm buckets" }));

  const dialog = await screen.findByRole("dialog", { name: "Confirm storage buckets" });
  expect(within(dialog).getByText("bioaf-raw-acme-4bd459")).toBeInTheDocument();
  expect(within(dialog).getByText("project 123456789012 · created 2026-03-02")).toBeInTheDocument();
  expect(within(dialog).queryByText("bioaf-working-acme-4bd459")).not.toBeInTheDocument();

  const confirm = within(dialog).getByRole("button", { name: "Confirm buckets" });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Reason (saved in the audit log)"), {
    target: { value: "Checked against our Google Cloud project" },
  });
  expect(confirm).toBeDisabled();
  fireEvent.click(
    within(dialog).getByLabelText(
      "I confirm these buckets belong to this bioAF installation. This records them as of today; it does not prove their earlier history.",
    ),
  );
  fireEvent.click(confirm);

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/storage/identity/confirm", {
      stores: ["raw", "results"],
      reason: "Checked against our Google Cloud project",
      confirm: true,
    }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.queryByText(/could not confirm that/)).not.toBeInTheDocument();
});

it("keeps the dialog open and shows a refusal", async () => {
  mockGet.mockResolvedValue(health([store("bioaf-raw-acme-4bd459", "not_confirmed")]));
  const { ApiError } = jest.requireActual("@/lib/api");
  mockPost.mockRejectedValue(
    new ApiError(409, "bioaf-raw-acme-4bd459 does not match bioAF's records, so it cannot be confirmed."),
  );
  render(<StorageHealthPanel />);

  fireEvent.click(await screen.findByRole("button", { name: "Review and confirm buckets" }));
  const dialog = await screen.findByRole("dialog", { name: "Confirm storage buckets" });
  fireEvent.change(within(dialog).getByLabelText("Reason (saved in the audit log)"), {
    target: { value: "mine" },
  });
  fireEvent.click(within(dialog).getByRole("checkbox"));
  fireEvent.click(within(dialog).getByRole("button", { name: "Confirm buckets" }));

  expect(
    await within(dialog).findByText(
      "bioaf-raw-acme-4bd459 does not match bioAF's records, so it cannot be confirmed.",
    ),
  ).toBeInTheDocument();
});

it("stays quiet when health cannot be loaded", async () => {
  mockGet.mockRejectedValue(new Error("network"));
  render(<StorageHealthPanel />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByTestId("health-chip")).not.toBeInTheDocument();
});

it("appears in the Components storage section once storage is deployed", async () => {
  const { StorageSection } = jest.requireActual("@/components/components/StorageSection");
  mockGet.mockImplementation((path: string) =>
    path === "/api/v1/infrastructure/storage/health"
      ? Promise.resolve(health([store("bioaf-results-acme-4bd459", "not_found")]))
      : Promise.resolve({ buckets: [] }),
  );
  render(<StorageSection storageDeployed terraformInitialized onDeploy={jest.fn()} />);
  expect(await screen.findByText("Not found")).toBeInTheDocument();
});
