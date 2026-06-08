import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExperimentFileUploader } from "./ExperimentFileUploader";

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    uploadSigned: jest.fn(),
    uploadProxied: jest.fn(),
  },
}));

const mockHas = jest.fn();
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({
    has: (flag: string) => mockHas(flag),
    capabilities: {},
    loading: false,
  }),
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockUploadSigned = api.uploadSigned as jest.Mock;
const mockUploadProxied = api.uploadProxied as jest.Mock;

const SAMPLES = [
  { id: 10, label: "S010" },
  { id: 11, label: "S011" },
];

beforeEach(() => {
  mockGet.mockReset();
  mockUploadSigned.mockReset();
  mockUploadProxied.mockReset();
  mockHas.mockReset();
  mockHas.mockReturnValue(true);
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/experiments/42") {
      return Promise.resolve({
        id: 42,
        name: "Exp Alpha",
        code: "EXP-A",
        project: { id: 7, name: "Proj One" },
      });
    }
    if (url.startsWith("/api/projects/7")) {
      return Promise.resolve({ id: 7, name: "Proj One", code: "P7" });
    }
    return Promise.resolve({});
  });
});

test("uploads via the proxied path when the backend lacks signed_url_upload", async () => {
  const user = userEvent.setup();
  mockHas.mockImplementation((flag: string) => flag !== "signed_url_upload");
  mockUploadProxied.mockResolvedValue({ id: 1 });
  render(
    <ExperimentFileUploader
      experimentId={42}
      samples={SAMPLES}
      onUploaded={() => {}}
    />,
  );
  // The uploader still works (no dead control); it expands and uploads.
  await user.click(screen.getByRole("button", { name: /upload/i }));
  expect(screen.getByText(/drag & drop/i)).toBeInTheDocument();

  const input = screen.getByTestId("upload-file-input") as HTMLInputElement;
  await user.upload(input, new File(["x"], "reads.fastq.gz", { type: "" }));
  await user.click(screen.getByRole("button", { name: /upload 1 file/i }));

  await waitFor(() => expect(mockUploadProxied).toHaveBeenCalledTimes(1));
  expect(mockUploadSigned).not.toHaveBeenCalled();
});

test("renders an Upload toggle button collapsed by default", () => {
  render(
    <ExperimentFileUploader
      experimentId={42}
      samples={SAMPLES}
      onUploaded={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: /upload/i })).toBeInTheDocument();
  // panel hidden when collapsed
  expect(screen.queryByText(/drag & drop/i)).not.toBeInTheDocument();
});

test("expands the upload panel when the toggle button is clicked", async () => {
  const user = userEvent.setup();
  render(
    <ExperimentFileUploader
      experimentId={42}
      samples={SAMPLES}
      onUploaded={() => {}}
    />,
  );
  await user.click(screen.getByRole("button", { name: /upload/i }));
  expect(screen.getByText(/drag & drop/i)).toBeInTheDocument();
});

test("association scope is locked to experiment with optional sample", async () => {
  const user = userEvent.setup();
  render(
    <ExperimentFileUploader
      experimentId={42}
      samples={SAMPLES}
      onUploaded={() => {}}
    />,
  );
  await user.click(screen.getByRole("button", { name: /upload/i }));

  // No project / experiment / global selectors -- scope is fixed to this experiment
  expect(screen.queryByLabelText(/project/i)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/^experiment$/i)).not.toBeInTheDocument();

  // The sample selector lists this experiment's samples plus a "whole experiment" option
  const sampleSelect = screen.getByLabelText(/sample/i) as HTMLSelectElement;
  expect(sampleSelect).toBeInTheDocument();
  const optionLabels = Array.from(sampleSelect.options).map((o) => o.text);
  expect(optionLabels).toEqual(
    expect.arrayContaining(["Whole experiment", "S010", "S011"]),
  );
});

test("uploadAll passes experimentId and optional sampleId", async () => {
  const user = userEvent.setup();
  mockUploadSigned.mockResolvedValue({ id: 99 });
  const onUploaded = jest.fn();
  render(
    <ExperimentFileUploader
      experimentId={42}
      samples={SAMPLES}
      onUploaded={onUploaded}
    />,
  );
  await user.click(screen.getByRole("button", { name: /upload/i }));

  // Pick a sample
  await user.selectOptions(screen.getByLabelText(/sample/i), "10");

  // Add a file via the hidden input
  const input = screen.getByTestId("upload-file-input") as HTMLInputElement;
  const file = new File(["content"], "reads.fastq.gz", { type: "" });
  await user.upload(input, file);

  await user.click(screen.getByRole("button", { name: /upload 1 file/i }));

  await waitFor(() => {
    expect(mockUploadSigned).toHaveBeenCalledTimes(1);
  });
  const [, opts] = mockUploadSigned.mock.calls[0];
  expect(opts.experimentId).toBe(42);
  expect(opts.sampleId).toBe(10);
  expect(opts.isGlobal).toBeUndefined();
  expect(onUploaded).toHaveBeenCalled();
});

test("uploads with no sample associate to the experiment only", async () => {
  const user = userEvent.setup();
  mockUploadSigned.mockResolvedValue({ id: 99 });
  render(
    <ExperimentFileUploader
      experimentId={42}
      samples={SAMPLES}
      onUploaded={() => {}}
    />,
  );
  await user.click(screen.getByRole("button", { name: /upload/i }));

  const input = screen.getByTestId("upload-file-input") as HTMLInputElement;
  const file = new File(["x"], "notes.txt", { type: "" });
  await user.upload(input, file);

  await user.click(screen.getByRole("button", { name: /upload 1 file/i }));

  await waitFor(() => {
    expect(mockUploadSigned).toHaveBeenCalledTimes(1);
  });
  const [, opts] = mockUploadSigned.mock.calls[0];
  expect(opts.experimentId).toBe(42);
  expect(opts.sampleId).toBeUndefined();
});
