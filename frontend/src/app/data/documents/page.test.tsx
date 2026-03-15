import { render, screen, waitFor } from "@testing-library/react";
import DataDocumentsPage from "./page";

// Mock next/link
jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

// Mock layout components
jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <nav data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <header data-testid="header" />,
}));

// Mock the api module
jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
  },
}));

// Mock auth
jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  window.confirm = jest.fn(() => true);
});

test("renders documents from paginated response", async () => {
  mockGet.mockResolvedValue({
    documents: [
      { id: 1, title: "Protocol v2", created_at: "2026-03-01T00:00:00Z" },
      { id: 2, title: "Lab Notes", created_at: "2026-03-02T00:00:00Z" },
    ],
    total: 2,
    page: 1,
    page_size: 25,
  });

  render(<DataDocumentsPage />);

  await waitFor(() => {
    expect(screen.getByText("Protocol v2")).toBeInTheDocument();
    expect(screen.getByText("Lab Notes")).toBeInTheDocument();
  });
});

test("shows empty state when no documents", async () => {
  mockGet.mockResolvedValue({
    documents: [],
    total: 0,
    page: 1,
    page_size: 25,
  });

  render(<DataDocumentsPage />);

  await waitFor(() => {
    expect(screen.getByText("No documents found.")).toBeInTheDocument();
  });
});
