/**
 * A cluster from an earlier deployment that bioAF could not check (location unknown, or Google
 * Cloud refused or failed the check): bioAF cannot tell whether it is still running and billing,
 * so the dialog says so instead of closing silently, alongside any other recovery choice.
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

const LOCATION_TEXT =
  "bioAF recorded a cluster from an earlier deployment (bioaf-demo-eu0001) but cannot tell where it is, so it cannot check whether it is still running and billing.";
const REFUSED_TEXT =
  "bioAF recorded a cluster from an earlier deployment (bioaf-demo-eu0002) in europe-west1 but Google Cloud did not let bioAF check it, so bioAF cannot tell whether it is still running and billing.";
const CONSOLE_TEXT =
  "Check the Kubernetes Engine page in the Google Cloud console and delete it there if it is no longer needed.";

const refused = {
  ...unverified,
  id: 9,
  resource_name: "bioaf-demo-eu0002",
  gcp_zone: "europe-west1",
  gke_status: "UNVERIFIED",
};

test("each unchecked earlier cluster is explained with its reason, and Continue proceeds", async () => {
  mockGet.mockResolvedValue({ recoverable: [], provisioning: [], dead: [], unverified: [unverified, refused] });
  renderModal();

  expect(await screen.findByRole("heading", { name: "A previous cluster could not be checked" })).toBeInTheDocument();
  expect(screen.getByText(LOCATION_TEXT)).toBeInTheDocument();
  expect(screen.getByText(REFUSED_TEXT)).toBeInTheDocument();
  expect(screen.getByText(CONSOLE_TEXT)).toBeInTheDocument();
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

test.each([
  ["recoverable", /Resume Deployment/],
  ["provisioning", /Check Again/],
] as const)("an unchecked cluster stays visible beside a %s one", async (bucket, button) => {
  const other = { ...unverified, id: 8, gcp_zone: "us-central1", gke_status: "RUNNING" };
  mockGet.mockResolvedValue({
    recoverable: bucket === "recoverable" ? [other] : [],
    provisioning: bucket === "provisioning" ? [{ ...other, gke_status: "PROVISIONING" }] : [],
    dead: [],
    unverified: [refused],
  });
  renderModal();

  await screen.findByRole("button", { name: button });
  expect(screen.getByRole("heading", { name: "A previous cluster could not be checked" })).toBeInTheDocument();
  expect(screen.getByText(REFUSED_TEXT)).toBeInTheDocument();
  expect(screen.getByText(CONSOLE_TEXT)).toBeInTheDocument();
});
