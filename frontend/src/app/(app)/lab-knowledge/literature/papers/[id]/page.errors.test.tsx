/**
 * A failed load must not claim the paper was deleted.
 *
 * `refresh()` set `error` and left `paper` null, and a null paper renders "Paper not
 * found." So a 500 or a dropped connection told the reader the record was gone. The
 * raw `e.message` was also printed underneath it, which is the leak the house rule
 * exists to prevent: the plain sentence goes on screen, the technical detail to the logs.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import PaperDetailPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
  useParams: () => ({ id: "9" }),
  useSearchParams: () => new URLSearchParams(),
}));

jest.mock("@/lib/literature", () => ({
  ...jest.requireActual("@/lib/literature"),
  literature: {
    getPaper: jest.fn(),
    listComments: jest.fn(),
    recommendationNotes: jest.fn(),
  },
}));

import { literature } from "@/lib/literature";

const getPaper = literature.getPaper as jest.Mock;
const listComments = literature.listComments as jest.Mock;
const recommendationNotes = literature.recommendationNotes as jest.Mock;

let errorLog: jest.SpyInstance;
beforeEach(() => {
  jest.clearAllMocks();
  listComments.mockResolvedValue({ items: [] });
  recommendationNotes.mockResolvedValue([]);
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("a 500 does not claim the paper was deleted, and does not leak the raw error", async () => {
  getPaper.mockRejectedValue(Object.assign(new Error("Injected 500"), { status: 500 }));

  render(<PaperDetailPage />);

  await waitFor(() => expect(screen.getByTestId("paper-load-failed")).toBeInTheDocument());
  expect(screen.getByTestId("paper-load-failed")).toHaveTextContent(/could not be loaded/i);
  expect(screen.queryByText(/paper not found/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/injected 500/i)).not.toBeInTheDocument();
  expect(errorLog).toHaveBeenCalledWith(expect.stringContaining("paper 9"), expect.any(Error));
});

test("a paper that really is missing still says so", async () => {
  getPaper.mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }));

  render(<PaperDetailPage />);

  await waitFor(() => expect(screen.getByText(/paper not found/i)).toBeInTheDocument());
  expect(screen.queryByTestId("paper-load-failed")).not.toBeInTheDocument();
});
