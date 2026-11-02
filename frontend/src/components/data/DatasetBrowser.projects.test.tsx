/**
 * Add to Project picks an existing project or creates one. A role that may not
 * list projects cannot pick, but creating is a separate grant.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import { DatasetBrowser } from "./DatasetBrowser";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "comp_bio" }),
}));
const mockCanAccess = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: mockCanAccess, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return projects();
    if (url.startsWith("/api/datasets/filter-options")) return Promise.resolve({ organisms: [] });
    if (url.startsWith("/api/datasets")) {
      return Promise.resolve({ experiments: [{ experiment_id: 3, experiment_name: "Exp One" }], total: 1 });
    }
    if (url.startsWith("/api/experiments/3")) return Promise.resolve({ samples: [{ id: 8 }] });
    return Promise.resolve({});
  });
}

const denied = () => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 }));

async function openAddToProject(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("checkbox", { name: "Select Exp One" }));
  await user.click(screen.getByRole("button", { name: /Add to Project \(1\)/ }));
  await screen.findByRole("dialog");
}

const submit = () => screen.getByRole("button", { name: "Add to Project" });

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockCanAccess.mockReset();
  mockCanAccess.mockReturnValue(true);
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("a 403 with create permission explains and still creates a project", async () => {
  const user = userEvent.setup();
  givenProjects(denied);
  mockPost.mockResolvedValue({ id: 50 });
  render(<DatasetBrowser />);
  await openAddToProject(user);

  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Select Project")).not.toBeInTheDocument();
  expect(submit()).toBeDisabled();

  await user.type(screen.getByLabelText("New Project Name"), "Fresh");
  await user.click(submit());

  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/projects/50/samples", { sample_ids: [8] }));
  expect(mockPost).toHaveBeenCalledWith("/api/projects", { name: "Fresh" });
});

test("a 403 without create permission leaves nothing to submit", async () => {
  const user = userEvent.setup();
  givenProjects(denied);
  mockCanAccess.mockImplementation((resource: string, action: string) => !(resource === "projects" && action === "create"));
  render(<DatasetBrowser />);
  await openAddToProject(user);

  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();
  expect(screen.queryByLabelText("New Project Name")).not.toBeInTheDocument();
  expect(submit()).toBeDisabled();
});

test("a failure keeps its refusal, so a duplicate cannot be created", async () => {
  const user = userEvent.setup();
  givenProjects(() => Promise.reject(new Error("Backend unavailable")));
  render(<DatasetBrowser />);
  await openAddToProject(user);

  expect(screen.getByTestId("projects-load-failed")).toBeInTheDocument();
  expect(screen.queryByLabelText("New Project Name")).not.toBeInTheDocument();
  expect(submit()).toBeDisabled();
});

test("a loaded list still offers its projects", async () => {
  const user = userEvent.setup();
  givenProjects(() => Promise.resolve({ projects: [{ id: 2, name: "Atlas" }] }));
  render(<DatasetBrowser />);
  await openAddToProject(user);

  expect(screen.getByRole("option", { name: "Atlas" })).toBeInTheDocument();
});

test("reopening after a denial that lifted returns to the picker", async () => {
  const user = userEvent.setup();
  const projects = jest
    .fn()
    .mockImplementationOnce(denied)
    .mockResolvedValue({ projects: [{ id: 2, name: "Atlas" }] });
  givenProjects(projects);
  render(<DatasetBrowser />);
  await openAddToProject(user);
  await user.click(screen.getByRole("button", { name: "Cancel" }));

  await user.click(screen.getByRole("button", { name: /Add to Project \(1\)/ }));

  expect(await screen.findByRole("option", { name: "Atlas" })).toBeInTheDocument();
  expect(screen.queryByText(/does not include viewing projects/)).not.toBeInTheDocument();
});
