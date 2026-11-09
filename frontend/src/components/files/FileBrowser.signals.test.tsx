/** The file list read is cancelled when a new search is submitted and when the browser closes. */
import { render, screen, fireEvent } from "@/testing/renderWithProviders";
import { captureReads } from "@/testing/captureReads";

jest.mock("@/components/shared/ContentLoading", () => ({
  ContentLoading: () => <div data-testid="loading" />,
}));
jest.mock("@/components/provenance/ProvenanceReportPanel", () => ({
  ProvenanceReportPanel: () => null,
}));
jest.mock("@/lib/auth", () => ({ getCurrentUser: () => ({ role_name: "admin" }) }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  fileContentUrl: async () => "",
}));
jest.mock("@/hooks/useContentUrl", () => ({ useFileContentUrl: () => null }));

import { FileBrowser } from "./FileBrowser";
import { api } from "@/lib/api";

test("the list read carries a signal that aborts on a new search and on unmount", () => {
  const capture = captureReads(api.get as jest.Mock);
  const { unmount } = render(<FileBrowser showSearch />);
  const first = capture.latest("/api/files?");
  expect(first.signal).toBeInstanceOf(AbortSignal);

  fireEvent.change(screen.getByLabelText("Search by filename"), { target: { value: "reads" } });
  fireEvent.submit(screen.getByLabelText("Search by filename").closest("form")!);
  expect(first.signal?.aborted).toBe(true);

  const current = capture.latest("reads");
  unmount();
  expect(current.signal?.aborted).toBe(true);
});
