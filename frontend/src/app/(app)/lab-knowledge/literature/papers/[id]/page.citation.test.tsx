/**
 * Copying a citation used to resolve with the error response's text and put that on the
 * clipboard as the citation. A failed citation request must say so and copy nothing.
 */
import { fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import PaperDetailPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
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
    citation: jest.fn(),
  },
}));

import { literature } from "@/lib/literature";
import { ApiError } from "@/lib/api";

const PAPER = {
  id: 9, title: "A method paper", authors: [], publication_date: null, journal: null,
  doi: "10.1/m", pmid: null, abstract: null, provenance: "source_search", source: "openalex",
  added_by_user_id: null, has_pdf: false, has_full_text: false, extraction_status: "none",
  extraction_error: null, full_text_version: null, full_text_source: null,
  full_text_source_url: null, full_text_acquired_at: null, comment_count: 0,
  reading_status: null, dismissed: false, in_library: true, associations: [],
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
};

const writeText = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  (literature.getPaper as jest.Mock).mockResolvedValue(PAPER);
  (literature.listComments as jest.Mock).mockResolvedValue({ items: [] });
  (literature.recommendationNotes as jest.Mock).mockResolvedValue([]);
  (literature.openAlexContext as jest.Mock).mockResolvedValue({ available: false, oa_locations: [], counts_by_year: [] });
  Object.assign(navigator, { clipboard: { writeText } });
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("a failed citation request is reported and nothing is copied", async () => {
  (literature.citation as jest.Mock).mockRejectedValue(new ApiError(500, "Request failed (HTTP 500)."));
  render(<PaperDetailPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Copy BibTeX" }));

  expect(await screen.findByText(/BibTeX citation could not be copied/i)).toBeInTheDocument();
  expect(writeText).not.toHaveBeenCalled();
  expect(screen.queryByText(/copied to clipboard/i)).not.toBeInTheDocument();
});

test("a citation that loads is copied", async () => {
  (literature.citation as jest.Mock).mockResolvedValue("@article{m}");
  render(<PaperDetailPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Copy RIS" }));

  await waitFor(() => expect(writeText).toHaveBeenCalledWith("@article{m}"));
  expect(await screen.findByText("RIS citation copied to clipboard.")).toBeInTheDocument();
});
