/**
 * A failed load must not look like an empty account.
 *
 * The list previously swallowed its error and fell through to "No work nodes in
 * this view", so a backend outage told the user their running, billing VMs were
 * gone. A failed Stop was silent too: the spinner cleared and the node stayed up.
 */
import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";
import WorkNodesPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ capabilities: {}, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

test("a failed node list shows an error with a retry, not an empty state", async () => {
  mockGet.mockImplementation((url: string) => {
    if (String(url).includes("/work-nodes/sessions")) {
      return Promise.reject(new Error("Backend unavailable"));
    }
    return Promise.resolve({ repos: [], sessions: [], profiles: [] });
  });

  render(<WorkNodesPage />);

  await waitFor(() =>
    expect(screen.getByText(/work nodes could not be loaded/i)).toBeInTheDocument(),
  );
  // The dangerous message: it says the user has nothing, when really we do not know.
  expect(screen.queryByText(/no work nodes in this view/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /retry|try again/i })).toBeInTheDocument();
});

test("a genuinely empty list still says so", async () => {
  mockGet.mockResolvedValue({ repos: [], sessions: [], profiles: [] });
  render(<WorkNodesPage />);
  await waitFor(() =>
    expect(screen.getByText(/no work nodes in this view/i)).toBeInTheDocument(),
  );
  expect(screen.queryByText(/work nodes could not be loaded/i)).not.toBeInTheDocument();
});
