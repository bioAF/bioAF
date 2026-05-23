import { render, screen, waitFor } from "@testing-library/react";
import { MyReadingListWidget } from "./MyReadingListWidget";

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

test("requests unread papers and renders them", async () => {
  mockGet.mockResolvedValueOnce({
    items: [{ id: 4, title: "To read later", journal: "Cell" }],
    total: 1,
  });
  render(<MyReadingListWidget />);
  await waitFor(() => expect(screen.getByText("To read later")).toBeInTheDocument());
  expect(mockGet).toHaveBeenCalledWith(
    "/api/literature/papers?reading_status=unread&page_size=6",
  );
});

test("empty state", async () => {
  mockGet.mockResolvedValueOnce({ items: [], total: 0 });
  render(<MyReadingListWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<MyReadingListWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
