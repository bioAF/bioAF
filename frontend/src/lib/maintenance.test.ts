/**
 * While the backend finishes a database upgrade it answers every API request with 503 and
 * `{"status": "maintenance", ...}` (plan 9). The API client reports that, once, to the shell, so an
 * already-open tab can say what is happening instead of failing each request on its own.
 */
import { api, ApiError } from "@/lib/api";
import {
  getMaintenance,
  clearMaintenance,
  subscribeMaintenance,
} from "@/lib/maintenance";

const MAINTENANCE = {
  status: "maintenance",
  detail: "bioAF is finishing an upgrade and is not accepting requests yet.",
  reason: "upgrade_in_progress",
  upgrade: { status: "in_progress", step: "migrate" },
};

const realFetch = global.fetch;
afterEach(() => {
  global.fetch = realFetch;
  clearMaintenance();
  sessionStorage.clear();
});

function respondWith(status: number, body: unknown) {
  global.fetch = jest.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }) as unknown as typeof fetch;
}

test("a maintenance answer is reported to the shell and still fails the request", async () => {
  sessionStorage.setItem("bioaf_backend_ready", "true");
  const seen: unknown[] = [];
  const unsubscribe = subscribeMaintenance((m) => seen.push(m));
  respondWith(503, MAINTENANCE);

  const error = await api.get("/api/projects").catch((e) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).status).toBe(503);
  expect(getMaintenance()?.reason).toBe("upgrade_in_progress");
  expect(seen).toHaveLength(1);
  // A cached "ready" must not hide the maintenance from a reload or another navigation.
  expect(sessionStorage.getItem("bioaf_backend_ready")).toBeNull();
  unsubscribe();
});

test("an ordinary 503 is not mistaken for maintenance", async () => {
  respondWith(503, { detail: "Service unavailable" });
  await expect(api.get("/api/projects")).rejects.toBeInstanceOf(ApiError);
  expect(getMaintenance()).toBeNull();
});

test("the failed request's message follows the maintenance reason", async () => {
  respondWith(503, MAINTENANCE);
  const running = await api.get("/api/projects").catch((e) => e);
  expect((running as ApiError).message).toBe(
    "bioAF is finishing an upgrade. Try again in a minute.",
  );

  respondWith(503, {
    ...MAINTENANCE,
    reason: "upgrade_failed",
    upgrade: { status: "failed", step: "validate" },
  });
  const failed = await api.get("/api/projects").catch((e) => e);
  expect((failed as ApiError).status).toBe(503);
  expect((failed as ApiError).message).toBe(
    "bioAF\u2019s last upgrade failed. An administrator needs to review the failure and retry.",
  );
});
