/** Plan 03.5A: Open sends the single-use ticket to the notebook gateway in a form POST, never a URL. */
import { isProtectedAccess, openProtectedSession } from "./notebookAccess";

jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
import { api } from "@/lib/api";
const mockPost = api.post as jest.Mock;

let submitted: { action: string; method: string; target: string; fields: Record<string, string> }[];
let opened: { url: string | undefined; name: string | undefined; closed: boolean }[];

beforeEach(() => {
  submitted = [];
  opened = [];
  mockPost.mockReset();
  jest.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(function (this: HTMLFormElement) {
    const fields: Record<string, string> = {};
    this.querySelectorAll("input").forEach((i) => (fields[i.name] = i.value));
    submitted.push({ action: this.action, method: this.method, target: this.target, fields });
  });
  jest.spyOn(window, "open").mockImplementation((url?: string | URL, name?: string) => {
    const w = { url: url?.toString(), name, closed: false };
    opened.push(w);
    return { close: () => (w.closed = true) } as unknown as Window;
  });
});

afterEach(() => jest.restoreAllMocks());

test("protected sessions are recognized by their gateway path", () => {
  expect(isProtectedAccess("/s/41/")).toBe(true);
  expect(isProtectedAccess("http://34.1.2.3:8888")).toBe(false);
  expect(isProtectedAccess(null)).toBe(false);
});

test("Open opens a tab first, then posts the ticket to the gateway in it", async () => {
  mockPost.mockResolvedValue({ entry_url: "https://10.0.0.5:8443/_bioaf/enter", ticket: "tkt-123", expires_in: 60 });
  await openProtectedSession(41);

  expect(mockPost).toHaveBeenCalledWith("/api/v1/notebooks/sessions/41/access");
  expect(opened).toHaveLength(1);
  expect(opened[0].url).toBe("about:blank");
  expect(submitted).toEqual([
    {
      action: "https://10.0.0.5:8443/_bioaf/enter",
      method: "post",
      target: opened[0].name,
      fields: { ticket: "tkt-123" },
    },
  ]);
  expect(submitted[0].action).not.toContain("tkt-123");
  // The form does not stay in the page.
  expect(document.querySelectorAll("form").length).toBe(0);
});

test("a refused Open closes the blank tab and reports the error", async () => {
  mockPost.mockRejectedValue(new Error("Only the person who started this session can open it."));
  await expect(openProtectedSession(41)).rejects.toThrow("Only the person who started this session can open it.");
  expect(opened[0].closed).toBe(true);
  expect(submitted).toEqual([]);
});
