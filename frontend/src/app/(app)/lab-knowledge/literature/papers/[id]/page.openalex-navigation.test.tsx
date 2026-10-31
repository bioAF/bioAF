/**
 * Moving between papers: a slower Research context response for the previous paper never
 * replaces the current paper's context or its citation-search targets. Also, a copy that
 * could not be saved says so plainly.
 */
import { fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import PaperDetailPage from "./page";

let paramId = "1";
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
  useParams: () => ({ id: paramId }),
  usePathname: () => `/lab-knowledge/literature/papers/${paramId}`,
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
    submitSearch: jest.fn(),
  },
}));

import { literature } from "@/lib/literature";

const openAlexContext = literature.openAlexContext as jest.Mock;
const submitSearch = literature.submitSearch as jest.Mock;

function paper(id: number) {
  return {
    id,
    title: `Paper ${id}`,
    authors: [],
    publication_date: null,
    journal: null,
    doi: `10.5555/p${id}`,
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
  };
}

function context(id: number, cited: number) {
  return {
    available: true,
    openalex_id: `W${id}`,
    is_retracted: false,
    cited_by_count: cited,
    counts_by_year: [],
    oa_locations: [
      { pdf_url: "https://r.example/a.pdf", landing_page_url: null, version: "acceptedVersion", license: null, host: "Repo" },
    ],
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let errorLog: jest.SpyInstance;
beforeEach(() => {
  jest.clearAllMocks();
  paramId = "1";
  (literature.getPaper as jest.Mock).mockImplementation((id: number) => Promise.resolve(paper(id)));
  (literature.listComments as jest.Mock).mockResolvedValue({ items: [] });
  (literature.recommendationNotes as jest.Mock).mockResolvedValue([]);
  submitSearch.mockResolvedValue({ id: 5 });
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

async function navigateAToB() {
  const a = deferred<ReturnType<typeof context>>();
  const b = deferred<ReturnType<typeof context>>();
  openAlexContext.mockImplementation((id: number) => (id === 1 ? a.promise : b.promise));
  const view = render(<PaperDetailPage />);
  await waitFor(() => expect(openAlexContext).toHaveBeenCalledWith(1));
  paramId = "2";
  view.rerender(<PaperDetailPage />);
  await waitFor(() => expect(openAlexContext).toHaveBeenCalledWith(2));
  b.resolve(context(2, 20));
  expect(await screen.findByText("Cited by 20")).toBeInTheDocument();
  return a;
}

async function expectPaperBTargets() {
  expect(screen.getByText("Cited by 20")).toBeInTheDocument();
  expect(screen.queryByText("Research context is unavailable right now.")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Papers citing this" }));
  await waitFor(() => expect(submitSearch).toHaveBeenCalledWith({ query: "cites:W2", sources: ["openalex"] }));
}

test("a late response for the previous paper does not replace the current context", async () => {
  const a = await navigateAToB();

  a.resolve(context(1, 10));
  await new Promise((r) => setTimeout(r, 0));

  expect(screen.queryByText("Cited by 10")).not.toBeInTheDocument();
  await expectPaperBTargets();
});

test("a late failure for the previous paper does not hide the current context", async () => {
  const a = await navigateAToB();

  a.reject(new Error("Bad gateway"));
  await new Promise((r) => setTimeout(r, 0));

  await expectPaperBTargets();
});

test("a copy that could not be saved says so plainly", async () => {
  openAlexContext.mockResolvedValue(context(1, 1));
  (literature.fetchOpenAccess as jest.Mock).mockRejectedValue(
    Object.assign(new Error("Service Unavailable"), { status: 503 }),
  );
  render(<PaperDetailPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Get open-access copy" }));

  expect(
    await screen.findByText("The PDF could not be saved, so it was not attached. Try again later."),
  ).toBeInTheDocument();
});

test("parsing at capacity says to try again shortly", async () => {
  openAlexContext.mockResolvedValue(context(1, 1));
  (literature.fetchOpenAccess as jest.Mock).mockRejectedValue(
    Object.assign(new Error("Too Many Requests"), { status: 429 }),
  );
  render(<PaperDetailPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Get open-access copy" }));

  expect(await screen.findByText("bioAF is busy reading other PDFs. Try again in a minute.")).toBeInTheDocument();
});
