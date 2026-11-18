/**
 * A cluster from an earlier deployment whose location is unknown: bioAF cannot tell whether it is
 * still running and billing, so the dialog says so instead of closing silently.
 */
import { render, screen, fireEvent } from "@/testing/renderWithProviders";
import { DeployRecoveryModal } from "./DeployRecoveryModal";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false, failed: false }),
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const onClose = jest.fn();

const unverified = {
  id: 7,
  resource_name: "bioaf-demo-eu0001",
  gcp_project_id: "test-project",
  gcp_zone: null,
  stack_uid: "eu0001",
  gke_status: "LOCATION_UNKNOWN",
  detected_at: null,
};

beforeEach(() => {
  mockGet.mockReset();
  onClose.mockReset();
});

function renderModal() {
  render(<DeployRecoveryModal open onClose={onClose} onRecovered={jest.fn()} onStartFresh={jest.fn()} />);
}

test("an unlocated earlier cluster is explained, and Continue proceeds", async () => {
  mockGet.mockResolvedValue({ recoverable: [], provisioning: [], dead: [], unverified: [unverified] });
  renderModal();

  expect(await screen.findByRole("heading", { name: "A previous cluster could not be located" })).toBeInTheDocument();
  expect(
    screen.getByText(
      "bioAF recorded a cluster from an earlier deployment (bioaf-demo-eu0001) but cannot tell where it is, so it cannot check whether it is still running and billing.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      "Check the Kubernetes Engine page in the Google Cloud console and delete it there if it is no longer needed.",
    ),
  ).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(onClose).toHaveBeenCalled();
});

test("Check Again re-runs the check", async () => {
  mockGet.mockResolvedValue({ recoverable: [], provisioning: [], dead: [], unverified: [unverified] });
  renderModal();
  fireEvent.click(await screen.findByRole("button", { name: "Check Again" }));
  expect(mockGet).toHaveBeenCalledTimes(2);
});

test("a recoverable cluster still takes priority", async () => {
  mockGet.mockResolvedValue({
    recoverable: [{ ...unverified, id: 8, gcp_zone: "us-central1", gke_status: "RUNNING" }],
    provisioning: [],
    dead: [],
    unverified: [unverified],
  });
  renderModal();
  await screen.findByRole("button", { name: /Resume Deployment/ });
  expect(screen.queryByText("A previous cluster could not be located")).not.toBeInTheDocument();
});
