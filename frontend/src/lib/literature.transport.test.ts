/**
 * Literature's PDF, citation, and attachment requests go through the shared application client,
 * keeping the DOI-conflict translation at the feature boundary.
 */
const mockNavigateToLogin = jest.fn();
jest.mock("@/lib/navigation", () => ({ navigateToLogin: () => mockNavigateToLogin() }));

import { literature, uploadPdfToPaper, fetchPaperPdfBlob, DoiConflictError } from "./literature";
import { fetchLabDocumentBlob } from "./labDocuments";
import { ApiError } from "./api";
import { getToken } from "./auth";
import { startSession } from "./session";
import { fakeServer } from "@/testing/fakeServer";

const realFetch = global.fetch;
const realEnv = process.env.NEXT_PUBLIC_API_URL;
const pdf = new File([new Uint8Array([1])], "p.pdf", { type: "application/pdf" });

beforeEach(() => {
  delete process.env.NEXT_PUBLIC_API_URL;
  localStorage.clear();
  startSession("live.jwt");
  mockNavigateToLogin.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  if (realEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = realEnv;
  jest.restoreAllMocks();
});

describe("citation", () => {
  test("returns the formatted citation", async () => {
    const { requests } = fakeServer([["/citation", { text: "@article{x}" }]]);
    await expect(literature.citation(3, "bibtex")).resolves.toBe("@article{x}");
    expect(requests[0].url).toBe("http://localhost:8000/api/literature/papers/3/citation?format=bibtex");
    expect(requests[0].headers.Authorization).toBe("Bearer live.jwt");
  });

  test("an error response is an error, not a citation", async () => {
    fakeServer([["/citation", { status: 404, json: { detail: "Paper not found" } }]]);
    const error = (await literature.citation(3).catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe("Paper not found");
  });
});

describe("PDF attachment", () => {
  test("a DOI collision still surfaces as a DoiConflictError with the other paper", async () => {
    fakeServer([["/upload-pdf", { status: 409, json: { detail: {
      error: "doi_conflict", other_paper_id: 99, other_paper_title: "Existing entry", doi: "10.x/1",
    } } }]]);
    const error = (await uploadPdfToPaper(1, pdf).catch((e) => e)) as DoiConflictError;
    expect(error).toBeInstanceOf(DoiConflictError);
    expect(error.conflict.other_paper_id).toBe(99);
  });

  test("a different 409 is an ordinary error, not a merge prompt", async () => {
    fakeServer([["/upload-pdf", { status: 409, json: { detail: "Extraction already running" } }]]);
    const error = (await uploadPdfToPaper(1, pdf).catch((e) => e)) as ApiError;
    expect(error).not.toBeInstanceOf(DoiConflictError);
    expect(error.message).toBe("Extraction already running");
  });

  test("an expired session ends the session", async () => {
    fakeServer([["/upload-pdf", { status: 401, json: { detail: "Unauthorized" } }]]);
    await expect(uploadPdfToPaper(1, pdf)).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
    expect(mockNavigateToLogin).toHaveBeenCalled();
  });
});

describe("authenticated document blobs", () => {
  test("a paper PDF comes from the shared API base with the session credential", async () => {
    const { requests } = fakeServer([["/pdf", { text: "%PDF" }]]);
    await fetchPaperPdfBlob(7);
    expect(requests[0].url).toBe("http://localhost:8000/api/literature/papers/7/pdf");
    expect(requests[0].headers.Authorization).toBe("Bearer live.jwt");
  });

  test("an expired session while reading a PDF ends the session", async () => {
    fakeServer([["/pdf", { status: 401, json: { detail: "Unauthorized" } }]]);
    await expect(fetchPaperPdfBlob(7)).rejects.toThrow();
    expect(getToken()).toBeNull();
  });

  test("a lab document version keeps its not-found message", async () => {
    const { requests } = fakeServer([["/content", { status: 404, json: { detail: "Not found" } }]]);
    await expect(fetchLabDocumentBlob(5, 2)).rejects.toThrow("Document not found.");
    expect(requests[0].url).toBe("http://localhost:8000/api/lab-knowledge/documents/5/content?version=2");
    expect(requests[0].headers.Authorization).toBe("Bearer live.jwt");
  });

  test("an expired session while reading a lab document ends the session", async () => {
    fakeServer([["/content", { status: 401, json: { detail: "Unauthorized" } }]]);
    await expect(fetchLabDocumentBlob(5)).rejects.toThrow();
    expect(getToken()).toBeNull();
  });
});
