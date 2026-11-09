/**
 * Terraform progress is a consequential POST whose answer is a server-sent event stream. It goes
 * through the shared client: the status policy applies before the stream opens, the
 * existing event parsing is unchanged, closing the modal stops observing, and the POST is never
 * sent again on its own.
 */
import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import { TextDecoder, TextEncoder } from "util";
import { fakeResponse } from "@/testing/fakeServer";

const mockNavigateToLogin = jest.fn();
jest.mock("@/lib/navigation", () => ({ navigateToLogin: () => mockNavigateToLogin() }));

import { TerraformProgressModal } from "./TerraformProgressModal";
import { getToken } from "@/lib/auth";
import { startSession } from "@/lib/session";
import { clearMaintenance, getMaintenance } from "@/lib/maintenance";

const realFetch = global.fetch;
const realEnv = process.env.NEXT_PUBLIC_API_URL;
const encoder = new TextEncoder();

/** A response whose body yields the given chunks, then waits until `end()` is called. */
function streamResponse(chunks: string[]) {
  let finish: () => void = () => {};
  const ended = new Promise<void>((resolve) => (finish = resolve));
  const queue = chunks.map((c) => encoder.encode(c));
  const response = {
    ...fakeResponse({ status: 200 }),
    body: {
      getReader: () => ({
        read: async () => {
          const next = queue.shift();
          if (next) return { done: false, value: next };
          await ended;
          return { done: true, value: undefined };
        },
      }),
    },
  } as unknown as Response;
  return { response, end: () => finish() };
}

function serve(response: Response) {
  const fetchMock = jest.fn(async () => response);
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function openModal() {
  return render(
    <TerraformProgressModal title="Deploy" sseUrl="/api/v1/infrastructure/stack/deploy" onComplete={() => {}} onClose={() => {}} />,
  );
}

beforeAll(() => {
  Object.assign(global, { TextDecoder });
});

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = "https://api.lab.org";
  localStorage.clear();
  startSession("live.jwt");
  mockNavigateToLogin.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  if (realEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = realEnv;
  clearMaintenance();
  jest.restoreAllMocks();
});

test("posts once with the session credential and reads events split across chunks", async () => {
  const { response, end } = streamResponse([
    'data: {"event_type": "progress", "message": "Creating storage", "resources_total": 4, "resources_completed": 1}\n\ndata: {"event_type": "apply_',
    'complete", "message": "done"}\n\n',
  ]);
  const fetchMock = serve(response);

  openModal();

  expect(await screen.findByTestId("tf-modal-done-btn")).toBeInTheDocument();
  end();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("https://api.lab.org/api/v1/infrastructure/stack/deploy");
  expect(init.method).toBe("POST");
  const headers = init.headers as Record<string, string>;
  expect(headers.Authorization).toBe("Bearer live.jwt");
  expect(headers.Accept).toBe("text/event-stream");
});

test("a stream that ends without a terminal event is reported", async () => {
  const { response, end } = streamResponse(['data: {"event_type": "progress", "message": "Creating storage"}\n\n']);
  serve(response);
  openModal();
  end();
  expect(await screen.findByTestId("tf-modal-error")).toHaveTextContent("Stream ended unexpectedly");
});

test("a refusal before the stream opens shows the backend's explanation", async () => {
  serve(fakeResponse({ status: 409, json: { detail: "Another deployment is running." } }));
  openModal();
  expect(await screen.findByTestId("tf-modal-error")).toHaveTextContent("Another deployment is running.");
});

test("an expired session before the stream opens ends the session", async () => {
  serve(fakeResponse({ status: 401, json: { detail: "Unauthorized" } }));
  openModal();
  await waitFor(() => expect(mockNavigateToLogin).toHaveBeenCalled());
  expect(getToken()).toBeNull();
});

test("maintenance before the stream opens is reported to the shell", async () => {
  serve(fakeResponse({ status: 503, json: { status: "maintenance", reason: "upgrade_in_progress" } }));
  openModal();
  expect(await screen.findByTestId("tf-modal-error")).toHaveTextContent(
    "bioAF is finishing an upgrade. Try again in a minute.",
  );
  expect(getMaintenance()?.reason).toBe("upgrade_in_progress");
});

test("closing the modal stops observing without sending the operation again", async () => {
  const { response } = streamResponse([]);
  const fetchMock = serve(response);
  const { unmount } = openModal();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  const signal = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].signal!;

  unmount();

  expect(signal.aborted).toBe(true);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test("React's development remount does not send the deployment twice", async () => {
  const { response } = streamResponse([]);
  const fetchMock = serve(response);
  render(
    <React.StrictMode>
      <TerraformProgressModal title="Deploy" sseUrl="/api/v1/infrastructure/stack/deploy" onComplete={() => {}} onClose={() => {}} />
    </React.StrictMode>,
  );
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
