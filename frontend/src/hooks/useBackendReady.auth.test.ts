/**
 * Readiness is an unauthenticated probe: it never carries the session credential, and
 * it resolves its URL the same way the API client does.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { useBackendReady } from "./useBackendReady";
import { startSession, endSession } from "@/lib/session";

const realFetch = global.fetch;
const realEnv = process.env.NEXT_PUBLIC_API_URL;

afterEach(() => {
  global.fetch = realFetch;
  if (realEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = realEnv;
  sessionStorage.clear();
  endSession();
});

test("the readiness probe sends no session credential, even with a session", async () => {
  process.env.NEXT_PUBLIC_API_URL = "";
  startSession("live.jwt");
  const fetchMock = jest.fn(async () => ({ ok: true, status: 200, json: async () => ({ status: "ok" }) }));
  global.fetch = fetchMock as unknown as typeof fetch;

  const { result } = renderHook(() => useBackendReady());

  await waitFor(() => expect(result.current.ready).toBe(true));
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/health/ready");
  expect((init.headers as Record<string, string> | undefined)?.Authorization).toBeUndefined();
});
