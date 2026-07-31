/** @type {import('tailwindcss').Config} */
module.exports = {
  // Dark mode is opt-in via a `dark` class on <html>, toggled by ThemeProvider
  // (src/components/theme/ThemeProvider.tsx) and set pre-paint by THEME_INIT_SCRIPT.
  darkMode: "class",
  content: [
    // Scan all of src/, not just app/ + components/. statusStyles.ts (under
    // src/lib/) is the single source of truth for status colors as string
    // literals; classes used only there (e.g. serviceHealth's bg-green-400 dot)
    // get purged from the built CSS if their file is not scanned, which silently
    // breaks the Service Health dots. A src-wide glob keeps every class literal
    // in any helper module visible to Tailwind.
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bioaf: {
          50: "#f0f9ff",
          100: "#e0f2fe",
          200: "#bae6fd",
          300: "#7dd3fc",
          400: "#38bdf8",
          500: "#0ea5e9",
          600: "#0284c7",
          700: "#0369a1",
          800: "#075985",
          900: "#0c4a6e",
        },
        // Semantic theme tokens, backed by CSS variables defined in globals.css.
        // These flip automatically between light and dark. Prefer these role-named
        // colors (bg-surface, text-ink, border-hairline) in new/tokenized code; the
        // override layer in globals.css keeps existing gray/white utilities coherent.
        canvas: "rgb(var(--color-canvas) / <alpha-value>)",
        surface: "rgb(var(--color-surface) / <alpha-value>)",
        "surface-muted": "rgb(var(--color-surface-muted) / <alpha-value>)",
        elevated: "rgb(var(--color-elevated) / <alpha-value>)",
        ink: "rgb(var(--color-ink) / <alpha-value>)",
        "ink-muted": "rgb(var(--color-ink-muted) / <alpha-value>)",
        "ink-subtle": "rgb(var(--color-ink-subtle) / <alpha-value>)",
        hairline: "rgb(var(--color-hairline) / <alpha-value>)",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
