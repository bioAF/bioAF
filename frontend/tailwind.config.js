const plugin = require("tailwindcss/plugin");
const { buildTokens } = require("./tailwind.tokens");

// The brand ramp is declared here rather than inline below because the dark-mode
// token layer needs it: bioaf is not part of Tailwind's shipped palette, so
// buildTokens cannot look it up.
//
// The dark half of this ramp is a contrast surface, not just a palette:
// 600 is the primary action shade (white text on it, and the same value
// as link text on white) and 700 is its hover partner.
//
// 600 was #0284c7 until 2026-08-05. That is 4.10:1 on white, under the
// 4.5:1 WCAG AA wants for normal text, across 226 buttons and 138 links.
// Rather than rewrite those 364 call sites, 600 through 900 each moved
// one step darker, so every class in the app keeps its name and picks up
// a compliant value. 900 is the only newly invented shade; the rest were
// already shipping one slot up. Hue is held at ~201 degrees throughout.
//
// Held by src/__tests__/brand-contrast.test.ts, which asserts the two
// properties that matter (600 clears AA, the ramp never stops getting
// darker) rather than pinning these hex values.
const bioaf = {
  50: "#f0f9ff",
  100: "#e0f2fe",
  200: "#bae6fd",
  300: "#7dd3fc",
  400: "#38bdf8",
  500: "#0ea5e9",
  600: "#0369a1", // 5.93:1 on white
  700: "#075985", // 7.56:1 - hover partner for 600
  800: "#0c4a6e", // 9.46:1
  900: "#0a405e", // 11.01:1
};

// Dark mode lives in the VALUE of each affected utility, not in a list of
// `.dark .bg-gray-50 {}` overrides. See tailwind.tokens.js for why, and for the
// one formula every token below is derived from.
const { scales, dark } = buildTokens({ bioaf });

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
      // Per-utility scales, so a step that means one thing as a background and
      // another as text can flip independently. `bg-red-600` is a destructive
      // button that keeps its white text; `text-red-600` is semantic text that
      // brightens on a dark page. Same step, same ramp, opposite requirements.
      ...scales,
      colors: {
        bioaf,
        // Semantic theme tokens, backed by CSS variables defined in globals.css.
        // These flip automatically between light and dark. Prefer these role-named
        // colors (bg-surface, text-ink, border-hairline) when the intent is the
        // ROLE; the per-utility scales above keep the existing gray/white/tint
        // utilities coherent when the intent is a specific shade.
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
  plugins: [
    require("@tailwindcss/typography"),
    // Emit the dark half of every token as one `.dark` block. Generated rather
    // than written out in globals.css so there is exactly one place a colour is
    // decided, and no 180-line table anyone has to keep in step by hand.
    plugin(({ addBase }) => {
      addBase({
        ".dark": Object.fromEntries(
          Object.entries(dark).map(([name, rgb]) => [name, rgb.join(" ")])
        ),
      });
    }),
  ],
};
