/**
 * A failed bulk dismissal must say so and leave the selection and the confirm
 * in place for a retry; it used to restore the buttons silently.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LiteraturePage from "./page";
import { literature } from "@/lib/literature";
import { api } from "@/lib/api";

const { toastMock } = jest.requireMock("@/components/shared/Toast");

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/lab-knowledge/literature",
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "admin" }),
}));
jest.mock("@/components/literature/AssociatePaperModal", () => ({ AssociatePaperModal: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return { ...actual, literature: { listPapers: jest.fn(), bulkDismiss: jest.fn() } };
});

const L = literature as unknown as Record<string, jest.Mock>;
let errorLog: jest.SpyInstance;
beforeEach(() => {
  (api.get as jest.Mock).mockResolvedValue({ projects: [], experiments: [] });
  L.listPapers.mockResolvedValue({
    items: [{ id: 42, title: "CRISPR screen paper", authors: [], publication_date: null, journal: null, provenance: "user_upload", reading_status: "unread", dismissed: false, has_full_text: false, associations: [], comment_count: 0 }],
    total: 1,
  });
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("a failed bulk dismissal is reported, logged, and can be retried", async () => {
  L.bulkDismiss.mockRejectedValueOnce(new Error("HTTP 500: internal detail")).mockResolvedValueOnce({});
  render(<LiteraturePage />);
  await userEvent.click(await screen.findByRole("checkbox", { name: /select CRISPR screen paper/i }));
  await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  const dialog = screen.getByRole("dialog", { name: "Dismiss papers" });
  await userEvent.click(within(dialog).getByRole("button", { name: "Dismiss" }));

  await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
  expect(toastMock.error.mock.calls[0][0]).toMatch(/could not be dismissed/i);
  expect(toastMock.error.mock.calls[0][0]).not.toMatch(/internal detail/);
  expect(errorLog).toHaveBeenCalledWith(expect.stringContaining("dismissing"), expect.any(Error));
  expect(screen.getByRole("dialog", { name: "Dismiss papers" })).toBeInTheDocument();

  await userEvent.click(within(screen.getByRole("dialog", { name: "Dismiss papers" })).getByRole("button", { name: "Dismiss" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(L.bulkDismiss).toHaveBeenLastCalledWith([42]);
});
