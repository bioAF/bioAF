import {
  THEME_INIT_SCRIPT,
  THEME_STORAGE_KEY,
  resolveTheme,
  getStoredTheme,
  storeTheme,
  applyResolvedTheme,
} from "./theme";

describe("resolveTheme", () => {
  it("returns the explicit choice regardless of system preference", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("light", false)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("dark", true)).toBe("dark");
  });

  it("follows the system preference when choice is 'system'", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });
});

describe("getStoredTheme / storeTheme", () => {
  beforeEach(() => window.localStorage.clear());

  it("defaults to 'system' when nothing is stored", () => {
    expect(getStoredTheme()).toBe("system");
  });

  it("round-trips a stored explicit choice", () => {
    storeTheme("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(getStoredTheme()).toBe("dark");
  });

  it("treats an unknown stored value as 'system'", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "chartreuse");
    expect(getStoredTheme()).toBe("system");
  });

  it("does not throw when localStorage access throws", () => {
    const getItem = jest
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("denied");
      });
    expect(() => getStoredTheme()).not.toThrow();
    expect(getStoredTheme()).toBe("system");
    getItem.mockRestore();
  });
});

describe("applyResolvedTheme", () => {
  afterEach(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "";
  });

  it("adds the dark class and sets colorScheme for dark", () => {
    applyResolvedTheme("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("removes the dark class and sets colorScheme for light", () => {
    document.documentElement.classList.add("dark");
    applyResolvedTheme("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe("light");
  });
});

describe("THEME_INIT_SCRIPT", () => {
  // The key is a literal inside the script rather than an interpolation, so that
  // the string never becomes constructed code (CodeQL js/bad-code-sanitization).
  // This is what keeps the literal and the constant from drifting apart.
  it("reads the same storage key the rest of the module writes", () => {
    expect(THEME_INIT_SCRIPT).toContain(`var k="${THEME_STORAGE_KEY}"`);
  });

  it("builds no part of itself from a value", () => {
    expect(THEME_INIT_SCRIPT).not.toMatch(/\$\{/);
  });
});
