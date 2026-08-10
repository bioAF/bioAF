import { readFileSync } from "fs";
import { join } from "path";
import { buildTokens } from "../../tailwind.tokens.js";
import tailwindConfig from "../../tailwind.config.js";

// The sidebar used to be `bg-gray-900 text-white`, permanently dark in BOTH
// themes. That made it the one piece of the app that did not participate in
// theming at all: in light mode it was a black slab against a white header and a
// gray-50 canvas, and in dark mode it matched only because everything else had
// finally caught up to it. The owner asked for it to align in both (2026-08-08).
//
// So the shell is now ONE surface: the sidebar and the header sit on
// `--color-surface` and are separated from the content canvas by a hairline. The
// active nav item uses the brand tint pair the rest of the app already uses for
// selection, instead of a solid pill that only worked on black.
//
// This guard exists because "permanently dark" is easy to reintroduce one
// utility at a time, and because the sidebar's text shades are now the exact
// inverse of what they were: `text-gray-300`/`text-gray-400` were correct on
// black and are 1.5-2.5:1 on white. a11y-text-contrast.test.ts used to exempt
// this file for that reason and no longer does.

const SRC = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

/**
 * Drop `//` and block comments.
 *
 * Every guard in this repo that scans source needs this, and the ones that skip
 * it all fail the same way: a comment written to explain a banned pattern
 * contains the banned pattern, so documenting the rule breaks the rule. The
 * bundled Impeccable detector ships with this bug (2 of its 12 findings on this
 * repo were comments describing code the file deliberately avoids), and the
 * first version of this file reproduced it within the hour.
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
}

const SIDEBAR = read("components/layout/Sidebar.tsx");
const NAV_ITEM = read("components/layout/NavItem.tsx");
const HEADER = read("components/layout/Header.tsx");
const GLOBALS = read("app/globals.css");
const SHELL = { "Sidebar.tsx": SIDEBAR, "NavItem.tsx": NAV_ITEM };

// --------------------------------------------------------------------------

type Rgb = [number, number, number];

function luminance([r, g, b]: Rgb): number {
  const channel = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `--name: r g b;` declarations inside a selector block of globals.css. */
function varsInBlock(selector: string): Record<string, Rgb> {
  const start = GLOBALS.indexOf(selector);
  const open = GLOBALS.indexOf("{", start);
  let depth = 0;
  let end = open;
  for (let i = open; i < GLOBALS.length; i++) {
    if (GLOBALS[i] === "{") depth++;
    else if (GLOBALS[i] === "}" && --depth === 0) {
      end = i;
      break;
    }
  }
  const out: Record<string, Rgb> = {};
  for (const m of GLOBALS.slice(open, end).matchAll(/--([a-z0-9-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}

const LIGHT_SEMANTIC = varsInBlock(":root");
const DARK_SEMANTIC = varsInBlock(".dark");

const BRAND = (
  tailwindConfig as unknown as { theme: { extend: { colors: Record<string, Record<string, string>> } } }
).theme.extend.colors;
const DARK_TOKENS = buildTokens({ bioaf: BRAND.bioaf }).dark as unknown as Record<string, Rgb>;

const hexToRgb = (hex: string): Rgb =>
  [0, 2, 4].map((i) => parseInt(hex.replace("#", "").slice(i, i + 2), 16)) as Rgb;

/** What a Tailwind utility paints, in a given theme. */
function paints(utility: string, theme: "light" | "dark"): Rgb {
  // Semantic tokens (bg-surface, text-ink) read straight from globals.css.
  const semantic = utility.replace(/^(bg|text|border)-/, "color-");
  const table = theme === "dark" ? DARK_SEMANTIC : LIGHT_SEMANTIC;
  if (table[semantic]) return table[semantic];

  // Palette utilities read the token layer: dark from the formula, light from
  // Tailwind's own value, which is what the token falls back to.
  const m = /^(bg|text|border)-([a-z]+)-(\d{2,3})$/.exec(utility);
  if (!m) throw new Error(`test cannot resolve utility: ${utility}`);
  const [, role, color, step] = m;
  if (theme === "dark") {
    const prefix = role === "bg" ? "bg" : role === "text" ? "fg" : "bd";
    const v = DARK_TOKENS[`--${prefix}-${color}-${step}`];
    if (v) return v as Rgb;
  }
  const palette = {
    ...(require("tailwindcss/colors") as Record<string, Record<string, string>>),
    ...BRAND,
  };
  return hexToRgb(palette[color][step]);
}

// --------------------------------------------------------------------------

describe("app shell surface", () => {
  it("computes known WCAG ratios correctly", () => {
    expect(contrast([0, 0, 0], [255, 255, 255])).toBeCloseTo(21, 1);
    expect(paints("bg-surface", "light")).toEqual([255, 255, 255]);
    expect(paints("bg-surface", "dark")).toEqual([22, 27, 34]);
  });

  it("puts the sidebar on the same surface as the header", () => {
    // The point of the change: chrome is one plane. If either moves off the
    // shared token the two stop matching and the shell looks assembled from
    // parts again.
    expect(SIDEBAR).toContain("bg-surface");
    expect(HEADER).toContain("bg-surface");
  });

  it("separates the shell from the content with a hairline, in both themes", () => {
    // This used to be `dark:border-r dark:border-gray-800`, so in light mode the
    // sidebar had no edge at all: it did not need one, being black. On a white
    // sidebar the edge is the only thing dividing nav from content.
    expect(SIDEBAR).toMatch(/border-r border-hairline/);
    expect(SIDEBAR).not.toMatch(/\bdark:/);
  });

  it("keeps no permanently-dark palette in the nav", () => {
    // Each of these paints a dark slab regardless of theme, which is exactly the
    // defect. `bg-white/10` is the same bug inverted: a translucent white lift
    // that is invisible on a white surface.
    const BANNED = [
      /\bbg-gray-(700|800|900)\b/,
      /\bborder-gray-(600|700|800)\b/,
      /\bbg-white\/\d+/,
      /\btext-white\b/,
      /\btext-gray-(300|400)\b/,
    ];
    const offenders: string[] = [];
    for (const [file, source] of Object.entries(SHELL)) {
      for (const pattern of BANNED) {
        const hit = pattern.exec(source);
        if (hit) offenders.push(`${file}: ${hit[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("marks the active item with the brand tint the rest of the app uses", () => {
    // A solid `bg-bioaf-700` pill is a deliberately untokenized action shade, so
    // it paints the same deep blue in both themes. That reads as a heavy block on
    // a light panel. The tint pair carries dark tokens and flips.
    expect(SIDEBAR).toContain("bg-bioaf-50");
    expect(SIDEBAR).not.toContain("bg-bioaf-700");
    expect(NAV_ITEM).not.toContain("bg-bioaf-700");
  });

  it("clears AA for every nav pairing, in both themes", () => {
    // The shades are the inverse of what they were, so every one of these is a
    // fresh pairing that has never been checked on this surface.
    const PAIRS: [string, string, string][] = [
      ["nav item, resting", "bg-surface", "text-gray-700"],
      ["nav item, hovered", "bg-gray-100", "text-gray-900"],
      ["nav child, resting", "bg-surface", "text-gray-600"],
      ["active item", "bg-bioaf-50", "text-bioaf-700"],
      ["expanded section header", "bg-gray-100", "text-gray-900"],
      ["brand wordmark", "bg-surface", "text-bioaf-700"],
      ["tagline", "bg-surface", "text-gray-600"],
      ["version footer", "bg-surface", "text-gray-600"],
      ["disabled item badge", "bg-gray-100", "text-gray-600"],
    ];

    const failures: string[] = [];
    for (const [what, bg, fg] of PAIRS) {
      for (const theme of ["light", "dark"] as const) {
        const ratio = contrast(paints(bg, theme), paints(fg, theme));
        if (ratio < 4.5) {
          failures.push(`${theme}: ${what} (${fg} on ${bg}) = ${ratio.toFixed(2)}:1`);
        }
      }
    }
    expect(failures).toEqual([]);
  });

  it("no longer needs a dark-surface exemption from the contrast guard", () => {
    // a11y-text-contrast.test.ts allows `text-gray-400` only on surfaces that are
    // dark in both themes. The sidebar is not one any more, and leaving it listed
    // would silently permit 2.54:1 text on a white panel.
    const guard = read("__tests__/a11y-text-contrast.test.ts");
    const list = /const DARK_SURFACES = \[([\s\S]*?)\];/.exec(guard);
    expect(list).not.toBeNull();
    // Strip comments first. The note in that list explaining WHY the sidebar was
    // removed names the file, and a naive scan reads the explanation as the
    // entry. This repo has hit that exact bug before, in its own tooling.
    const entries = stripComments(list![1]);
    expect(entries).not.toContain("Sidebar.tsx");
    expect(entries).not.toContain("NavItem.tsx");
    // The boot splash IS still dark in both themes and must keep its exemption.
    expect(entries).toContain("BootSplash.tsx");
  });
});
