import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";
import { THEME_STORAGE_KEY } from "@/lib/theme";

function setMatchMedia(dark: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: dark,
      media: query,
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      addListener: jest.fn(),
      removeListener: jest.fn(),
      dispatchEvent: jest.fn(),
      onchange: null,
    }),
  });
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
  setMatchMedia(false);
});

function renderToggle() {
  return render(
    <ThemeProvider>
      <ThemeToggle />
    </ThemeProvider>,
  );
}

describe("ThemeToggle", () => {
  it("renders an accessible toggle button", () => {
    renderToggle();
    expect(
      screen.getByRole("button", { name: /switch to dark mode/i }),
    ).toBeInTheDocument();
  });

  it("turns dark mode on, persists it, and flips the label", () => {
    renderToggle();
    fireEvent.click(screen.getByRole("button", { name: /switch to dark mode/i }));

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(
      screen.getByRole("button", { name: /switch to light mode/i }),
    ).toBeInTheDocument();
  });

  it("toggles back to light on a second click", () => {
    renderToggle();
    const btn = () => screen.getByRole("button");
    fireEvent.click(btn()); // -> dark
    fireEvent.click(btn()); // -> light

    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });
});

describe("ThemeProvider initialization", () => {
  it("applies a stored dark preference on mount", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    renderToggle();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("follows a dark OS preference when no choice is stored", () => {
    setMatchMedia(true);
    renderToggle();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("stays light for a light OS preference with no stored choice", () => {
    setMatchMedia(false);
    renderToggle();
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});
