import { extractErrorMessage, api, ApiError } from "./api";

describe("extractErrorMessage", () => {
  test("returns a plain string detail unchanged", () => {
    expect(extractErrorMessage({ detail: "boom" })).toBe("boom");
  });

  test("maps a known string code to a friendly message", () => {
    expect(extractErrorMessage({ detail: "review_in_progress" })).toMatch(
      /already in progress/i,
    );
  });

  test("maps a structured 409 conflict (nested code) to a friendly message", () => {
    // Shape raised by JobAlreadyRunning: detail is an object, not a string.
    const body = {
      detail: {
        detail: "review_in_progress",
        existing_job_id: 12,
        existing_agent_review_id: 34,
      },
    };
    expect(extractErrorMessage(body)).toMatch(/already in progress/i);
  });

  test("falls back to a nested string detail when the code is unknown", () => {
    expect(extractErrorMessage({ detail: { detail: "some other thing" } })).toBe(
      "some other thing",
    );
  });

  test("joins validation arrays", () => {
    expect(
      extractErrorMessage({ detail: [{ msg: "a" }, { msg: "b" }] }),
    ).toBe("a; b");
  });

  test("falls back to a generic message for an opaque object", () => {
    expect(extractErrorMessage({ detail: { foo: 1 } })).toBe("Request failed");
  });
});

describe("ApiError carries the structured domain-error envelope", () => {
  const realFetch = global.fetch;
  afterEach(() => {
    global.fetch = realFetch;
  });

  test("a domain error surfaces code and details to the caller", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({
        detail: "Some selected samples have no linked input files",
        code: "samples_missing_files",
        details: { samples_without_files: [{ id: 9, external_id: "SAMPLE-102" }] },
      }),
    }) as unknown as typeof fetch;

    expect.assertions(3);
    try {
      await api.post("/api/pipeline-runs", {});
    } catch (err) {
      const e = err as ApiError;
      expect(e).toBeInstanceOf(ApiError);
      expect(e.code).toBe("samples_missing_files");
      expect(e.details?.samples_without_files).toEqual([{ id: 9, external_id: "SAMPLE-102" }]);
    }
  });
});
