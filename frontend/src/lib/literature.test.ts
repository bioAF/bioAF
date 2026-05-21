import {
  uploadPdfToPaper,
  fetchPaperPdfObjectUrl,
  fetchPaperPdfBlob,
  advanceReadingStatus,
  cleanText,
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

describe("fetchPaperPdfBlob", () => {
  test("fetches the PDF endpoint with auth and returns the blob", async () => {
    const blob = { type: "application/pdf" };
    fetchMock.mockResolvedValue({ status: 200, ok: true, blob: async () => blob });
    const result = await fetchPaperPdfBlob(7);
    expect(result).toBe(blob);
    const [calledUrl, init] = fetchMock.mock.calls[0];
    expect(calledUrl).toContain("/api/literature/papers/7/pdf");
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: "Bearer test-token",
    });
  });

  test("throws a 'no PDF' message on 404", async () => {
    fetchMock.mockResolvedValue({ status: 404, ok: false, blob: async () => ({}) });
    await expect(fetchPaperPdfBlob(7)).rejects.toThrow(/no pdf/i);
  });
});

describe("advanceReadingStatus", () => {
  test("page 1 of a multi-page document implies no progress", () => {
    expect(advanceReadingStatus("unread", 1, 10)).toBeNull();
    expect(advanceReadingStatus(null, 1, 10)).toBeNull();
  });

  test("reaching page 2 advances unread to reading", () => {
    expect(advanceReadingStatus("unread", 2, 10)).toBe("reading");
    expect(advanceReadingStatus(null, 2, 10)).toBe("reading");
  });

  test("reaching the last page advances to read", () => {
    expect(advanceReadingStatus("reading", 10, 10)).toBe("read");
    expect(advanceReadingStatus("unread", 10, 10)).toBe("read");
  });

  test("a single-page document goes straight to read on open", () => {
    expect(advanceReadingStatus("unread", 1, 1)).toBe("read");
    expect(advanceReadingStatus(null, 1, 1)).toBe("read");
  });

  test("never downgrades or overrides a higher status", () => {
    expect(advanceReadingStatus("read", 2, 10)).toBeNull();
    expect(advanceReadingStatus("read", 10, 10)).toBeNull();
    expect(advanceReadingStatus("reading", 3, 10)).toBeNull();
  });

  test("returns null when the page count is unknown", () => {
    expect(advanceReadingStatus("unread", 1, 0)).toBeNull();
  });
});

describe("cleanText", () => {
  test("strips simple inline tags", () => {
    expect(cleanText("Ca<sup>2+</sup> signalling")).toBe("Ca2+ signalling");
  });

  test("fully strips nested/split tags (no live tag survives a single pass)", () => {
    expect(cleanText("<scr<script>ipt>alert(1)")).not.toContain("<script");
    expect(cleanText("<scr<script>ipt>alert(1)")).not.toContain("<");
  });

  test("preserves standalone angle brackets used as math", () => {
    expect(cleanText("expression p < 0.05 and FC > 2")).toBe(
      "expression p < 0.05 and FC > 2",
    );
  });

  test("decodes entities before stripping", () => {
    expect(cleanText("Ca&lt;sup&gt;2+&lt;/sup&gt;")).toBe("Ca2+");
  });

  test("handles null/undefined", () => {
    expect(cleanText(null)).toBe("");
    expect(cleanText(undefined)).toBe("");
  });
});
