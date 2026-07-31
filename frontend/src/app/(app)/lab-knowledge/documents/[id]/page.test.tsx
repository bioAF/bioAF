import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import LabDocumentDetailPage from "./page";

jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "5" }),
  useRouter: () => ({ push: jest.fn() }),
}));

let canAccessImpl = (_r: string, _a: string) => true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => canAccessImpl(r, a) }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, role_name: "admin" }),
}));

jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => <div /> }));
jest.mock("@/components/layout/Header", () => ({ Header: () => <div /> }));
jest.mock("@/components/lab-knowledge/LabDocumentViewer", () => ({
  LabDocumentViewer: () => <div data-testid="viewer" />,
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
}));
jest.mock("@/lib/labDocuments", () => ({
  labDocuments: { listNotes: jest.fn(), addNote: jest.fn(), deleteNote: jest.fn() },
  uploadDocumentFile: jest.fn(),
}));

import { api } from "@/lib/api";
import { labDocuments } from "@/lib/labDocuments";

const mockGet = api.get as jest.Mock;
const listNotes = labDocuments.listNotes as jest.Mock;
const addNote = labDocuments.addNote as jest.Mock;

const doc = {
  id: 5,
  title: "Centrifuge Manual",
  description: "How to spin",
  file_name: "manual.pdf",
  current_version: 1,
  file_size_bytes: 2048,
  mime_type: "application/pdf",
  is_archived: false,
  tags: [{ id: 7, name: "manual" }],
  created_by: { id: 1, name: "Alice", email: "alice@example.com" },
  created_at: "2026-01-15T10:00:00Z",
  updated_at: "2026-01-15T10:00:00Z",
};

beforeEach(() => {
  canAccessImpl = () => true;
  mockGet.mockReset();
  listNotes.mockReset();
  addNote.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.endsWith("/documents/5")) return Promise.resolve(doc);
    if (url.includes("/versions")) return Promise.resolve([]);
    return Promise.resolve({});
  });
  listNotes.mockResolvedValue([]);
});

test("renders the document title, viewer, and notes section", async () => {
  render(<LabDocumentDetailPage />);
  await waitFor(() => expect(screen.getByText("Centrifuge Manual")).toBeInTheDocument());
  expect(screen.getByTestId("viewer")).toBeInTheDocument();
  expect(screen.getByText(/notes \(0\)/i)).toBeInTheDocument();
});

test("renders existing notes", async () => {
  listNotes.mockResolvedValue([
    {
      id: 11,
      body: "Check section 3.",
      user: { id: 2, name: "Bob", email: "bob@x.com" },
      created_at: "2026-01-16T10:00:00Z",
      deleted: false,
    },
  ]);
  render(<LabDocumentDetailPage />);
  await waitFor(() => expect(screen.getByText("Check section 3.")).toBeInTheDocument());
});

test("posting a note calls addNote and refreshes the list", async () => {
  addNote.mockResolvedValue({ id: 12, body: "New note", user: null, created_at: "", deleted: false });
  render(<LabDocumentDetailPage />);
  await waitFor(() => screen.getByText("Centrifuge Manual"));
  fireEvent.change(screen.getByLabelText(/add a note/i), { target: { value: "New note" } });
  fireEvent.click(screen.getByRole("button", { name: /^post$/i }));
  await waitFor(() => expect(addNote).toHaveBeenCalledWith(5, "New note"));
});
