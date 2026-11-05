/**
 * The launch endpoint answers a compute refusal with HTTP 200 and the failed run, so the dialog
 * must hand that run to the caller like any other: the run page is where the failure is explained.
 * A request that errors (validation, permissions, quota) has no run to show and stays in the dialog.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/components/notebooks/FileTreeSelector", () => ({
  FileTreeSelector: ({
    onSelectionChange,
  }: {
    onSelectionChange: (ids: number[]) => void;
  }) => <button onClick={() => onSelectionChange([300])}>Pick file</button>,
}));
jest.mock("@/components/references/ReferencePicker", () => ({
  ReferencePicker: () => null,
}));

import { CustomPipelineLaunchDialog } from "./CustomPipelineLaunchDialog";
import { api } from "@/lib/api";
import type { CustomPipelineDetail } from "@/lib/types";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const pipeline = {
  id: 42,
  name: "In-house RNA-seq",
  versions: [
    {
      id: 9,
      version_number: 3,
      status: "active",
      variables: [],
      created_at: "2026-01-01T00:00:00Z",
    },
  ],
} as unknown as CustomPipelineDetail;

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects"))
      return Promise.resolve({ projects: [{ id: 7, name: "Atlas" }] });
    if (url.startsWith("/api/experiments?")) {
      return Promise.resolve({
        experiments: [
          { id: 15, name: "GSE309060", project: { id: 7, name: "Atlas" } },
        ],
      });
    }
    if (url.startsWith("/api/experiments/")) {
      return Promise.resolve({
        id: 15,
        name: "GSE309060",
        project: { id: 7, name: "Atlas" },
      });
    }
    if (url.startsWith("/api/files")) {
      return Promise.resolve({
        files: [{ id: 300, filename: "a.fastq.gz", sample_ids: [] }],
      });
    }
    return Promise.resolve({});
  });
});

async function launchFromExperiment(onLaunched: jest.Mock) {
  render(
    <CustomPipelineLaunchDialog
      pipeline={pipeline}
      envOptionsById={new Map()}
      repoById={new Map()}
      onClose={jest.fn()}
      onLaunched={onLaunched}
      initialExperimentId={15}
    />,
  );
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Experiment (optional)") as HTMLSelectElement)
        .value,
    ).toBe("15"),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Pick file" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Launch" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Launch" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalled());
}

test("a run the cluster refused is handed to the caller so the user reaches its failure details", async () => {
  mockPost.mockResolvedValue({
    id: 88,
    status: "failed",
    failure_reason: "submission_failed",
    error_message:
      "bioAF could not confirm that the compute cluster accepted this run, so it is marked failed.",
  });
  const onLaunched = jest.fn();

  await launchFromExperiment(onLaunched);

  await waitFor(() => expect(onLaunched).toHaveBeenCalledWith(88));
});

test("a request error stays in the dialog and is shown", async () => {
  mockPost.mockRejectedValue(
    new Error("Quota exceeded: Would exceed monthly limit"),
  );
  const onLaunched = jest.fn();

  await launchFromExperiment(onLaunched);

  expect(await screen.findByText(/Quota exceeded/)).toBeInTheDocument();
  expect(onLaunched).not.toHaveBeenCalled();
});
