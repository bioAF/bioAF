import { render, screen, waitFor, fireEvent } from "@testing-library/react";

const mockPush = jest.fn();
const mockGetParam = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => ({ get: (k: string) => mockGetParam(k) }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));
jest.mock("@/components/pipelines/RegistryBrowseModal", () => ({
  RegistryBrowseModal: () => null,
}));

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import PipelineCatalogPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

const builtIn = {
  id: 1,
  name: "bioAF System Test",
  pipeline_key: "bioaf-system-test",
  description: "Smoke test",
  version: "1.0.0",
  source_type: "built_in",
  custom_pipeline_id: null,
  latest_version_number: null,
  created_by_username: null,
};

const custom = {
  id: 2,
  name: "In-house RNA-seq",
  pipeline_key: "in-house-rnaseq",
  description: "Ours",
  version: null,
  source_type: "custom",
  custom_pipeline_id: 42,
  latest_version_number: 3,
  created_by_username: "bmills",
};

beforeEach(() => {
  mockPush.mockReset();
  mockGet.mockReset();
  mockGetParam.mockReset();
  mockGetParam.mockReturnValue(null);
  mockGet.mockResolvedValue({ pipelines: [builtIn, custom] });
});

async function renderCatalog() {
  render(<PipelineCatalogPage />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  await screen.findByText("bioAF System Test");
}

test("carries the experiment deep-link through to the built-in launch wizard", async () => {
  mockGetParam.mockImplementation((k: string) => (k === "experiment" ? "15" : null));
  await renderCatalog();

  fireEvent.click(screen.getAllByRole("button", { name: "Launch" })[0]);

  expect(mockPush).toHaveBeenCalledWith("/pipelines/launch/bioaf-system-test?experiment=15");
});

test("carries the experiment through when the card itself is opened", async () => {
  mockGetParam.mockImplementation((k: string) => (k === "experiment" ? "15" : null));
  await renderCatalog();

  fireEvent.click(screen.getByText("bioAF System Test"));

  expect(mockPush).toHaveBeenCalledWith("/pipelines/launch/bioaf-system-test?experiment=15");
});

test("carries the experiment through to a custom pipeline launch", async () => {
  mockGetParam.mockImplementation((k: string) => (k === "experiment" ? "15" : null));
  await renderCatalog();

  fireEvent.click(screen.getAllByRole("button", { name: "Launch" })[1]);

  expect(mockPush).toHaveBeenCalledWith("/pipelines/custom/42?launch=1&experiment=15");
});

test("adds no parameter when the catalog was opened without one", async () => {
  await renderCatalog();

  fireEvent.click(screen.getAllByRole("button", { name: "Launch" })[0]);

  expect(mockPush).toHaveBeenCalledWith("/pipelines/launch/bioaf-system-test");
});

test("ignores an experiment parameter that is not a number", async () => {
  mockGetParam.mockImplementation((k: string) => (k === "experiment" ? "not-an-id" : null));
  await renderCatalog();

  fireEvent.click(screen.getAllByRole("button", { name: "Launch" })[0]);

  expect(mockPush).toHaveBeenCalledWith("/pipelines/launch/bioaf-system-test");
});
