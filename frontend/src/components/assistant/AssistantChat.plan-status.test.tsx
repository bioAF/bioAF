/**
 * Plan cards show the server's plan state truthfully (plan 12.5).
 *
 * A confirmed plan now runs step by step and keeps each completed step, so a later failure leaves a
 * partly executed plan with status "failed", and a plan being executed is "executing". The card must
 * say so, mark which steps ran, failed, or never ran, and only offer Confirm/Cancel while the plan
 * is still proposed. After a confirmation error the chat re-reads the plan instead of guessing: a
 * permission denial leaves the plan proposed, a partial failure does not.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { AssistantChat } from "./AssistantChat";
import { api, ApiError } from "@/lib/api";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
    }
  },
}));

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
const createdAt = "2026-11-08T18:00:00Z";

const proposedSteps = [
  { tool: "create_experiment", args: { name: "Already created" }, consequence_class: "mutating" },
  { tool: "create_sample", args: { experiment_id: 999999 }, consequence_class: "mutating" },
  { tool: "create_experiment", args: { name: "Never reached" }, consequence_class: "mutating" },
];
const failedSteps = [
  { ...proposedSteps[0], result: { experiment_id: 42 } },
  { ...proposedSteps[1], outcome: "failed" },
  { ...proposedSteps[2], outcome: "not_run" },
];

function serve(plan: { status: string; steps: unknown[] }) {
  mockGet.mockImplementation(async (url: string) => {
    if (url === "/api/assistant/availability") return { enabled: true, reason: null };
    if (url === "/api/assistant/conversations")
      return {
        conversations: [
          { id: 5, title: "Failure review", preview: "", status: "active", message_count: 1, created_at: createdAt, updated_at: createdAt },
        ],
        total: 1,
      };
    if (url === "/api/assistant/conversations/5/messages")
      return { id: 5, title: "Failure review", messages: [], plans: [{ id: 7, created_at: createdAt, ...plan }] };
    throw new Error(`Unexpected test request: ${url}`);
  });
}

async function reopen() {
  render(<AssistantChat />);
  fireEvent.click(await screen.findByRole("button", { name: /^history$/i }));
  fireEvent.click(await screen.findByText("Failure review"));
  return screen.findByTestId("plan-confirm-card");
}

async function proposeThenConfirm(confirmError: Error) {
  mockPost
    .mockResolvedValueOnce({ id: 5, status: "active", provider: "openai", model: "gpt-6.1-sol" })
    .mockResolvedValueOnce({ status: "awaiting_confirmation", text: null, action_plan_id: 7, plan_steps: proposedSteps, reason: null })
    .mockRejectedValueOnce(confirmError);
  render(<AssistantChat />);
  fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "create these" } });
  fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^confirm$/i }));
}

function expectNoControls() {
  expect(screen.queryByRole("button", { name: /^confirm$/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^cancel$/i })).not.toBeInTheDocument();
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

test("a reopened failed plan says it stopped partway and marks each step", async () => {
  serve({ status: "failed", steps: failedSteps });
  const card = await reopen();

  expect(screen.queryByTestId("plan-approved")).not.toBeInTheDocument();
  expect(screen.getByTestId("plan-failed")).toBeInTheDocument();
  const outcomes = within(card).getAllByTestId("plan-step-outcome").map((el) => el.textContent);
  expect(outcomes).toEqual(["Done", "Failed", "Not run"]);
  expectNoControls();
});

test("a reopened executing plan is shown as in progress, not approved", async () => {
  serve({ status: "executing", steps: proposedSteps });
  await reopen();

  expect(screen.queryByTestId("plan-approved")).not.toBeInTheDocument();
  expect(screen.getByTestId("plan-executing")).toHaveTextContent(/in progress/i);
  expectNoControls();
});

test("a reopened proposed plan can still be confirmed or cancelled", async () => {
  serve({ status: "proposed", steps: proposedSteps });
  await reopen();

  expect(screen.getByRole("button", { name: /^confirm$/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^cancel$/i })).toBeInTheDocument();
  expect(screen.queryByTestId("plan-step-outcome")).not.toBeInTheDocument();
});

test("a reopened approved plan marks every step done", async () => {
  serve({ status: "approved", steps: failedSteps.map((s, i) => (i === 0 ? s : { ...proposedSteps[i], result: {} })) });
  const card = await reopen();

  expect(screen.getByTestId("plan-approved")).toBeInTheDocument();
  expect(within(card).getAllByTestId("plan-step-outcome").map((el) => el.textContent)).toEqual(["Done", "Done", "Done"]);
  expectNoControls();
});

test("a partial confirmation failure re-reads the plan and removes confirm and cancel", async () => {
  serve({ status: "failed", steps: failedSteps });
  await proposeThenConfirm(new ApiError(400, "action 'create_sample' failed: experiment not found"));
  await screen.findByText("action 'create_sample' failed: experiment not found");

  expect(await screen.findByTestId("plan-failed")).toBeInTheDocument();
  expect(screen.getAllByTestId("plan-step-outcome").map((el) => el.textContent)).toEqual(["Done", "Failed", "Not run"]);
  expectNoControls();
  expect(screen.queryByText(/Nothing was run/)).not.toBeInTheDocument();
});

test("a confirmation the server refused before running anything leaves the plan confirmable", async () => {
  serve({ status: "proposed", steps: proposedSteps });
  await proposeThenConfirm(new ApiError(403, "permission denied for pipelines:launch"));
  await screen.findByText("permission denied for pipelines:launch");

  expect(screen.queryByTestId("plan-failed")).not.toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /^confirm$/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^cancel$/i })).toBeInTheDocument();
});

test("when the plan cannot be re-read after a confirmation error, its outcome is unknown and no controls return", async () => {
  serve({ status: "failed", steps: failedSteps });
  const serveImpl = mockGet.getMockImplementation()!;
  mockGet.mockImplementation((url: string) =>
    url === "/api/assistant/conversations/5/messages" ? Promise.reject(new Error("connection unavailable")) : serveImpl(url),
  );
  await proposeThenConfirm(new ApiError(400, "action 'create_sample' failed: experiment not found"));
  await screen.findByText("action 'create_sample' failed: experiment not found");

  expect(await screen.findByTestId("plan-unknown")).toHaveTextContent(/could not be checked/i);
  expectNoControls();
  expect(screen.queryByTestId("plan-approved")).not.toBeInTheDocument();
  expect(screen.queryByText(/Nothing was run/)).not.toBeInTheDocument();
});
