import { render, screen, waitFor } from "@testing-library/react";
import { MySessionsWidget } from "./MySessionsWidget";

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

test("combines notebook + work-node sessions and shows only active ones", async () => {
  // First call = notebooks, second = work-nodes (Promise.all order)
  mockGet
    .mockResolvedValueOnce({
      sessions: [{ id: 1, session_type: "jupyter", status: "running", proxy_url: "http://nb" }],
    })
    .mockResolvedValueOnce({
      sessions: [{ id: 2, session_type: "shell", status: "stopped", access_url: null }],
    });
  render(<MySessionsWidget />);
  await waitFor(() => expect(screen.getByText("Notebook: jupyter")).toBeInTheDocument());
  // the stopped work node is filtered out
  expect(screen.queryByText("Work node: shell")).not.toBeInTheDocument();
});

test("empty when no active sessions", async () => {
  mockGet
    .mockResolvedValueOnce({ sessions: [{ id: 1, session_type: "jupyter", status: "stopped" }] })
    .mockResolvedValueOnce({ sessions: [] });
  render(<MySessionsWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});
