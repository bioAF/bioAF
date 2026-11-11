/**
 * A failed dismissal (or reversal) must say so. The handlers had try/finally
 * with no catch: the controls came back as if nothing happened and the error
 * escaped as an unhandled rejection. Found on blackbird during plan 18's live
 * validation. The message is plain; the technical detail goes to the logs.
 */
import { render, screen, waitFor, within } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import PaperDetailPage from "./page";

const { toastMock } = jest.requireMock("@/components/shared/Toast");

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
  useParams: () => ({ id: "9" }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/lab-knowledge/literature/papers/9",
}));
jest.mock("@/lib/literature", () => ({
  ...jest.requireActual("@/lib/literature"),
  literature: {
    getPaper: jest.fn(),
    listComments: jest.fn(),
    recommendationNotes: jest.fn(),
    openAlexContext: jest.fn(),
    dismissPaper: jest.fn(),
    reverseDismiss: jest.fn(),
  },
}));

import { literature } from "@/lib/literature";
const L = literature as unknown as Record<string, jest.Mock>;

const paper = (dismissed: boolean) => ({
  id: 9, title: "A paper", authors: [], publication_date: null, journal: null, doi: null, pmid: null, abstract: null,
  provenance: "user_upload", source: null, added_by_user_id: 1, has_pdf: false, has_full_text: false,
  extraction_status: "complete", extraction_error: null, full_text_version: null, full_text_source: null,
  full_text_source_url: null, full_text_acquired_at: null, comment_count: 0, reading_status: null,
  dismissed, in_library: true, associations: [], created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z",
});

let errorLog: jest.SpyInstance;
beforeEach(() => {
  jest.clearAllMocks();
  L.listComments.mockResolvedValue({ items: [] });
  L.recommendationNotes.mockResolvedValue([]);
  L.openAlexContext?.mockResolvedValue({ available: false, oa_locations: [] });
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("a failed dismissal is shown in the prompt, keeps the reason, logs the detail, and can be retried", async () => {
  L.getPaper.mockResolvedValue(paper(false));
  L.dismissPaper.mockRejectedValueOnce(new Error("HTTP 500: internal detail")).mockResolvedValueOnce({});
  render(<PaperDetailPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Dismiss org-wide" }));
  const dialog = screen.getByRole("dialog", { name: "Dismiss paper org-wide" });
  await userEvent.type(within(dialog).getByLabelText("Reason (optional)"), "superseded");
  await userEvent.click(within(dialog).getByRole("button", { name: "Dismiss" }));

  expect(await within(dialog).findByText(/could not be dismissed/i)).toBeInTheDocument();
  expect(within(dialog).queryByText(/internal detail/)).toBeNull();
  expect(within(dialog).getByLabelText("Reason (optional)")).toHaveValue("superseded");
  expect(errorLog).toHaveBeenCalledWith(expect.stringContaining("dismissing paper 9"), expect.any(Error));

  await userEvent.click(within(dialog).getByRole("button", { name: "Dismiss" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(L.dismissPaper).toHaveBeenCalledTimes(2);
  expect(L.dismissPaper).toHaveBeenLastCalledWith(9, "superseded");
});

test("reopening the prompt after a failure does not show the old error", async () => {
  L.getPaper.mockResolvedValue(paper(false));
  L.dismissPaper.mockRejectedValueOnce(new Error("boom"));
  render(<PaperDetailPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Dismiss org-wide" }));
  const dialog = screen.getByRole("dialog", { name: "Dismiss paper org-wide" });
  await userEvent.click(within(dialog).getByRole("button", { name: "Dismiss" }));
  await within(dialog).findByText(/could not be dismissed/i);
  await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await userEvent.click(screen.getByRole("button", { name: "Dismiss org-wide" }));
  expect(within(screen.getByRole("dialog")).queryByText(/could not be dismissed/i)).toBeNull();
});

test("a failed reversal says so, keeps the confirm open for a retry, and logs the detail", async () => {
  L.getPaper.mockResolvedValue(paper(true));
  L.reverseDismiss.mockRejectedValueOnce(new Error("HTTP 500: internal detail"));
  render(<PaperDetailPage />);
  await userEvent.click(await screen.findByRole("button", { name: "Reverse dismissal" }));
  const dialog = screen.getByRole("dialog", { name: "Reverse dismissal" });
  await userEvent.click(within(dialog).getByRole("button", { name: "Reverse" }));
  await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
  expect(toastMock.error.mock.calls[0][0]).toMatch(/could not be completed/i);
  expect(toastMock.error.mock.calls[0][0]).not.toMatch(/internal detail/);
  expect(screen.getByRole("dialog", { name: "Reverse dismissal" })).toBeInTheDocument();
  expect(errorLog).toHaveBeenCalledWith(expect.stringContaining("paper 9"), expect.any(Error));
});
