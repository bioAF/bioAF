import { render, screen, fireEvent, waitFor } from "@/testing/renderWithProviders";
import CellxgenePage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/results/cellxgene",
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin", email: "admin@test.com" }),
  isAuthenticated: () => true,
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

const FILE = {
  id: 7,
  filename: "pbmc.h5ad",
  size_bytes: 1024,
  source_type: "pipeline_output",
  experiment_name: "Exp A",
  project_name: "Proj A",
  sample_names: [],
};

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  (console.error as jest.Mock).mockRestore?.();
});

/** Route every GET the page makes, with `inspect` under our control. */
function routeApi(inspect: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/cellxgene/publishable-files")) return Promise.resolve([FILE]);
    if (url.includes("/api/cellxgene/inspect/")) return inspect();
    if (url.includes("/api/cellxgene")) return Promise.resolve([]); // publications list
    return Promise.resolve({ experiments: [], total: 0 });
  });
}

async function openPublishForm() {
  render(<CellxgenePage />);
  const publish = await screen.findByRole("button", { name: /publish dataset/i });
  fireEvent.click(publish);
  return await screen.findByText("pbmc.h5ad");
}

// The catch used to fabricate an inspection result:
//   { embeddings: [], cell_count: 0, cellxgene_ready: false,
//     missing: "unable to inspect file" }
// The panel renders that as "Not ready for cellxgene. Missing: unable to inspect file.
// This file needs secondary analysis (normalization, PCA, UMAP) before it can be viewed
// in cellxgene." A dropped connection was therefore presented as a scientific finding
// about the user's h5ad, and Publish was disabled on the strength of it.
test("a failed inspect is not reported as a finding about the file", async () => {
  routeApi(() => Promise.reject(new Error("network down")));
  const row = await openPublishForm();
  fireEvent.click(row);

  expect(await screen.findByTestId("cellxgene-inspect-failed")).toBeInTheDocument();
  expect(screen.queryByText(/not ready for cellxgene/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/needs secondary analysis/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/unable to inspect file/i)).not.toBeInTheDocument();
});

test("the real error goes to the logs", async () => {
  routeApi(() => Promise.reject(new Error("network down")));
  const row = await openPublishForm();
  fireEvent.click(row);

  await screen.findByTestId("cellxgene-inspect-failed");
  expect(console.error).toHaveBeenCalledWith(
    expect.stringContaining("cellxgene compatibility"),
    expect.any(Error)
  );
});

test("a genuine not-ready verdict from the server still renders", async () => {
  routeApi(() =>
    Promise.resolve({
      embeddings: [],
      cell_count: 500,
      gene_count: 20,
      cellxgene_ready: false,
      missing: "X_umap",
    })
  );
  const row = await openPublishForm();
  fireEvent.click(row);

  expect(await screen.findByText(/not ready for cellxgene/i)).toBeInTheDocument();
  expect(screen.getByText(/X_umap/)).toBeInTheDocument();
  expect(screen.queryByTestId("cellxgene-inspect-failed")).not.toBeInTheDocument();
});

test("retrying after a failure can succeed", async () => {
  let attempt = 0;
  routeApi(() => {
    attempt += 1;
    return attempt === 1
      ? Promise.reject(new Error("network down"))
      : Promise.resolve({
          embeddings: ["X_umap"],
          cell_count: 500,
          gene_count: 20,
          cellxgene_ready: true,
          missing: null,
        });
  });
  const row = await openPublishForm();
  fireEvent.click(row);

  fireEvent.click(await screen.findByRole("button", { name: /retry/i }));

  expect(await screen.findByText(/ready for cellxgene/i)).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.queryByTestId("cellxgene-inspect-failed")).not.toBeInTheDocument()
  );
});
