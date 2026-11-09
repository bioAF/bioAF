/**
 * The lab-document, decision-record, and glossary lists read with the resource's signal: a filter
 * change cancels the previous filter's read rather than letting it finish unseen.
 */
import { render, screen, fireEvent } from "@testing-library/react";

jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() } }));

import { LabDocumentBrowser } from "./LabDocumentBrowser";
import { SdrBrowser } from "./SdrBrowser";
import { LabGlossaryBrowser } from "./LabGlossaryBrowser";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Read = { url: string; signal?: AbortSignal };
let reads: Read[];

beforeEach(() => {
  reads = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string, options?: { signal?: AbortSignal }) => {
    reads.push({ url, signal: options?.signal });
    if (url.includes("/tags") || url.includes("/sdr-categories")) return Promise.resolve([]);
    return new Promise(() => {});
  });
});

const listRead = (fragment: string) => reads.find((r) => r.url.includes(fragment))!;

test("lab documents: changing the archive filter aborts the previous list read", () => {
  render(<LabDocumentBrowser />);
  const first = listRead("/documents?").signal!;
  expect(first).toBeInstanceOf(AbortSignal);
  fireEvent.click(screen.getByLabelText(/archived/i));
  expect(first.aborted).toBe(true);
});

test("decision records: changing the status filter aborts the previous list read", () => {
  render(<SdrBrowser />);
  const first = listRead("/sdrs?").signal!;
  expect(first).toBeInstanceOf(AbortSignal);
  fireEvent.change(screen.getByLabelText(/filter by status/i), { target: { value: "repealed" } });
  expect(first.aborted).toBe(true);
});

test("glossary: the term list and pending count abort when the browser closes", () => {
  const { unmount } = render(<LabGlossaryBrowser />);
  const terms = listRead("/glossary?").signal!;
  const pending = listRead("/glossary/pending").signal!;
  unmount();
  expect(terms.aborted).toBe(true);
  expect(pending.aborted).toBe(true);
});
