import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InfraUpdatesCard } from "./InfraUpdatesCard";
import type { CheckUpdatesResult } from "@/lib/infrastructure";

jest.mock("@/lib/infrastructure", () => ({
  infrastructure: {
    checkUpdates: jest.fn(),
    applyUpdates: jest.fn(),
  },
}));

import { infrastructure } from "@/lib/infrastructure";
const checkUpdates = infrastructure.checkUpdates as jest.Mock;
const applyUpdates = infrastructure.applyUpdates as jest.Mock;

const result = (over: Partial<CheckUpdatesResult> = {}): CheckUpdatesResult => ({
  has_changes: false,
  has_additive: false,
  has_destructive: false,
  requires_approval: false,
  applying: false,
  realigned: null,
  modules_with_additive: [],
  modules: [],
  additive_resources: [],
  destructive_resources: [],
  ...over,
});

const litCreate = {
  address: "module.storage.google_storage_bucket.literature",
  type: "google_storage_bucket",
  action: "create",
  description: "GCS bucket: literature",
};
const rawReplace = {
  address: "module.storage.google_storage_bucket.raw",
  type: "google_storage_bucket",
  action: "replace",
  description: "GCS bucket: raw",
  stateful: true,
};

beforeEach(() => {
  checkUpdates.mockReset();
  applyUpdates.mockReset();
});

const clickCheck = () =>
  userEvent.click(screen.getByRole("button", { name: /check for infrastructure updates/i }));

test("reports when infrastructure is up to date", async () => {
  checkUpdates.mockResolvedValue(result());
  render(<InfraUpdatesCard />);
  await clickCheck();
  await waitFor(() => expect(screen.getByText(/up to date/i)).toBeInTheDocument());
});

test("auto-applies additive-only changes and notifies the parent", async () => {
  checkUpdates.mockResolvedValue(
    result({
      has_changes: true,
      has_additive: true,
      applying: true,
      modules_with_additive: ["storage"],
      additive_resources: [litCreate],
    }),
  );
  const onApplyStarted = jest.fn();
  render(<InfraUpdatesCard onApplyStarted={onApplyStarted} />);
  await clickCheck();
  await waitFor(() =>
    expect(screen.getByText(/applying updates in the background/i)).toBeInTheDocument(),
  );
  expect(onApplyStarted).toHaveBeenCalled();
  expect(applyUpdates).not.toHaveBeenCalled();
});

test("shows the literature create AND the skipped destructive buckets, applies additive only", async () => {
  checkUpdates.mockResolvedValue(
    result({
      has_changes: true,
      has_additive: true,
      has_destructive: true,
      requires_approval: true,
      modules_with_additive: ["storage"],
      additive_resources: [litCreate],
      destructive_resources: [rawReplace],
    }),
  );
  applyUpdates.mockResolvedValue({ applying: true, modules: ["storage"] });
  const onApplyStarted = jest.fn();
  render(<InfraUpdatesCard onApplyStarted={onApplyStarted} />);

  await clickCheck();
  // The new literature bucket is now visible (the original bug hid it)...
  await waitFor(() => expect(screen.getByText(/GCS bucket: literature/i)).toBeInTheDocument());
  // ...alongside the destructive bucket that will be skipped.
  expect(screen.getByText(/will not be applied/i)).toBeInTheDocument();
  expect(screen.getByText(/GCS bucket: raw/i)).toBeInTheDocument();
  expect(applyUpdates).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: /apply additive changes only/i }));
  await waitFor(() => expect(applyUpdates).toHaveBeenCalledWith(["storage"]));
  await waitFor(() =>
    expect(screen.getByText(/applying updates in the background/i)).toBeInTheDocument(),
  );
  expect(onApplyStarted).toHaveBeenCalled();
});

test("shows an error when the check fails", async () => {
  checkUpdates.mockRejectedValue(new Error("Another Terraform operation is in progress."));
  render(<InfraUpdatesCard />);
  await clickCheck();
  await waitFor(() =>
    expect(screen.getByText(/another terraform operation is in progress/i)).toBeInTheDocument(),
  );
});
