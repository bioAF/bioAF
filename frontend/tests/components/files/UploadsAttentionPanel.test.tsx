/**
 * Uploads received but not yet filed, listed for admins and comp_bio on Data & Files
 * (storage lifecycle 11F; signed-off mockup M12, decision M12-a).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { UploadsAttentionPanel } from "@/components/files/UploadsAttentionPanel";

let canEdit = true;
let canConfigure = false;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (r: string, a: string) =>
      (r === "files" && a === "edit" && canEdit) || (r === "infrastructure" && a === "configure" && canConfigure),
    loading: false,
    failed: false,
  }),
}));
const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  ApiError: jest.requireActual("@/lib/api").ApiError,
  api: { get: (...a: unknown[]) => mockGet(...a), post: (...a: unknown[]) => mockPost(...a) },
}));

const UPLOAD = {
  upload_id: "u-1",
  filename: "PBMC_S1_L001_R1_001.fastq.gz",
  reference: "UPL-7F3A21",
  stage: "received",
  state: "needs_attention",
  attempts: 4,
  experiment_id: 42,
  experiment_name: "PBMC pilot",
  uploaded_by: "Priya",
  created_at: "2026-11-21T15:00:00Z",
};

beforeEach(() => {
  canEdit = true;
  canConfigure = false;
  mockGet.mockReset();
  mockPost.mockReset();
});

test("lists uploads that need attention with who, where and the reference", async () => {
  mockGet.mockResolvedValue({ uploads: [UPLOAD] });
  render(<UploadsAttentionPanel />);
  expect(await screen.findByText("1 upload needs attention")).toBeInTheDocument();
  expect(screen.getByText("Received but not yet filed into its experiment.")).toBeInTheDocument();
  expect(screen.getByText("PBMC_S1_L001_R1_001.fastq.gz")).toBeInTheDocument();
  expect(screen.getByText("Priya · PBMC pilot · UPL-7F3A21")).toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledWith("/api/files/uploads/pending", expect.anything());
});

test("Retry files it and removes the row", async () => {
  mockGet.mockResolvedValueOnce({ uploads: [UPLOAD] }).mockResolvedValue({ uploads: [] });
  mockPost.mockResolvedValue({ id: 9 });
  render(<UploadsAttentionPanel />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/files/uploads/u-1/retry", {}));
  await waitFor(() => expect(screen.queryByText("1 upload needs attention")).not.toBeInTheDocument());
});

test("nothing to show is no panel", async () => {
  mockGet.mockResolvedValue({ uploads: [] });
  const { container } = render(<UploadsAttentionPanel />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

test("roles without files edit see no panel", () => {
  canEdit = false;
  const { container } = render(<UploadsAttentionPanel />);
  expect(container).toBeEmptyDOMElement();
  expect(mockGet).not.toHaveBeenCalled();
});

const STOPPING = { ...UPLOAD, upload_id: "u-2", stage: "cancelling", state: "stopping", reference: "UPL-111111" };
const UNCONFIRMED = { ...UPLOAD, upload_id: "u-3", stage: "cancelling", state: "unconfirmed", reference: "UPL-222222" };

test("a cancelled upload still settling says so and offers nothing to do", async () => {
  mockGet.mockResolvedValue({ uploads: [STOPPING] });
  render(<UploadsAttentionPanel />);
  expect(await screen.findByText("Cancelled. Waiting for storage to confirm the upload stopped.")).toBeInTheDocument();
  expect(screen.queryByText("Received but not yet filed into its experiment.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test("an upload storage could not confirm can be released only by infrastructure configure", async () => {
  mockGet.mockResolvedValue({ uploads: [UNCONFIRMED] });
  const { unmount } = render(<UploadsAttentionPanel />);
  expect(await screen.findByText("Storage could not confirm this upload stopped.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
  unmount();

  canConfigure = true;
  render(<UploadsAttentionPanel />);
  expect(await screen.findByRole("button", { name: "Release" })).toBeInTheDocument();
});

test("releasing asks for a reason and an acknowledgement, and is sent once both are given", async () => {
  canConfigure = true;
  mockGet.mockResolvedValueOnce({ uploads: [UNCONFIRMED] }).mockResolvedValue({ uploads: [] });
  mockPost.mockResolvedValue(undefined);
  render(<UploadsAttentionPanel />);
  fireEvent.click(await screen.findByRole("button", { name: "Release" }));

  const dialog = await screen.findByRole("dialog", { name: "Release this upload?" });
  expect(dialog).toHaveTextContent(
    "bioAF could not confirm that PBMC_S1_L001_R1_001.fastq.gz stopped uploading. Releasing it lets Destroy Storage go ahead even though the transfer might still exist. This is recorded in the audit log.",
  );
  const release = within(dialog).getByRole("button", { name: "Release upload" });
  expect(release).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: "Laptop is off" } });
  expect(release).toBeDisabled();
  fireEvent.click(within(dialog).getByLabelText("I understand the transfer might still exist."));
  fireEvent.click(release);

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/storage/writes/u-3/release", {
      reason: "Laptop is off",
      acknowledge_transfer_may_exist: true,
    }),
  );
});
