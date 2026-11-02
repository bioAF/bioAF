/**
 * A launch needs an experiment or a project, and the dialog is project-first.
 * Losing the project list must not lose experiment selection, the deep link
 * from an experiment, or a launch the role is otherwise allowed.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/components/notebooks/FileTreeSelector", () => ({
  FileTreeSelector: ({ onSelectionChange }: { onSelectionChange: (ids: number[]) => void }) => (
    <button onClick={() => onSelectionChange([300])}>Pick file</button>
  ),
}));
jest.mock("@/components/references/ReferencePicker", () => ({ ReferencePicker: () => null }));

import { CustomPipelineLaunchDialog } from "./CustomPipelineLaunchDialog";
import { api } from "@/lib/api";
import type { CustomPipelineDetail } from "@/lib/types";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const pipeline = {
  id: 42,
  name: "In-house RNA-seq",
  versions: [{ id: 9, version_number: 3, status: "active", variables: [], created_at: "2026-01-01T00:00:00Z" }],
} as unknown as CustomPipelineDetail;

const denied = () => Object.assign(new Error("role_missing"), { status: 403 });
const atlas = { id: 7, name: "Atlas" };

function given(projects: () => Promise<unknown>, overrides: Record<string, () => Promise<unknown>> = {}) {
  mockGet.mockImplementation((url: string) => {
    for (const [fragment, value] of Object.entries(overrides)) {
      if (url.includes(fragment)) return value();
    }
    if (url.startsWith("/api/projects")) return projects();
    if (url.startsWith("/api/experiments?")) {
      return Promise.resolve({
        experiments: [
          { id: 15, name: "GSE309060", project: atlas },
          { id: 16, name: "Unfiled", project: null },
        ],
      });
    }
    if (url.startsWith("/api/experiments/")) {
      return Promise.resolve({ id: 15, name: "GSE309060", project: atlas });
    }
    if (url.startsWith("/api/files")) {
      return Promise.resolve({ files: [{ id: 300, filename: "a.fastq.gz", sample_ids: [] }] });
    }
    return Promise.resolve({});
  });
}

function open(props: Record<string, unknown> = {}) {
  return render(
    <CustomPipelineLaunchDialog
      pipeline={pipeline}
      envOptionsById={new Map()}
      repoById={new Map()}
      onClose={jest.fn()}
      onLaunched={jest.fn()}
      {...props}
    />,
  );
}

const projectSelect = () => screen.getByLabelText("Project (optional)") as HTMLSelectElement;
const experimentSelect = () => screen.getByLabelText("Experiment (optional)") as HTMLSelectElement;

async function launch() {
  fireEvent.click(await screen.findByRole("button", { name: "Pick file" }));
  fireEvent.click(screen.getByRole("button", { name: "Launch" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  return mockPost.mock.calls[0][1];
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockResolvedValue({ id: 1 });
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("a 403 disables the project filter and lists every experiment", async () => {
  given(() => Promise.reject(denied()));
  open();

  await waitFor(() => expect(projectSelect()).toBeDisabled());
  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();
  await waitFor(() => expect(experimentSelect()).toBeEnabled());
  expect(await screen.findByRole("option", { name: "GSE309060" })).toBeInTheDocument();
  expect(screen.queryByText("Select a project first")).not.toBeInTheDocument();
});

test("a chosen experiment launches with the project it belongs to", async () => {
  given(() => Promise.reject(denied()));
  open();

  await screen.findByRole("option", { name: "GSE309060" });
  fireEvent.change(experimentSelect(), { target: { value: "15" } });

  expect(await launch()).toMatchObject({ experiment_id: 15, project_id: 7, input_file_ids: [300] });
});

test("an experiment with no project launches with no project", async () => {
  given(() => Promise.reject(denied()));
  open();

  await screen.findByRole("option", { name: "Unfiled" });
  fireEvent.change(experimentSelect(), { target: { value: "16" } });

  expect(await launch()).toMatchObject({ experiment_id: 16, project_id: null });
});

test("a deep link still selects its experiment under a 403", async () => {
  given(() => Promise.reject(denied()));
  open({ initialExperimentId: 15 });

  await waitFor(() => expect(experimentSelect().value).toBe("15"));
  expect(projectSelect()).toBeDisabled();
  expect(await launch()).toMatchObject({ experiment_id: 15, project_id: 7 });
});

test("a deep link survives an experiment beyond the listed page", async () => {
  given(() => Promise.reject(denied()), {
    "/api/experiments?": () => Promise.resolve({ experiments: [{ id: 16, name: "Unfiled", project: null }] }),
  });
  open({ initialExperimentId: 15 });

  await waitFor(() => expect(experimentSelect().value).toBe("15"));
  expect(screen.getByRole("option", { name: "GSE309060" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Unfiled" })).toBeInTheDocument();
});

test("a deep link to an experiment with no project is honoured under a 403", async () => {
  given(() => Promise.reject(denied()), {
    "/api/experiments/15": () => Promise.resolve({ id: 15, name: "GSE309060", project: null }),
  });
  open({ initialExperimentId: 15 });

  await waitFor(() => expect(experimentSelect().value).toBe("15"));
});

test("a failure is visible, and keeps experiment selection and the deep link", async () => {
  given(() => Promise.reject(new Error("Backend unavailable")));
  open({ initialExperimentId: 15 });

  expect(await screen.findByText(/Projects could not be loaded/)).toBeInTheDocument();
  await waitFor(() => expect(experimentSelect().value).toBe("15"));
});

test("a retry that succeeds keeps the chosen experiment and restores its project", async () => {
  const projects = jest
    .fn()
    .mockRejectedValueOnce(new Error("Backend unavailable"))
    .mockResolvedValue({ projects: [atlas] });
  given(projects);
  open({ initialExperimentId: 15 });
  await waitFor(() => expect(experimentSelect().value).toBe("15"));

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  await waitFor(() => expect(projectSelect().value).toBe("7"));
  await waitFor(() => expect(experimentSelect().value).toBe("15"));
});

test("a role that may list neither is told so", async () => {
  given(() => Promise.reject(denied()), { "/api/experiments?": () => Promise.reject(denied()) });
  open();

  expect(await screen.findByText(/does not include viewing experiments/)).toBeInTheDocument();
});

test("experiments that fail to load are reported", async () => {
  given(() => Promise.reject(denied()), {
    "/api/experiments?": () => Promise.reject(new Error("Backend unavailable")),
  });
  open();

  expect(await screen.findByText(/Experiments could not be loaded/)).toBeInTheDocument();
});
