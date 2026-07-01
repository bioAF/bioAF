import { render, screen, waitFor } from "@testing-library/react";
import AuditLogPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => null }));
jest.mock("@/components/layout/Header", () => ({ Header: () => null }));
jest.mock("@/components/layout/Breadcrumb", () => ({ Breadcrumb: () => null }));
jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

function entry(overrides: Record<string, unknown>) {
  return {
    id: 1,
    timestamp: "2026-06-30T00:00:00Z",
    user: { id: 1, email: "founder@test.com", name: "Founder" },
    entity_type: "pipeline_run",
    entity_id: 7,
    action: "launch",
    details: {},
    previous_value: null,
    ...overrides,
  };
}

describe("AuditLogPage via-assistant marker", () => {
  test("an agent-driven action shows a 'via assistant' badge (still attributed to the user)", async () => {
    mockGet.mockResolvedValue({
      entries: [entry({ details: { pipeline_key: "nf-core/rnaseq", via_assistant: true } })],
      total: 1,
    });

    render(<AuditLogPage />);

    await waitFor(() => expect(screen.getByTestId("via-assistant-badge")).toBeInTheDocument());
    // The action stays attributed to the user; the badge only notes the agent was used.
    expect(screen.getByText("founder@test.com")).toBeInTheDocument();
  });

  test("a hand-taken action shows no 'via assistant' badge", async () => {
    mockGet.mockResolvedValue({
      entries: [entry({ details: { pipeline_key: "nf-core/rnaseq" } })],
      total: 1,
    });

    render(<AuditLogPage />);

    await waitFor(() => expect(screen.getByText("founder@test.com")).toBeInTheDocument());
    expect(screen.queryByTestId("via-assistant-badge")).not.toBeInTheDocument();
  });
});
