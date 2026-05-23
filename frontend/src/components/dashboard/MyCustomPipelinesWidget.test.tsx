import { render, screen, waitFor } from "@testing-library/react";
import { MyCustomPipelinesWidget } from "./MyCustomPipelinesWidget";

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

test("renders custom pipelines (bare array) with a view-all link", async () => {
  mockGet.mockResolvedValueOnce([
    { id: 7, name: "My RNA pipeline", pipeline_key: "my_rna", updated_at: new Date().toISOString() },
  ]);
  render(<MyCustomPipelinesWidget />);
  await waitFor(() => expect(screen.getByText("My RNA pipeline")).toBeInTheDocument());
  expect(screen.getByText("View custom pipelines")).toHaveAttribute("href", "/pipelines/custom");
});

test("empty state with no pipelines", async () => {
  mockGet.mockResolvedValueOnce([]);
  render(<MyCustomPipelinesWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("error state on fetch failure", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<MyCustomPipelinesWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
