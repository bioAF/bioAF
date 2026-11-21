/**
 * Uploads received but not yet filed, listed for admins and comp_bio on Data & Files
 * (storage lifecycle 11F; signed-off mockup M12, decision M12-a).
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { UploadsAttentionPanel } from "@/components/files/UploadsAttentionPanel";

let canEdit = true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (r: string, a: string) => r === "files" && a === "edit" && canEdit,
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
