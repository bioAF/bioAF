import { render, screen, fireEvent } from "@testing-library/react";
import AddReferencePage from "./page";

const mockPush = jest.fn();
const mockSearch = new URLSearchParams();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => mockSearch,
}));

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <nav data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <header data-testid="header" />,
}));

// Stub the two forms so this page-level test only exercises the toggle.
jest.mock("@/components/references/UploadReferenceForm", () => ({
  UploadReferenceForm: () => <div data-testid="upload-form" />,
}));
jest.mock("@/components/references/UrlImportReferenceForm", () => ({
  UrlImportReferenceForm: () => <div data-testid="url-form" />,
}));

beforeEach(() => {
  mockPush.mockReset();
  for (const k of Array.from(mockSearch.keys())) mockSearch.delete(k);
});

describe("Add Reference Data page - mode toggle", () => {
  it("Defaults to the Upload mode", () => {
    render(<AddReferencePage />);
    expect(screen.getByTestId("upload-form")).toBeInTheDocument();
    expect(screen.queryByTestId("url-form")).not.toBeInTheDocument();
  });

  it("Switches to URL Import when the URL toggle is clicked", () => {
    render(<AddReferencePage />);
    fireEvent.click(screen.getByRole("button", { name: /url import/i }));
    expect(screen.getByTestId("url-form")).toBeInTheDocument();
    expect(screen.queryByTestId("upload-form")).not.toBeInTheDocument();
  });

  it("Honors ?mode=url on the URL to deep-link into the URL Import side", () => {
    mockSearch.set("mode", "url");
    render(<AddReferencePage />);
    expect(screen.getByTestId("url-form")).toBeInTheDocument();
    expect(screen.queryByTestId("upload-form")).not.toBeInTheDocument();
  });
});
