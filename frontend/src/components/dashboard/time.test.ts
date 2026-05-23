import { timeAgo, withinHours } from "./time";

function isoAgo(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

describe("timeAgo", () => {
  test("formats minutes and hours", () => {
    expect(timeAgo(isoAgo(5 * 60 * 1000))).toBe("5m ago");
    expect(timeAgo(isoAgo(3 * 3600 * 1000))).toBe("3h ago");
    expect(timeAgo(isoAgo(2 * 24 * 3600 * 1000))).toBe("2d ago");
  });

  test("recent is 'just now'", () => {
    expect(timeAgo(isoAgo(2000))).toBe("just now");
  });

  test("empty/invalid input is an empty string", () => {
    expect(timeAgo(null)).toBe("");
    expect(timeAgo(undefined)).toBe("");
    expect(timeAgo("not-a-date")).toBe("");
  });
});

describe("withinHours", () => {
  test("true inside the window, false outside", () => {
    expect(withinHours(isoAgo(2 * 3600 * 1000), 24)).toBe(true);
    expect(withinHours(isoAgo(30 * 3600 * 1000), 24)).toBe(false);
    expect(withinHours(isoAgo(30 * 60 * 1000), 1)).toBe(true);
    expect(withinHours(isoAgo(2 * 3600 * 1000), 1)).toBe(false);
  });

  test("null is never within", () => {
    expect(withinHours(null, 24)).toBe(false);
  });
});
