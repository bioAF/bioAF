/**
 * The picker is project-first, but associating with an experiment needs no
 * project. Losing the project list must not lose that.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AssociatePaperModal } from "./AssociatePaperModal";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return { ...actual, literature: { ...actual.literature, addAssociation: jest.fn() } };
});

import { api } from "@/lib/api";
import { literature } from "@/lib/literature";
const mockGet = api.get as jest.Mock;
const mockAssoc = literature.addAssociation as jest.Mock;

const denied = () => Object.assign(new Error("role_missing"), { status: 403 });
const allExperiments = { experiments: [{ id: 101, name: "Exp One" }], total: 1 };

function given(projects: () => Promise<unknown>, experiments: () => Promise<unknown> = () => Promise.resolve(allExperiments)) {
  mockGet.mockImplementation((url: string) =>
    url.startsWith("/api/projects") ? projects() : experiments(),
  );
}

function open() {
  return render(<AssociatePaperModal paperIds={[5]} onClose={jest.fn()} onAssociated={jest.fn()} />);
}

const projectSelect = () => screen.getByLabelText("Project");
const experimentSelect = () => screen.getByLabelText("Experiment (optional)");
const associate = () => screen.getByRole("button", { name: /^Associate$/ });

beforeEach(() => {
  mockGet.mockReset();
  mockAssoc.mockReset();
  mockAssoc.mockResolvedValue({});
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("a 403 keeps experiment association, listing experiments without a project", async () => {
  given(() => Promise.reject(denied()));
  open();

  await waitFor(() => expect(projectSelect()).toBeDisabled());
  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledWith("/api/experiments?page_size=100");

  await userEvent.selectOptions(experimentSelect(), await screen.findByRole("option", { name: "Exp One" }));
  await userEvent.click(associate());

  await waitFor(() => expect(mockAssoc).toHaveBeenCalledWith(5, { scope_type: "experiment", scope_id: 101 }));
});

test("a failure is visible and experiment association still works", async () => {
  given(() => Promise.reject(new Error("Backend unavailable")));
  open();

  expect(await screen.findByText(/Projects could not be loaded/)).toBeInTheDocument();
  await userEvent.selectOptions(experimentSelect(), await screen.findByRole("option", { name: "Exp One" }));
  await userEvent.click(associate());
  await waitFor(() => expect(mockAssoc).toHaveBeenCalled());
});

test("a retry that succeeds returns to the project-first picker", async () => {
  const projects = jest
    .fn()
    .mockRejectedValueOnce(new Error("Backend unavailable"))
    .mockResolvedValue({ projects: [{ id: 11, name: "Atlas" }] });
  given(projects);
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Retry" }));

  expect(await screen.findByRole("option", { name: "Atlas" })).toBeInTheDocument();
  expect(projectSelect()).toBeEnabled();
  await waitFor(() => expect(experimentSelect()).toBeDisabled());
});

test("a role that may list neither is told so, and nothing can be submitted", async () => {
  given(() => Promise.reject(denied()), () => Promise.reject(denied()));
  open();

  expect(await screen.findByText(/does not include viewing experiments/)).toBeInTheDocument();
  expect(associate()).toBeDisabled();
});

test("experiments that fail to load are reported, not shown as an empty list", async () => {
  given(() => Promise.reject(denied()), () => Promise.reject(new Error("Backend unavailable")));
  open();

  expect(await screen.findByText(/Experiments could not be loaded/)).toBeInTheDocument();
});

test("a loaded project list does not read experiments until a project is chosen", async () => {
  given(() => Promise.resolve({ projects: [{ id: 11, name: "Atlas" }] }));
  open();

  await screen.findByRole("option", { name: "Atlas" });
  expect(mockGet).toHaveBeenCalledTimes(1);
  expect(experimentSelect()).toBeDisabled();
});
