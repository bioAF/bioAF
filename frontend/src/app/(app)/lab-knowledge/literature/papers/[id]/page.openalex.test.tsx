/**
 * OpenAlex on the paper page: the Research context card loads on its own (a failure
 * never takes the paper with it), and "Get open-access copy" attaches a copy found
 * through OpenAlex and shows which version it is.
 */
import { fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import PaperDetailPage from "./page";

let role = "admin";
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: role }),
  getToken: () => "t",
}));

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
  useParams: () => ({ id: "9" }),
  usePathname: () => "/lab-knowledge/literature/papers/9",
  useSearchParams: () => new URLSearchParams(),
}));

jest.mock("@/lib/literature", () => ({
  ...jest.requireActual("@/lib/literature"),
  literature: {
    getPaper: jest.fn(),
    listComments: jest.fn(),
    recommendationNotes: jest.fn(),
    openAlexContext: jest.fn(),
    fetchOpenAccess: jest.fn(),
  },
}));

import { literature } from "@/lib/literature";

const getPaper = literature.getPaper as jest.Mock;
const openAlexContext = literature.openAlexContext as jest.Mock;
const fetchOpenAccess = literature.fetchOpenAccess as jest.Mock;

function paper(overrides = {}) {
  return {
    id: 9,
    title: "A method paper",
    authors: [],
    publication_date: null,
    journal: null,
    doi: "10.1/m",
    pmid: null,
    abstract: null,
    provenance: "source_search",
    source: "openalex",
    added_by_user_id: null,
    has_pdf: false,
    has_full_text: false,
    extraction_status: "none",
    extraction_error: null,
    full_text_version: null,
    full_text_source: null,
    full_text_source_url: null,
    full_text_acquired_at: null,
    comment_count: 0,
    reading_status: null,
    dismissed: false,
    in_library: true,
    associations: [],
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

const CONTEXT = {
  available: true,
  openalex_id: "W1",
  is_retracted: false,
  cited_by_count: 3,
  counts_by_year: [],
  oa_locations: [
    { pdf_url: "https://r.example/a.pdf", landing_page_url: null, version: "acceptedVersion", license: null, host: "Repo" },
  ],
};

let errorLog: jest.SpyInstance;
beforeEach(() => {
  jest.clearAllMocks();
  role = "admin";
  (literature.listComments as jest.Mock).mockResolvedValue({ items: [] });
  (literature.recommendationNotes as jest.Mock).mockResolvedValue([]);
  getPaper.mockResolvedValue(paper());
  openAlexContext.mockResolvedValue(CONTEXT);
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("shows the Research context card for the paper", async () => {
  render(<PaperDetailPage />);

  expect(await screen.findByText("Cited by 3")).toBeInTheDocument();
  expect(openAlexContext).toHaveBeenCalledWith(9);
});

test("a context failure stays inside its card", async () => {
  openAlexContext.mockRejectedValue(Object.assign(new Error("Bad gateway"), { status: 502 }));

  render(<PaperDetailPage />);

  expect(await screen.findByText("Research context is unavailable right now.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "A method paper" })).toBeInTheDocument();
  expect(screen.queryByText(/bad gateway/i)).not.toBeInTheDocument();
});

test("offers an open-access copy only without a PDF, with a listed PDF, to uploaders", async () => {
  const { unmount } = render(<PaperDetailPage />);
  expect(await screen.findByRole("button", { name: "Get open-access copy" })).toBeInTheDocument();
  unmount();

  getPaper.mockResolvedValue(paper({ has_pdf: true }));
  const second = render(<PaperDetailPage />);
  await screen.findByText("Cited by 3");
  expect(screen.queryByRole("button", { name: "Get open-access copy" })).not.toBeInTheDocument();
  second.unmount();

  getPaper.mockResolvedValue(paper());
  openAlexContext.mockResolvedValue({ ...CONTEXT, oa_locations: [{ ...CONTEXT.oa_locations[0], pdf_url: null }] });
  const third = render(<PaperDetailPage />);
  await screen.findByText("Cited by 3");
  expect(screen.queryByRole("button", { name: "Get open-access copy" })).not.toBeInTheDocument();
  third.unmount();

  role = "viewer";
  openAlexContext.mockResolvedValue(CONTEXT);
  render(<PaperDetailPage />);
  await screen.findByText("Cited by 3");
  expect(screen.queryByRole("button", { name: "Get open-access copy" })).not.toBeInTheDocument();
});

test("fetching disables the button, then shows the version it attached", async () => {
  let resolve: (p: unknown) => void = () => {};
  fetchOpenAccess.mockReturnValue(new Promise((r) => (resolve = r)));
  render(<PaperDetailPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Get open-access copy" }));
  const pending = await screen.findByRole("button", { name: "Getting open-access copy..." });
  expect(pending).toBeDisabled();
  fireEvent.click(pending);
  expect(fetchOpenAccess).toHaveBeenCalledTimes(1);

  getPaper.mockResolvedValue(
    paper({ has_pdf: true, full_text_version: "acceptedVersion", full_text_source: "openalex" }),
  );
  resolve(paper());

  expect(await screen.findByText("Accepted version, via OpenAlex")).toBeInTheDocument();
});

test.each([
  [404, "No openly downloadable copy was found."],
  [409, "The copy found may belong to a different paper, so it was not attached. Upload the PDF yourself instead."],
  [502, "OpenAlex could not be reached right now. Try again later."],
  [504, "Looking for an open-access copy took too long. Try again later."],
  [500, "The open-access copy could not be fetched. Try again later."],
])("a %s says so plainly and leaves the paper as it was", async (status, message) => {
  fetchOpenAccess.mockRejectedValue(Object.assign(new Error("raw backend text"), { status }));
  render(<PaperDetailPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Get open-access copy" }));

  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.queryByText(/raw backend text/)).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "A method paper" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Get open-access copy" })).toBeEnabled());
});
