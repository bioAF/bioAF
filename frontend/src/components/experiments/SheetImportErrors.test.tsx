/**
 * Google Sheets read failures render the server's controlled message (plan 12.5, finding L1).
 *
 * The backend now returns JSON `detail` text for every Sheets read failure. These tests go through
 * the real API client (only `fetch` is stubbed) for all three modals that read a sheet, and check
 * that the message shown is that text: not "Unknown error", not raw Google output, and not a
 * session teardown.
 */
import { fireEvent, render, screen } from "@/testing/renderWithProviders";
import { setToken, getToken } from "@/lib/auth";
import { CsvUploadModal } from "./CsvUploadModal";
import { SheetImportModal } from "./SheetImportModal";
import { TemplateSheetImportModal } from "./TemplateSheetImportModal";

const SHEET_URL = "https://docs.google.com/spreadsheets/d/abc123/edit";
const realFetch = global.fetch;

function respondWith(status: number, body: unknown) {
  global.fetch = jest.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  localStorage.clear();
  setToken("live.session.jwt");
});
afterEach(() => {
  global.fetch = realFetch;
});

const MODALS: [string, () => JSX.Element, string][] = [
  [
    "SheetImportModal",
    () => (
      <SheetImportModal
        onClose={() => {}}
        onApply={() => {}}
        existingFieldDefaults={[]}
        existingCustomFields={[]}
      />
    ),
    "Import Columns",
  ],
  [
    "TemplateSheetImportModal",
    () => (
      <TemplateSheetImportModal
        onClose={() => {}}
        onApply={() => {}}
        existingRequiredSampleFields={[]}
        existingCustomFields={[]}
      />
    ),
    "Import Columns",
  ],
  ["CsvUploadModal", () => <CsvUploadModal experimentId={4} onClose={() => {}} onSuccess={() => {}} />, "Import from Sheet"],
];

async function submitSheet(renderModal: () => JSX.Element, buttonName: string) {
  render(renderModal());
  const tab = screen.queryByRole("button", { name: "Google Sheet" });
  if (tab) fireEvent.click(tab);
  fireEvent.change(screen.getByPlaceholderText("https://docs.google.com/spreadsheets/d/..."), {
    target: { value: SHEET_URL },
  });
  fireEvent.click(screen.getByRole("button", { name: buttonName }));
}

describe.each(MODALS)("%s", (_name, renderModal, buttonName) => {
  test("a spreadsheet Google rejects shows the correction guidance", async () => {
    const detail =
      "Google Sheets could not open that spreadsheet or tab. Check the spreadsheet URL, including the tab, and try again.";
    respondWith(400, { detail });
    await submitSheet(renderModal, buttonName);

    expect(await screen.findByText(detail)).toBeInTheDocument();
    expect(screen.queryByText(/Unknown error/)).not.toBeInTheDocument();
    expect(screen.queryByText(/HttpError/)).not.toBeInTheDocument();
  });

  test("an upstream credential failure keeps the bioAF session", async () => {
    const detail =
      "Google did not accept bioAF's reader credentials. An administrator should check the Google Sheets reader setup in Settings > Integrations > GCP.";
    respondWith(502, { detail });
    await submitSheet(renderModal, buttonName);

    expect(await screen.findByText(detail)).toBeInTheDocument();
    expect(getToken()).toBe("live.session.jwt");
  });

  test("a timeout shows the retry message, and a retry recovers without the stale error", async () => {
    const detail = "Google Sheets did not respond in time. Try again.";
    respondWith(504, { detail });
    await submitSheet(renderModal, buttonName);
    expect(await screen.findByText(detail)).toBeInTheDocument();

    respondWith(200, {
      spreadsheet_id: "abc123",
      sheet_name: "S1",
      columns: ["organism"],
      recognized_columns: [{ header: "organism", mapped_to: "organism", defaultable: true }],
      unknown_columns: ["barcode"],
      // CsvUploadModal's sample preview shape:
      total_rows: 1,
      errors: [],
      preview_rows: [{ organism: "human", barcode: "AAAC" }],
    });
    fireEvent.click(screen.getByRole("button", { name: buttonName }));
    expect(await screen.findByLabelText("Map column barcode to")).toBeInTheDocument();
    expect(screen.queryByText(detail)).not.toBeInTheDocument();
  });
});
