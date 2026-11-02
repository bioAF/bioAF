import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AiLitReviewLauncher } from "./AiLitReviewLauncher";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return {
    ...actual,
    literature: { ...actual.literature, runLitReview: jest.fn(), getRun: jest.fn() },
  };
});

import { api } from "@/lib/api";
import { literature } from "@/lib/literature";
const mockGet = api.get as jest.Mock;
const mockRun = literature.runLitReview as jest.Mock;

const experiments = { experiments: [{ id: 101, name: "Exp One", project: { id: 11, name: "Atlas" } }], total: 1 };

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) =>
    url.startsWith("/api/projects") ? projects() : Promise.resolve(experiments),
  );
}

beforeEach(() => {
  mockGet.mockReset();
  mockRun.mockReset();
  mockRun.mockRejectedValue(new Error("stop here"));
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

const projectFilter = () => screen.getByLabelText("Project (optional filter)");

test("a 403 disables the optional filter, says why, and a review still runs", async () => {
  givenProjects(() => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 })));
  render(<AiLitReviewLauncher />);

  await waitFor(() => expect(projectFilter()).toBeDisabled());
  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();

  await userEvent.selectOptions(await screen.findByLabelText("Experiment"), await screen.findByRole("option", { name: /Exp One/ }));
  await userEvent.click(screen.getByRole("button", { name: "Run AI Lit Review" }));
  await waitFor(() => expect(mockRun).toHaveBeenCalledWith(101));
});

test("a failure is visible and can be retried", async () => {
  const projects = jest
    .fn()
    .mockRejectedValueOnce(new Error("Backend unavailable"))
    .mockResolvedValue({ projects: [{ id: 11, name: "Atlas" }] });
  givenProjects(projects);
  render(<AiLitReviewLauncher />);

  expect(await screen.findByText(/Projects could not be loaded/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(await screen.findByRole("option", { name: "Atlas" })).toBeInTheDocument();
  expect(screen.queryByText(/Projects could not be loaded/)).not.toBeInTheDocument();
});
