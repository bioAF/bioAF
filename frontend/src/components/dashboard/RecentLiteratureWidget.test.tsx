import { render, screen, waitFor } from "@testing-library/react";
import { RecentLiteratureWidget } from "./RecentLiteratureWidget";

jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});
jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), getWithRetry: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.getWithRetry as jest.Mock;

beforeEach(() => mockGet.mockReset());

test("renders recently added papers, linking each to its detail page", async () => {
  mockGet.mockResolvedValueOnce({
    items: [
      { id: 9, title: "Single-cell atlas of the liver", journal: "Nature", created_at: new Date().toISOString(), comment_count: 2 },
    ],
    total: 1,
  });
  render(<RecentLiteratureWidget />);
  await waitFor(() =>
    expect(screen.getByText("Single-cell atlas of the liver")).toBeInTheDocument(),
  );
  expect(screen.getByText("Single-cell atlas of the liver").closest("a")).toHaveAttribute(
    "href",
    "/data/literature/papers/9",
  );
});

test("empty state", async () => {
  mockGet.mockResolvedValueOnce({ items: [], total: 0 });
  render(<RecentLiteratureWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<RecentLiteratureWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
