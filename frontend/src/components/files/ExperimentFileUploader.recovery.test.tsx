/**
 * Upload outcomes the uploader explains (storage lifecycle 11F; signed-off mockups M11 and M12):
 * received but not yet filed, filing retries exhausted (Retry now / Cancel upload), and uploads
 * paused while storage is being changed. Raw storage errors are never shown.
 */
import { act, render, screen, waitFor, within } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import { ExperimentFileUploader } from "./ExperimentFileUploader";

jest.mock("@/lib/api", () => ({
  ApiError: jest.requireActual("@/lib/api").ApiError,
  api: {
    get: jest.fn(),
    post: jest.fn(),
    uploadSigned: jest.fn(),
    uploadProxied: jest.fn(),
  },
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => false, capabilities: {}, loading: false }),
}));

import { api, ApiError } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
const mockUpload = api.uploadProxied as jest.Mock;

const PENDING_202 = {
  detail:
    "Received, not yet filed. Your file is stored safely but could not be added to PBMC pilot yet. bioAF will try again automatically. Reference UPL-7F3A21.",
  code: "upload_received_not_filed",
  details: { upload_id: "u-1", reference: "UPL-7F3A21", experiment_id: 42 },
};

function pendingList(state: "retrying" | "needs_attention" | null) {
  return {
    uploads: state
      ? [
          {
            upload_id: "u-1",
            filename: "PBMC_S1_L001_R1_001.fastq.gz",
            reference: "UPL-7F3A21",
            stage: "received",
            state,
            attempts: 4,
            experiment_id: 42,
            experiment_name: "PBMC pilot",
            created_at: "2026-11-21T15:00:00Z",
          },
        ]
      : [],
  };
}

let pendingState: "retrying" | "needs_attention" | null = "retrying";

/** The upload's explicit outcome; null means an automatic retry filed it. */
function outcomeOf(state: "retrying" | "needs_attention" | null) {
  return state
    ? { upload_id: "u-1", reference: "UPL-7F3A21", stage: "received", outcome: "pending", state }
    : { upload_id: "u-1", reference: "UPL-7F3A21", stage: "filed", outcome: "filed", file_id: 9 };
}

beforeEach(() => {
  jest.useFakeTimers({ advanceTimers: true });
  mockGet.mockReset();
  mockPost.mockReset();
  mockUpload.mockReset();
  pendingState = "retrying";
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/files/uploads/u-1") return Promise.resolve(outcomeOf(pendingState));
    if (url === "/api/files/uploads/pending") return Promise.resolve(pendingList(pendingState));
    if (url === "/api/experiments/42") return Promise.resolve({ id: 42, name: "PBMC pilot" });
    return Promise.resolve({});
  });
});

afterEach(() => jest.useRealTimers());

/** The uploaded file's row: the panel's own toggle is also labelled "Cancel upload". */
function fileRow(): HTMLElement {
  return screen.getByText("PBMC_S1_L001_R1_001.fastq.gz").closest("li") as HTMLElement;
}

async function uploadOne(user: ReturnType<typeof userEvent.setup>) {
  render(<ExperimentFileUploader experimentId={42} samples={[]} onUploaded={() => {}} />);
  await user.click(screen.getByRole("button", { name: /upload/i }));
  const input = screen.getByTestId("upload-file-input") as HTMLInputElement;
  await user.upload(input, new File(["x"], "PBMC_S1_L001_R1_001.fastq.gz", { type: "" }));
  await user.click(screen.getByRole("button", { name: /upload 1 file/i }));
}

test("received but not yet filed is explained, never shown as done or failed (M12)", async () => {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  mockUpload.mockResolvedValue(PENDING_202);
  await uploadOne(user);

  expect(await screen.findByText(PENDING_202.detail)).toBeInTheDocument();
  expect(screen.getByText("Not filed yet")).toBeInTheDocument();
  expect(screen.queryByText("Done")).not.toBeInTheDocument();
});

test("when retries run out the uploader can retry or cancel (M12)", async () => {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  mockUpload.mockResolvedValue(PENDING_202);
  pendingState = "needs_attention";
  await uploadOne(user);
  await screen.findByText("Not filed yet");

  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(
    await screen.findByText(
      "Could not be filed. Your file is still stored. Retry now, or cancel the upload to remove it. Reference UPL-7F3A21.",
    ),
  ).toBeInTheDocument();

  mockPost.mockResolvedValueOnce({ id: 9 });
  await user.click(within(fileRow()).getByRole("button", { name: "Retry now" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/files/uploads/u-1/retry", {}));
  expect(await screen.findByText("Done")).toBeInTheDocument();
});

test("cancelling asks first and removes the stored copy (M12)", async () => {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  mockUpload.mockResolvedValue(PENDING_202);
  pendingState = "needs_attention";
  await uploadOne(user);
  await screen.findByText("Not filed yet");
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  await screen.findByRole("button", { name: "Retry now" });

  mockPost.mockResolvedValueOnce(undefined);
  await user.click(within(fileRow()).getByRole("button", { name: "Cancel upload" }));
  const confirm = await screen.findByRole("dialog");
  expect(confirm).toHaveTextContent("Remove this upload?");
  expect(confirm).toHaveTextContent("The stored copy is deleted and nothing is added to PBMC pilot.");
  await user.click(within(confirm).getByRole("button", { name: /remove/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/files/uploads/u-1/cancel", {}));
  expect(await screen.findByText("Cancelled")).toBeInTheDocument();
});

test("a file filed by an automatic retry turns done", async () => {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  mockUpload.mockResolvedValue(PENDING_202);
  await uploadOne(user);
  await screen.findByText("Not filed yet");

  pendingState = null;
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(await screen.findByText("Done")).toBeInTheDocument();
});

test("paused uploads are not sent and say why (M11)", async () => {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  mockUpload.mockRejectedValue(
    new ApiError(
      409,
      "Uploads are paused. An administrator is changing storage. Your files have not been sent. Try again later.",
      "uploads_paused",
    ),
  );
  await uploadOne(user);

  expect(
    await screen.findByText(
      "Uploads are paused. An administrator is changing storage. Your files have not been sent. Try again later.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByText("Not sent")).toBeInTheDocument();
});

test("an unusable upload store is not sent and says why (11F)", async () => {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  const message =
    "Uploads are unavailable because bioAF cannot reach its upload storage. Your files have not been sent. Ask an administrator to check Storage under Infrastructure > Components.";
  mockUpload.mockRejectedValue(new ApiError(409, message, "upload_storage_unavailable"));
  await uploadOne(user);

  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.getByText("Not sent")).toBeInTheDocument();
});


test("a file storage has not confirmed yet shows Confirming upload, not an error (W8)", async () => {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  mockUpload.mockImplementation((_file: File, options: { onConfirming?: () => void }) => {
    options.onConfirming?.();
    return new Promise(() => {});
  });
  await uploadOne(user);

  expect(await screen.findByText("Confirming upload")).toBeInTheDocument();
  expect(screen.getByText("bioAF is checking that your file has finished uploading.")).toBeInTheDocument();
  expect(screen.queryByText("Failed")).not.toBeInTheDocument();
});
