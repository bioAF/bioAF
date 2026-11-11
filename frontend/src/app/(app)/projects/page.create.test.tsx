/**
 * The New Project dialog is a real form: Enter in a field and the Create
 * Project button submit through one handler, a second submit while the first
 * is in flight creates nothing, and the browser never performs its own
 * (GET, query-string) submission.
 */
import { act, render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProjectsPage from "./page";
import { clearPermissionsCache } from "@/hooks/usePermissions";
const { toastMock } = jest.requireMock("@/components/shared/Toast");

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/projects",
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  clearPermissionsCache();
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) =>
    url === "/api/auth/me"
      ? Promise.resolve({ role_name: "lab", permissions: [{ resource: "projects", action: "create" }] })
      : Promise.resolve({ projects: [], total: 0 }),
  );
});

async function openCreate() {
  render(<ProjectsPage />);
  const buttons = await screen.findAllByRole("button", { name: "New Project" });
  await userEvent.click(buttons[0]);
  const dialog = screen.getByRole("dialog");
  const form = dialog.querySelector("form") as HTMLFormElement;
  return { dialog, form };
}

test("the name and hypothesis fields are labelled with instance-unique ids", async () => {
  const { dialog } = await openCreate();
  const name = within(dialog).getByLabelText("Name");
  const hypothesis = within(dialog).getByLabelText("Hypothesis (optional)");
  expect(name.tagName).toBe("INPUT");
  expect(hypothesis.tagName).toBe("TEXTAREA");
  expect(name.id).not.toBe("name");
  expect(name.id).not.toBe(hypothesis.id);
  expect(name).toBeRequired();
});

test("Enter in the name field submits once", async () => {
  mockPost.mockResolvedValue({});
  const { dialog } = await openCreate();
  await userEvent.type(within(dialog).getByLabelText("Name"), "Atlas{Enter}");
  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  expect(mockPost).toHaveBeenCalledWith("/api/projects", { name: "Atlas", hypothesis: null });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("the Create Project button submits the same form", async () => {
  mockPost.mockResolvedValue({});
  const { dialog, form } = await openCreate();
  await userEvent.type(within(dialog).getByLabelText("Name"), "Atlas");
  await userEvent.type(within(dialog).getByLabelText("Hypothesis (optional)"), "Why");
  const create = within(dialog).getByRole("button", { name: "Create Project" });
  expect(create).toHaveAttribute("type", "submit");
  expect(create).toHaveAttribute("form", form.id);
  await userEvent.click(create);
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/projects", { name: "Atlas", hypothesis: "Why" }));
  expect(mockPost).toHaveBeenCalledTimes(1);
});

test("two submits before a re-render create one project", async () => {
  let resolve: (v: unknown) => void = () => {};
  mockPost.mockImplementation(() => new Promise((r) => (resolve = r)));
  const { dialog, form } = await openCreate();
  await userEvent.type(within(dialog).getByLabelText("Name"), "Atlas");
  act(() => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(mockPost).toHaveBeenCalledTimes(1);
  await act(async () => resolve({}));
});

test("the browser's own submission is always prevented", async () => {
  mockPost.mockResolvedValue({});
  const { dialog, form } = await openCreate();
  await userEvent.type(within(dialog).getByLabelText("Name"), "Atlas");
  const event = new Event("submit", { bubbles: true, cancelable: true });
  await act(async () => {
    form.dispatchEvent(event);
  });
  expect(event.defaultPrevented).toBe(true);
  expect(form.getAttribute("action")).toBeNull();
  expect(form.getAttribute("method")).toBeNull();
});

test("after a failed create the form stays open and can submit again", async () => {
  mockPost.mockRejectedValueOnce(new Error("A project with this name already exists.")).mockResolvedValueOnce({});
  const { dialog } = await openCreate();
  await userEvent.type(within(dialog).getByLabelText("Name"), "Atlas{Enter}");
  await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("A project with this name already exists."));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "Create Project" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(2));
});

test("an empty name cannot be submitted, by Enter or by the button", async () => {
  const { dialog, form } = await openCreate();
  expect(within(dialog).getByRole("button", { name: "Create Project" })).toBeDisabled();
  await userEvent.type(within(dialog).getByLabelText("Name"), "   {Enter}");
  fireEvent.submit(form);
  expect(mockPost).not.toHaveBeenCalled();
});

test("Cancel closes without submitting and is not a submit button", async () => {
  const { dialog } = await openCreate();
  await userEvent.type(within(dialog).getByLabelText("Name"), "Atlas");
  const cancel = within(dialog).getByRole("button", { name: "Cancel" });
  expect(cancel).toHaveAttribute("type", "button");
  await userEvent.click(cancel);
  expect(mockPost).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});
