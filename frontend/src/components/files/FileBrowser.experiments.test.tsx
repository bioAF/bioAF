/**
 * Both file-browser experiment pickers reach every experiment, a page at a time (#104): the list filter,
 * and the Associate dialog, whose experiments are narrowed to the chosen project on the server.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import { FileBrowser } from "./FileBrowser";

jest.mock("@/lib/auth", () => ({ getCurrentUser: () => ({ role_name: "bench" }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  fileContentUrl: (id: number) => Promise.resolve(`/api/files/${id}/content`),
}));
import { fireEvent, within } from "@/testing/renderWithProviders";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
const many = Array.from({ length: 180 }, (_, i) => ({ id: 500 + i, name: `Deep ${i}`, project: { id: 3, name: "P3" } }));
const unlinked = {
  id: 77, filename: "raw.fastq", storage_uri: "gs://b/raw.fastq", gcs_uri: "gs://b/raw.fastq", size_bytes: 1,
  md5_checksum: null, file_type: "fastq", tags: [], uploader: null, project_id: null, experiment_id: null,
  sample_ids: [], source_type: "upload", source_pipeline_run_id: null, source_notebook_session_id: null,
  storage_deleted: false, upload_timestamp: "2026-01-01T00:00:00Z", created_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset().mockResolvedValue({});
  mockGet.mockImplementation((url: string) => {
    const u = new URL(url, "http://x");
    if (u.pathname === "/api/projects") return Promise.resolve({ projects: [{ id: 3, name: "P3" }] });
    if (u.pathname === "/api/experiments") {
      const name = u.searchParams.get("name") ?? "";
      const project = u.searchParams.get("project_id");
      const page = Number(u.searchParams.get("page"));
      const matching = many.filter((e) => e.name.includes(name) && (!project || String(e.project.id) === project));
      return Promise.resolve({ experiments: matching.slice((page - 1) * 100, page * 100), total: matching.length });
    }
    if (u.pathname.endsWith("/samples")) return Promise.resolve([]);
    return Promise.resolve({ files: [unlinked], total: 1, page: 1, page_size: 25, items: [] });
  });
});

test("the experiment filter reaches an experiment past the first hundred", async () => {
  render(<FileBrowser showExperimentFilter />);
  const search = (await screen.findAllByRole("searchbox", { name: "Search names" }))[0];
  fireEvent.change(search, { target: { value: "Deep 150" } });
  await screen.findByRole("option", { name: "Deep 150" });
  fireEvent.change(screen.getByLabelText("Filter by experiment id"), { target: { value: "650" } });
  await waitFor(() =>
    expect(mockGet.mock.calls.some(([u]) => String(u).includes("/api/files") && String(u).includes("experiment_id=650"))).toBe(true),
  );
});

test("the Associate dialog links to an experiment past the first hundred in the chosen project", async () => {
  render(<FileBrowser />);
  fireEvent.click(await screen.findByRole("button", { name: "Associate" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Project"), { target: { value: "3" } });
  await waitFor(() => expect(mockGet.mock.calls.some(([u]) => String(u).includes("project_id=3"))).toBe(true));
  fireEvent.change(within(dialog).getByRole("searchbox", { name: "Search names" }), { target: { value: "Deep 170" } });
  await within(dialog).findByRole("option", { name: "Deep 170" });
  fireEvent.change(within(dialog).getByLabelText("Experiment"), { target: { value: "670" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/files/77/link", { project_id: 3, experiment_id: 670 }));
});
