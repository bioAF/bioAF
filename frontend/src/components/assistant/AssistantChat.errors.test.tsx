/**
 * Assistant provider failures render the server's controlled message (plan 12.5, finding L2).
 *
 * Sending a message used to fail with an unhandled 500 whose plain-text body made the chat say
 * "Unknown error". The backend now returns JSON `detail` for expected provider failures. These tests
 * go through the real API client (only `fetch` is stubbed) and check the rendered text, that a
 * provider credential failure does not end the bioAF session, and that a retry recovers.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { getToken, setToken } from "@/lib/auth";
import { AssistantChat } from "./AssistantChat";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

type Reply = { status: number; body: unknown };

const realFetch = global.fetch;
let messageReplies: Reply[] = [];

function stubServer() {
  global.fetch = jest.fn(async (url: RequestInfo | URL) => {
    const path = String(url);
    let reply: Reply;
    if (path.endsWith("/api/assistant/availability")) reply = { status: 200, body: { enabled: true, reason: null } };
    else if (path.endsWith("/api/assistant/conversations"))
      reply = { status: 200, body: { id: 9, status: "active", provider: "openai", model: "gpt-6.1-sol" } };
    else reply = messageReplies.shift() as Reply;
    return {
      ok: reply.status >= 200 && reply.status < 300,
      status: reply.status,
      json: async () => reply.body,
    } as Response;
  }) as unknown as typeof fetch;
}

async function send(text: string) {
  const input = await screen.findByLabelText("Message");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /send/i }));
}

beforeEach(() => {
  localStorage.clear();
  setToken("live.session.jwt");
  messageReplies = [];
  stubServer();
});
afterEach(() => {
  global.fetch = realFetch;
});

test("an incompatible provider request shows the configuration message, not Unknown error", async () => {
  const detail =
    "The AI provider rejected the assistant's request. An administrator should check the provider and model configuration in Settings > Integrations > LLMs.";
  messageReplies = [{ status: 502, body: { detail } }];
  render(<AssistantChat />);
  await send("what should I run?");

  expect(await screen.findByText(detail)).toBeInTheDocument();
  expect(screen.queryByText(/Unknown error/)).not.toBeInTheDocument();
});

test("a provider credential failure keeps the bioAF session", async () => {
  const detail =
    "The AI provider did not accept bioAF's credentials. An administrator should check the API key in Settings > Integrations > LLMs.";
  messageReplies = [{ status: 502, body: { detail } }];
  render(<AssistantChat />);
  await send("hello");

  expect(await screen.findByText(detail)).toBeInTheDocument();
  expect(getToken()).toBe("live.session.jwt");
});

test("a provider timeout shows the retry message and a retry gets the answer", async () => {
  const detail = "The AI provider did not respond in time. Try again.";
  messageReplies = [
    { status: 504, body: { detail } },
    {
      status: 200,
      body: { status: "answered", text: "Run nf-core/rnaseq.", action_plan_id: null, plan_steps: null, reason: null },
    },
  ];
  render(<AssistantChat />);
  await send("what should I run?");
  expect(await screen.findByText(detail)).toBeInTheDocument();

  await send("what should I run?");
  expect(await screen.findByText("Run nf-core/rnaseq.")).toBeInTheDocument();
});

test("provider rate limiting renders an actionable retry message", async () => {
  messageReplies = [{ status: 429, body: { detail: "The AI provider is limiting requests right now. Wait a minute and try again." } }];
  render(<AssistantChat />);
  await send("hello");

  // The shared client renders every 429 with its own wording (plan 13 owns that transport rule).
  expect(await screen.findByText(/try again/i)).toBeInTheDocument();
  expect(getToken()).toBe("live.session.jwt");
});
