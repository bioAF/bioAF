/** @type {import('tailwindcss').Config} */
module.exports = {
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
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
