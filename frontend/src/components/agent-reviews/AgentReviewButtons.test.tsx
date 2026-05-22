import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AgentReviewButtons } from "./AgentReviewButtons";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
}));

const mockCanAccess = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: mockCanAccess,
    roleName: "comp_bio",
    loading: false,
    permissions: new Set(),
  }),
}));

jest.mock("./SectionBuilderModal", () => ({
  SectionBuilderModal: () => <div data-testid="section-builder" />,
}));

const mockGet = api.get as jest.Mock;

function availability(enabled: boolean) {
  mockGet.mockImplementation((url: string) =>
    url === "/api/agent_reviews/availability"
      ? Promise.resolve({ enabled })
      : Promise.resolve({}),
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

it("shows the trigger when AI is available and the user can use it (uses availability, not the admin providers endpoint)", async () => {
  mockCanAccess.mockReturnValue(true);
  availability(true);
  render(
    <AgentReviewButtons mode="pipeline_run" runId={42} experimentId={1} pipelineStatus="completed" />,
  );
  expect(await screen.findByText("Review this pipeline run")).toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledWith("/api/agent_reviews/availability");
  expect(mockGet).not.toHaveBeenCalledWith("/api/integrations/llm/providers");
});

it("hides the trigger when AI is not available", async () => {
  mockCanAccess.mockReturnValue(true);
  availability(false);
  render(
    <AgentReviewButtons mode="pipeline_run" runId={42} experimentId={1} pipelineStatus="completed" />,
  );
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByText("Review this pipeline run")).not.toBeInTheDocument();
});

it("hides the trigger and does not query availability without llm_integration:use", () => {
  mockCanAccess.mockReturnValue(false);
  availability(true);
  render(
    <AgentReviewButtons mode="pipeline_run" runId={42} experimentId={1} pipelineStatus="completed" />,
  );
  expect(screen.queryByText("Review this pipeline run")).not.toBeInTheDocument();
  expect(mockGet).not.toHaveBeenCalled();
});

it("hides the trigger when the pipeline run is not completed", async () => {
  mockCanAccess.mockReturnValue(true);
  availability(true);
  render(
    <AgentReviewButtons mode="pipeline_run" runId={42} experimentId={1} pipelineStatus="running" />,
  );
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByText("Review this pipeline run")).not.toBeInTheDocument();
});

it("opens the prompt-builder when clicked", async () => {
  mockCanAccess.mockReturnValue(true);
  availability(true);
  render(
    <AgentReviewButtons mode="pipeline_run" runId={42} experimentId={1} pipelineStatus="completed" />,
  );
  fireEvent.click(await screen.findByText("Review this pipeline run"));
  expect(screen.getByTestId("section-builder")).toBeInTheDocument();
});
