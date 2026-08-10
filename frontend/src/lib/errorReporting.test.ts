import { logError, loadFailureMessage } from "./errorReporting";

describe("logError", () => {
  let spy: jest.SpyInstance;
  beforeEach(() => {
    spy = jest.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => spy.mockRestore());

  test("puts the real error in the logs, with the context that names what failed", () => {
    const cause = new TypeError("Cannot read properties of undefined (reading 'length')");
    logError("loading samples", cause);

    expect(spy).toHaveBeenCalledWith(
      expect.stringContaining("loading samples"),
      cause,
    );
  });

  test("logs a non-Error rejection too, rather than dropping it", () => {
    logError("loading samples", "socket hang up");
    expect(spy).toHaveBeenCalledWith(expect.any(String), "socket hang up");
  });
});

describe("loadFailureMessage", () => {
  test("says what is missing and where the detail is, in plain language", () => {
    const msg = loadFailureMessage("Samples");
    expect(msg).toMatch(/^Samples/);
    expect(msg).toMatch(/logs/i);
  });

  test("carries no technical text of its own", () => {
    // The whole point: whatever threw, the user reads the same plain sentence.
    for (const technical of ["TypeError", "undefined", "500", "stack", "fetch"]) {
      expect(loadFailureMessage("Samples")).not.toContain(technical);
    }
  });
});
