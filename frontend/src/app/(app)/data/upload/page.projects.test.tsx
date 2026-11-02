/**
 * Upload scopes are independent. A role that may not list projects cannot use
 * Project scope, but still uploads to an experiment, a sample, or globally.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import DataUploadPage from "./page";

jest.mock("@/hooks/useCapabilities", () => ({ useCapabilities: () => ({ has: () => false }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), uploadProxied: jest.fn(), uploadSigned: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return projects();
    if (url.startsWith("/api/experiments?")) {
      return Promise.resolve({ experiments: [{ id: 5, name: "Exp One", code: null, status: "registered" }] });
    }
    return Promise.resolve([]);
  });
}

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

const projectSelect = () => screen.getByLabelText(/^Project/);

test("a 403 closes Project scope, says why, and keeps experiment scope", async () => {
  givenProjects(() => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 })));
  render(<DataUploadPage />);

  await waitFor(() => expect(projectSelect()).toBeDisabled());
  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Project" })).toBeDisabled();

  await userEvent.selectOptions(screen.getByLabelText(/^Experiment/), await screen.findByRole("option", { name: "Exp One" }));
  expect((screen.getByLabelText(/^Experiment/) as HTMLSelectElement).value).toBe("5");
});

test("a 403 can still reach experiments past the first page", async () => {
  const firstPage = Array.from({ length: 200 }, (_, i) => ({ id: 1000 + i, name: `Exp ${i}`, code: null, status: "registered" }));
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return Promise.reject(Object.assign(new Error("role_missing"), { status: 403 }));
    if (url.startsWith("/api/experiments?")) {
      return Promise.resolve(
        url.includes("page=2")
          ? { experiments: [{ id: 2000, name: "Oldest", code: null, status: "registered" }], total: 201 }
          : { experiments: firstPage, total: 201 },
      );
    }
    return Promise.resolve([]);
  });
  render(<DataUploadPage />);

  expect(await screen.findByRole("option", { name: "Oldest" })).toBeInTheDocument();
});

test("a failure is visible, can be retried, and leaves other scopes open", async () => {
  const projects = jest
    .fn()
    .mockRejectedValueOnce(new Error("Backend unavailable"))
    .mockResolvedValue({ projects: [{ id: 2, name: "Atlas", code: null }] });
  givenProjects(projects);
  render(<DataUploadPage />);

  expect(await screen.findByText(/Projects could not be loaded/)).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Project" })).toBeEnabled();
  expect(await screen.findByRole("option", { name: "Exp One" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("option", { name: "Atlas" })).toBeInTheDocument();
  expect(projectSelect()).toBeEnabled();
});
