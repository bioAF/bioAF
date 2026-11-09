/**
 * Test-only stand-in for the application server behind the real API client. Routes are matched
 * by URL substring, first match wins; unmatched requests answer 404. Not imported by application
 * code.
 */
export interface FakeReply {
  status?: number;
  json?: unknown;
  /** A non-JSON body (an HTML error page, a CSV, a report). */
  text?: string;
  headers?: Record<string, string>;
}

export interface RecordedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

export function fakeResponse(reply: FakeReply): Response {
  const status = reply.status ?? 200;
  const headers = Object.fromEntries(
    Object.entries(reply.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v]),
  );
  const raw = reply.text ?? (reply.json === undefined ? "" : JSON.stringify(reply.json));
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => headers[name.toLowerCase()] ?? null },
    json: async () => {
      if (reply.json === undefined) throw new SyntaxError("Unexpected token in JSON");
      return reply.json;
    },
    text: async () => raw,
    blob: async () => new Blob([raw]),
  } as unknown as Response;
}

export function fakeServer(routes: Array<[string, FakeReply | ((req: RecordedRequest) => FakeReply)]>) {
  const requests: RecordedRequest[] = [];
  const fetchMock = jest.fn(async (url: RequestInfo | URL, init: RequestInit = {}) => {
    const req: RecordedRequest = {
      url: String(url),
      method: init.method ?? "GET",
      headers: (init.headers ?? {}) as Record<string, string>,
      body: init.body,
    };
    requests.push(req);
    const route = routes.find(([fragment]) => req.url.includes(fragment));
    if (!route) return fakeResponse({ status: 404, json: { detail: "Not found" } });
    const reply = typeof route[1] === "function" ? route[1](req) : route[1];
    return fakeResponse(reply);
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return { requests, fetchMock };
}
