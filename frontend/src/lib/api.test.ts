import { extractErrorMessage } from "./api";

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
