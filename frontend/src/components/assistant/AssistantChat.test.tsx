import { render, screen, fireEvent } from "@testing-library/react";
import { AssistantChat } from "./AssistantChat";

jest.mock("@/hooks/usePermissions", () => ({ usePermissions: jest.fn() }));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
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

describe("AssistantChat", () => {
  it("shows a permission gate when the user lacks assistant:use", async () => {
    mockUsePermissions.mockReturnValue({ canAccess: () => false, loading: false });
    render(<AssistantChat />);
    expect(await screen.findByText(/permission to use the Assistant/i)).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("shows the availability reason when the provider is not tool-capable", async () => {
    allowUse();
    mockGet.mockResolvedValue({
      enabled: false,
      reason: "Your active LLM provider does not support the assistant.",
    });
    render(<AssistantChat />);
    expect(await screen.findByText(/does not support the assistant/i)).toBeInTheDocument();
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

    render(<AssistantChat />);
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
        plan_steps: [{ tool: "launch_run", args: { experiment_id: 3, pipeline_key: "nf-core/rnaseq" } }],
        reason: null,
      })
      .mockResolvedValueOnce({
        status: "approved",
        plan_id: 7,
        executed: false,
        result: { pipeline_key: "nf-core/rnaseq", experiment_id: 3 },
      });

    render(<AssistantChat />);
    const input = await screen.findByLabelText("Message");
    fireEvent.change(input, { target: { value: "run it" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    // The plan card surfaces the proposed pipeline BEFORE confirming.
    expect(await screen.findByTestId("plan-confirm-card")).toBeInTheDocument();
    expect(screen.getByText("nf-core/rnaseq")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i }));

    expect(await screen.findByTestId("plan-approved")).toBeInTheDocument();
    expect(await screen.findByText(/not started/i)).toBeInTheDocument();
    // Even a build-only confirm links the user to the experiment instead of leaving them adrift.
    expect(screen.getByRole("link", { name: /open experiment/i })).toHaveAttribute("href", "/experiments/3");
  });

  it("links to the experiment it just created so the user isn't left to find it", async () => {
    allowUse();
    mockGet.mockResolvedValue({ enabled: true, reason: null });
    mockPost
      .mockResolvedValueOnce({ id: 1, status: "active", provider: "anthropic", model: "x" })
      .mockResolvedValueOnce({
        status: "awaiting_confirmation",
        text: null,
        action_plan_id: 9,
        plan_steps: [{ tool: "create_experiment", args: { name: "Mouse Gut Serotonin Investigation" } }],
        reason: null,
      })
      .mockResolvedValueOnce({
        status: "approved",
        plan_id: 9,
        executed: true,
        result: { experiment_id: 8, name: "Mouse Gut Serotonin Investigation", code: "bioae-0004", status: "registered" },
      });

    render(<AssistantChat />);
    const input = await screen.findByLabelText("Message");
    fireEvent.change(input, { target: { value: "start a new mouse gut experiment" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByTestId("plan-confirm-card")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i }));

    expect(await screen.findByText(/Created experiment "Mouse Gut Serotonin Investigation"/i)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /open experiment/i });
    expect(link).toHaveAttribute("href", "/experiments/8");
  });
});
