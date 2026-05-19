import {
  uploadPdfToPaper,
  fetchPaperPdfObjectUrl,
  DoiConflictError,
  type Paper,
} from "./literature";

jest.mock("./auth", () => ({
  getToken: () => "test-token",
}));

const makePaper = (overrides: Partial<Paper> = {}): Paper =>
  ({
    id: 1,
    title: "T",
    authors: [],
    publication_date: null,
    journal: null,
    doi: "10.x/1",
    pmid: null,
    abstract: null,
    provenance: "user_upload",
    source: "upload",
    added_by_user_id: 1,
    has_pdf: true,
    has_full_text: false,
    extraction_status: "pending",
    extraction_error: null,
    comment_count: 0,
    reading_status: null,
    dismissed: false,
    in_library: true,
    associations: [],
    created_at: "",
    updated_at: "",
    ...overrides,
  }) as Paper;

const file = new File([new Uint8Array([1, 2, 3])], "p.pdf", {
  type: "application/pdf",
});

const fetchMock = jest.fn();

function fakeResponse(status: number, body: unknown) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

test("returns the updated paper on 200", async () => {
  fetchMock.mockResolvedValue(fakeResponse(200, makePaper({ id: 5 })));
  const paper = await uploadPdfToPaper(5, file);
  expect(paper.id).toBe(5);
  const calledUrl = fetchMock.mock.calls[0][0] as string;
  expect(calledUrl).toContain("/api/literature/papers/5/upload-pdf");
  expect(calledUrl).not.toContain("confirm_merge");
});

test("adds confirm_merge=true to the URL when confirming", async () => {
  fetchMock.mockResolvedValue(fakeResponse(200, makePaper()));
  await uploadPdfToPaper(7, file, true);
  const calledUrl = fetchMock.mock.calls[0][0] as string;
  expect(calledUrl).toContain("confirm_merge=true");
});

test("throws DoiConflictError carrying the other paper on 409", async () => {
  fetchMock.mockResolvedValue(
    fakeResponse(409, {
      detail: {
        error: "doi_conflict",
        other_paper_id: 99,
        other_paper_title: "Existing entry",
        doi: "10.x/1",
      },
    }),
  );
  await expect(uploadPdfToPaper(1, file)).rejects.toBeInstanceOf(
    DoiConflictError,
  );
  try {
    await uploadPdfToPaper(1, file);
  } catch (e) {
    const conflict = (e as DoiConflictError).conflict;
    expect(conflict.other_paper_id).toBe(99);
    expect(conflict.other_paper_title).toBe("Existing entry");
  }
});

test("throws a plain error on other failures", async () => {
  fetchMock.mockResolvedValue(
    fakeResponse(400, { detail: "only PDF uploads are supported" }),
  );
  await expect(uploadPdfToPaper(1, file)).rejects.toThrow(/only PDF/);
});

describe("fetchPaperPdfObjectUrl", () => {
  const realCreate = global.URL.createObjectURL;

  beforeEach(() => {
    global.URL.createObjectURL = jest.fn(() => "blob:fake-url");
  });
  afterEach(() => {
    global.URL.createObjectURL = realCreate;
  });

  test("fetches the PDF endpoint with auth and returns an object URL", async () => {
    const blob = { type: "application/pdf" };
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      blob: async () => blob,
    });
    const url = await fetchPaperPdfObjectUrl(42);
    expect(url).toBe("blob:fake-url");
    const [calledUrl, init] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain("/api/literature/papers/42/pdf");
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: "Bearer test-token",
    });
    expect(global.URL.createObjectURL).toHaveBeenCalledWith(blob);
  });

  test("throws when the paper has no PDF (404)", async () => {
    fetchMock.mockResolvedValue({
      status: 404,
      ok: false,
      blob: async () => ({}),
    });
    await expect(fetchPaperPdfObjectUrl(42)).rejects.toThrow();
  });
});
