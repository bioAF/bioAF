import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ResearchContextCard } from "./ResearchContextCard";
import { literature, type OpenAlexContext } from "@/lib/literature";

const push = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
jest.mock("@/lib/literature", () => ({
  ...jest.requireActual("@/lib/literature"),
  literature: { submitSearch: jest.fn() },
}));
const mockSubmit = literature.submitSearch as jest.Mock;

const CAVEAT =
  "Context only. Citation counts do not establish validity or method reuse, and no retraction flag does not guarantee a clean record.";

function ctx(overrides: Partial<OpenAlexContext> = {}): OpenAlexContext {
  return {
    available: true,
    openalex_id: "W123",
    is_retracted: false,
    cited_by_count: 42,
    counts_by_year: [
      { year: 2023, cited_by_count: 12 },
      { year: 2024, cited_by_count: 30 },
    ],
    oa_locations: [],
    ...overrides,
  };
}

beforeEach(() => {
  push.mockReset();
  mockSubmit.mockReset();
});

test("shows the count, yearly history, no known retraction, and the caveat", () => {
  render(<ResearchContextCard context={ctx()} failed={false} canSearch />);
  expect(screen.getByRole("heading", { name: "Research context" })).toBeInTheDocument();
  expect(screen.getByText("Cited by 42")).toBeInTheDocument();
  expect(screen.getByText("No retraction known to OpenAlex")).toBeInTheDocument();
  expect(screen.getByText("Available citation history")).toBeInTheDocument();
  expect(screen.getByLabelText("2024: 30 citations")).toBeInTheDocument();
  expect(screen.getByText(CAVEAT)).toBeInTheDocument();
});

test("flags a known retraction", () => {
  render(<ResearchContextCard context={ctx({ is_retracted: true })} failed={false} canSearch />);
  expect(screen.getByText("Retracted, according to OpenAlex")).toBeInTheDocument();
});

test("does not show unknown status or counts as clean or zero", () => {
  render(
    <ResearchContextCard
      context={ctx({ is_retracted: null, cited_by_count: null, counts_by_year: [] })}
      failed={false}
      canSearch
    />,
  );
  expect(screen.getByText("Retraction status unknown")).toBeInTheDocument();
  expect(screen.getByText("Citation count unknown")).toBeInTheDocument();
  expect(screen.queryByText(/Cited by/)).not.toBeInTheDocument();
  expect(screen.queryByText("Available citation history")).not.toBeInTheDocument();
});

test("says so when OpenAlex has no record, and when it cannot be reached", () => {
  const { rerender } = render(
    <ResearchContextCard context={{ ...ctx(), available: false, openalex_id: null }} failed={false} canSearch />,
  );
  expect(screen.getByText("OpenAlex has no record matching this paper.")).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();

  rerender(<ResearchContextCard context={null} failed canSearch />);
  expect(screen.getByText("Research context is unavailable right now.")).toBeInTheDocument();
});

test("lists open-access copies with their version and license", () => {
  render(
    <ResearchContextCard
      context={ctx({
        oa_locations: [
          {
            pdf_url: "https://peerj.com/a.pdf",
            landing_page_url: "https://peerj.com/a",
            version: "publishedVersion",
            license: "cc-by",
            host: "PeerJ",
          },
          { pdf_url: null, landing_page_url: "https://repo.example/b", version: null, license: null, host: null },
        ],
      })}
      failed={false}
      canSearch
    />,
  );
  expect(screen.getByRole("link", { name: "PeerJ" })).toHaveAttribute("href", "https://peerj.com/a");
  expect(screen.getByText("Published version, license cc-by")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "repo.example" })).toHaveAttribute("href", "https://repo.example/b");
  expect(screen.getByText("Version not stated, license not stated")).toBeInTheDocument();
});

test.each([
  ["Papers this builds on", "cited_by:W123"],
  ["Papers citing this", "cites:W123"],
])("%s starts an OpenAlex citation search and opens Searches", async (label, query) => {
  mockSubmit.mockResolvedValue({ id: 9 });
  render(<ResearchContextCard context={ctx()} failed={false} canSearch />);

  fireEvent.click(screen.getByRole("button", { name: label }));

  await waitFor(() => expect(push).toHaveBeenCalledWith("/lab-knowledge/literature/searches"));
  expect(mockSubmit).toHaveBeenCalledWith({ query, sources: ["openalex"] });
});

test("a citation search that cannot start says so in plain words", async () => {
  const spy = jest.spyOn(console, "error").mockImplementation(() => {});
  mockSubmit.mockRejectedValue(new Error("500 Internal Server Error"));
  render(<ResearchContextCard context={ctx()} failed={false} canSearch />);

  fireEvent.click(screen.getByRole("button", { name: "Papers citing this" }));

  expect(await screen.findByText("The search could not be started. Try again later.")).toBeInTheDocument();
  expect(screen.queryByText(/500/)).not.toBeInTheDocument();
  expect(push).not.toHaveBeenCalled();
  spy.mockRestore();
});

test("hides citation actions from roles that cannot search", () => {
  render(<ResearchContextCard context={ctx()} failed={false} canSearch={false} />);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
