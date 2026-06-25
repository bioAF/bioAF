import { render, screen, fireEvent } from "@testing-library/react";
import AssistantPage from "./page";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => null }));
jest.mock("@/components/layout/Header", () => ({ Header: () => null }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: jest.fn() }));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
  ApiError: class ApiError extends Error {},
}));

import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";

const mockUsePermissions = usePermissions as jest.Mock;
const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

function allowUse() {
  mockUsePermissions.mockReturnValue({ canAccess: () => true, loading: false });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockUsePermissions.mockReset();
});

describe("AssistantPage", () => {
  it("shows a permission gate when the user lacks assistant:use", async () => {
    mockUsePermissions.mockReturnValue({ canAccess: () => false, loading: false });
    render(<AssistantPage />);
    expect(await screen.findByText(/permission to use the Assistant/i)).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("shows the availability reason when the provider is not tool-capable", async () => {
    allowUse();
    mockGet.mockResolvedValue({
      enabled: false,
      reason: "Your active LLM provider does not support the assistant.",
    });
    render(<AssistantPage />);
    expect(
      await screen.findByText(/does not support the assistant/i),
    ).toBeInTheDocument();
  });

  it("sends a message and renders the assistant's answer", async () => {
    allowUse();
    mockGet.mockResolvedValue({ enabled: true, reason: null });
    mockPost
      .mockResolvedValueOnce({ id: 1, status: "active", provider: "anthropic", model: "x" })
      .mockResolvedValueOnce({
        status: "answered",
        text: "I recommend nf-core/rnaseq.",
        action_plan_id: null,
        plan_steps: null,
        reason: null,
      });

    render(<AssistantPage />);
    const input = await screen.findByLabelText("Message");
    fireEvent.change(input, { target: { value: "recommend a pipeline" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByText("I recommend nf-core/rnaseq.")).toBeInTheDocument();
    expect(screen.getByText("recommend a pipeline")).toBeInTheDocument();
  });

  it("shows the plan, and confirming it reports approval without launching", async () => {
    allowUse();
    mockGet.mockResolvedValue({ enabled: true, reason: null });
    mockPost
      .mockResolvedValueOnce({ id: 1, status: "active", provider: "anthropic", model: "x" })
      .mockResolvedValueOnce({
        status: "awaiting_confirmation",
        text: null,
        action_plan_id: 7,
        plan_steps: [
          { tool: "launch_run", args: { experiment_id: 3, pipeline_key: "nf-core/rnaseq" } },
        ],
        reason: null,
      })
      .mockResolvedValueOnce({
        status: "approved",
        plan_id: 7,
        launch_request: { pipeline_key: "nf-core/rnaseq", experiment_id: 3 },
      });

    render(<AssistantPage />);
    const input = await screen.findByLabelText("Message");
    fireEvent.change(input, { target: { value: "run it" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    // The plan card surfaces the proposed pipeline BEFORE confirming.
    expect(await screen.findByTestId("plan-confirm-card")).toBeInTheDocument();
    expect(screen.getByText("nf-core/rnaseq")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i }));

    expect(await screen.findByTestId("plan-approved")).toBeInTheDocument();
    expect(await screen.findByText(/Prepared launch request/i)).toBeInTheDocument();
  });
});
