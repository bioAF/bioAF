import { formatElapsed } from "./duration";
import { NOT_SET } from "./placeholders";

const START = "2026-03-11T10:00:00Z";
const at = (seconds: number) => new Date(Date.parse(START) + seconds * 1000).toISOString();
const NOW = Date.parse(START) + 7200 * 1000;

describe("formatElapsed", () => {
  test.each([
    [0, "0s", "0s"],
    [59, "59s", "59s"],
    [60, "1m", "1m 0s"],
    [61, "1m", "1m 1s"],
    [3599, "59m", "59m 59s"],
    [3600, "1h 0m", "1h 0m"],
    [3661, "1h 1m", "1h 1m"],
    [8 * 3600 + 5 * 60 + 7, "8h 5m", "8h 5m"],
    [30 * 3600, "30h 0m", "30h 0m"],
  ])("a run that took %ps reads %s in the list and %s on the detail page", (seconds, compact, detail) => {
    expect(formatElapsed(START, at(seconds), NOW, "compact")).toBe(compact);
    expect(formatElapsed(START, at(seconds), NOW, "detail")).toBe(detail);
  });

  test("an unfinished run is measured to the supplied time", () => {
    expect(formatElapsed(START, null, NOW, "compact")).toBe("2h 0m");
    expect(formatElapsed(START, undefined, Date.parse(START) + 90_500, "detail")).toBe("1m 30s");
  });

  test("a run that has not started has no duration", () => {
    expect(formatElapsed(null, null, NOW, "compact")).toBe(NOT_SET);
    expect(formatElapsed(undefined, at(5), NOW, "detail")).toBe(NOT_SET);
  });

  test("unreadable times have no duration", () => {
    expect(formatElapsed("garbage", at(5), NOW, "compact")).toBe(NOT_SET);
    expect(formatElapsed(START, "garbage", NOW, "detail")).toBe(NOT_SET);
  });

  test("an end before the start (clock skew) reads as zero, not a negative duration", () => {
    expect(formatElapsed(START, at(-5), NOW, "compact")).toBe("0s");
    expect(formatElapsed(START, null, Date.parse(START) - 3000, "detail")).toBe("0s");
  });
});
