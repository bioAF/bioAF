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

const baseResult = (over: Partial<CheckUpdatesResult> = {}): CheckUpdatesResult => ({
  has_changes: false,
  requires_approval: false,
  applying: false,
  modules_with_changes: [],
  modules: [],
  destructive_resources: [],
  ...over,
});

beforeEach(() => {
  checkUpdates.mockReset();
  applyUpdates.mockReset();
});

test("reports when infrastructure is up to date", async () => {
  checkUpdates.mockResolvedValue(baseResult());
  render(<InfraUpdatesCard />);
  await userEvent.click(screen.getByRole("button", { name: /check for infrastructure updates/i }));
  await waitFor(() =>
    expect(screen.getByText(/up to date/i)).toBeInTheDocument(),
  );
});

test("auto-applies safe changes and notifies the parent", async () => {
  checkUpdates.mockResolvedValue(
    baseResult({ has_changes: true, applying: true, modules_with_changes: ["storage"] }),
  );
  const onApplyStarted = jest.fn();
  render(<InfraUpdatesCard onApplyStarted={onApplyStarted} />);
  await userEvent.click(screen.getByRole("button", { name: /check for infrastructure updates/i }));
  await waitFor(() =>
    expect(screen.getByText(/applying updates in the background/i)).toBeInTheDocument(),
  );
  expect(onApplyStarted).toHaveBeenCalled();
  expect(applyUpdates).not.toHaveBeenCalled();
});

test("holds destructive changes for approval, then applies on confirm", async () => {
  checkUpdates.mockResolvedValue(
    baseResult({
      has_changes: true,
      requires_approval: true,
      modules_with_changes: ["storage"],
      destructive_resources: [
        {
          address: "module.storage.google_storage_bucket.raw",
          type: "google_storage_bucket",
          action: "delete",
          description: "GCS bucket: raw",
        },
      ],
    }),
  );
  applyUpdates.mockResolvedValue({ applying: true, modules: ["storage"] });
  const onApplyStarted = jest.fn();
  render(<InfraUpdatesCard onApplyStarted={onApplyStarted} />);

  await userEvent.click(screen.getByRole("button", { name: /check for infrastructure updates/i }));
  await waitFor(() =>
    expect(screen.getByText(/would destroy or replace stored data/i)).toBeInTheDocument(),
  );
  // The destructive resource is listed and nothing has applied yet.
  expect(screen.getByText(/GCS bucket: raw/i)).toBeInTheDocument();
  expect(applyUpdates).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: /apply anyway/i }));
  await waitFor(() => expect(applyUpdates).toHaveBeenCalledWith(["storage"]));
  await waitFor(() =>
    expect(screen.getByText(/applying updates in the background/i)).toBeInTheDocument(),
  );
  expect(onApplyStarted).toHaveBeenCalled();
});

test("shows an error when the check fails", async () => {
  checkUpdates.mockRejectedValue(new Error("Another Terraform operation is in progress."));
  render(<InfraUpdatesCard />);
  await userEvent.click(screen.getByRole("button", { name: /check for infrastructure updates/i }));
  await waitFor(() =>
    expect(screen.getByText(/another terraform operation is in progress/i)).toBeInTheDocument(),
  );
});
