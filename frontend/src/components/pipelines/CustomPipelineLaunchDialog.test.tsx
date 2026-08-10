import { render, screen, waitFor } from "@testing-library/react";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/components/notebooks/FileTreeSelector", () => ({
  FileTreeSelector: () => null,
}));
jest.mock("@/components/references/ReferencePicker", () => ({
  ReferencePicker: () => null,
}));

import { CustomPipelineLaunchDialog } from "./CustomPipelineLaunchDialog";
import { api } from "@/lib/api";
import type { CustomPipelineDetail } from "@/lib/types";

const mockGet = api.get as jest.Mock;

const pipeline = {
  id: 42,
  name: "In-house RNA-seq",
  description: null,
  pipeline_key: "in-house-rnaseq",
  created_by_user_id: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  versions: [
    {
      id: 9,
      version_number: 3,
      code_source_type: "code_blob",
      github_repo_id: null,
      code_content: "echo hi",
      entrypoint_command: "bash run.sh",
      environment_version_id: 5,
      cpu_request: "2",
      memory_request: "8Gi",
      log_file_path: null,
      version_trigger: "manual",
      status: "active",
      created_by_user_id: 1,
      created_at: "2026-01-01T00:00:00Z",
      variables: [],
      qc_template: null,
      qc_config_json: null,
    },
  ],
} as unknown as CustomPipelineDetail;

// The dialog fans out to projects, experiments and files. Route by url so each
// test only has to say what is different about its world.
function respondWith(overrides: Record<string, unknown> = {}) {
  mockGet.mockImplementation((url: string) => {
    for (const [fragment, value] of Object.entries(overrides)) {
      if (url.includes(fragment)) {
        return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
      }
    }
    if (url.startsWith("/api/projects")) {
      return Promise.resolve({ projects: [{ id: 7, name: "Reproduction" }] });
    }
    if (url.startsWith("/api/experiments?")) {
      return Promise.resolve({ experiments: [{ id: 15, name: "GSE309060" }] });
    }
    if (url.startsWith("/api/experiments/")) {
      return Promise.resolve({ id: 15, name: "GSE309060", project: { id: 7, name: "Reproduction" } });
    }
    if (url.startsWith("/api/files")) return Promise.resolve({ files: [] });
    return Promise.resolve({});
  });
}

function renderDialog(props: Record<string, unknown> = {}) {
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

function projectSelect() {
  return screen.getByLabelText("Project (optional)") as HTMLSelectElement;
}

function experimentSelect() {
  return screen.getByLabelText("Experiment (optional)") as HTMLSelectElement;
}

beforeEach(() => {
  mockGet.mockReset();
  respondWith();
});

test("preselects the experiment the user launched from, and its project", async () => {
  renderDialog({ initialExperimentId: 15 });

  await waitFor(() => expect(experimentSelect().value).toBe("15"));
  expect(projectSelect().value).toBe("7");
  expect(experimentSelect()).toBeEnabled();
});

test("selects nothing when no experiment was carried in", async () => {
  renderDialog();

  await waitFor(() => expect(projectSelect()).toBeInTheDocument());
  expect(projectSelect().value).toBe("");
  expect(experimentSelect().value).toBe("");
});

test("leaves both pickers empty when the experiment cannot be looked up", async () => {
  respondWith({ "/api/experiments/15": new Error("boom") });
  renderDialog({ initialExperimentId: 15 });

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(projectSelect().value).toBe("");
  expect(experimentSelect().value).toBe("");
});

test("leaves both pickers empty when the experiment belongs to no project", async () => {
  respondWith({ "/api/experiments/15": { id: 15, name: "GSE309060", project: null } });
  renderDialog({ initialExperimentId: 15 });

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(projectSelect().value).toBe("");
  expect(experimentSelect().value).toBe("");
});

test("a manual project change after preselection clears the experiment as before", async () => {
  renderDialog({ initialExperimentId: 15 });
  await waitFor(() => expect(experimentSelect().value).toBe("15"));

  const { fireEvent } = await import("@testing-library/react");
  fireEvent.change(projectSelect(), { target: { value: "" } });

  await waitFor(() => expect(experimentSelect().value).toBe(""));
});
