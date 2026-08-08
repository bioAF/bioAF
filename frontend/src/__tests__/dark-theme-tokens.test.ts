import { readFileSync, readdirSync } from "fs";
import { join } from "path";
import resolveConfig from "tailwindcss/resolveConfig";
import defaultColors from "tailwindcss/colors";
import tailwindConfig from "../../tailwind.config.js";
import { buildTokens, surfaces } from "../../tailwind.tokens.js";

// Dark mode used to be a CSS override layer: a list of `.dark .bg-gray-50 {...}`
// rules in globals.css, one per utility, that remapped the light-neutral classes
// to theme tokens. It lost the race it was in, and the measurements are the
// reason this file exists (whole-app audit, live browser, 2026-08-07):
//
//   * 444 colored `bg-*-50` / `bg-*-100` sites in non-test source, ZERO of them
//     remapped. Painted pixels in dark were byte-identical to light on 20 of 20
//     routes. `/notifications` rendered 20 rows of body text at 1.13:1.
//   * The override matches the BARE class, so every variant needs a duplicate
//     rule. `disabled:` appeared 0 times in globals.css against 27 sites, gray
//     coverage stopped at -200 against 43 `bg-gray-300`/`-400` sites, and
//     `bg-blue-50/50` (NotificationItem) could never match `.dark .bg-blue-50`
//     at all, because that is a different class name.
//
// The fix moves the flip into the utility's own VALUE instead: each affected
// step resolves to `rgb(var(--token, <light rgb>) / <alpha-value>)`, and `.dark`
// overrides the variable. Three properties follow, and this file holds all three:
//
//   1. Every variant is covered for free. `hover:`, `disabled:`, `odd:`,
//      `group-hover:` and the `/50` opacity modifier all read the same variable,
//      so no rule has to be written per variant ever again.
//   2. Light mode cannot regress: the fallback baked into each value IS the
//      Tailwind default, so light renders identically even with globals.css empty.
//   3. Roles stay separate. Tailwind resolves backgroundColor, textColor and
//      borderColor as independent scales, so `text-red-700` can brighten on a
//      dark page while the 120 solid `bg-red-600` buttons keep white text.

const SRC = join(__dirname, "..");
const GLOBALS = readFileSync(join(SRC, "app", "globals.css"), "utf8");

// --------------------------------------------------------------------------
// contrast helpers
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

function hexToRgb(hex: string): Rgb {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as Rgb;
}

// --------------------------------------------------------------------------
// reading the token layer
// --------------------------------------------------------------------------

/** `rgb(var(--bg-red-50, 254 242 242) / <alpha-value>)` -> name + light fallback. */
const TOKEN_VALUE = /^rgb\(var\(--([a-z0-9-]+),\s*(\d+ \d+ \d+)\)\s*\/\s*<alpha-value>\)$/;

interface Token {
  /** CSS custom property name, without the leading `--`. */
  name: string;
  /** The value light mode renders, baked in as the variable's fallback. */
  light: Rgb;
  /** Where it came from, for readable failure messages: e.g. "backgroundColor.red.50". */
  path: string;
}

function parseToken(value: unknown, path: string): Token | null {
  if (typeof value !== "string") return null;
  const m = TOKEN_VALUE.exec(value.trim());
  if (!m) return null;
  return {
    name: m[1],
    light: m[2].split(" ").map(Number) as Rgb,
    path,
  };
}

const resolved = resolveConfig(tailwindConfig as never).theme as unknown as Record<
  string,
  Record<string, Record<string, string> | string>
>;

const BRAND = (
  tailwindConfig as unknown as {
    theme: { extend: { colors: Record<string, Record<string, string>> } };
  }
).theme.extend.colors;

/** Every scale whose entries this file governs. */
const SCALES = ["backgroundColor", "textColor", "borderColor", "ringColor", "placeholderColor"];

function tokensIn(scale: string): Token[] {
  const out: Token[] = [];
  const family = resolved[scale] ?? {};
  for (const [color, value] of Object.entries(family)) {
    if (typeof value === "string") {
      const t = parseToken(value, `${scale}.${color}`);
      if (t) out.push(t);
      continue;
    }
    for (const [step, v] of Object.entries(value)) {
      const t = parseToken(v, `${scale}.${color}.${step}`);
      if (t) out.push(t);
    }
  }
  return out;
}

const ALL_TOKENS = SCALES.flatMap(tokensIn);

/** The `--name: r g b;` declarations inside a given selector block of globals.css. */
function varsInBlock(selector: string): Record<string, Rgb> {
  const start = GLOBALS.indexOf(selector);
  if (start === -1) return {};
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
  const body = GLOBALS.slice(open, end);
  const out: Record<string, Rgb> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}

/** The semantic surfaces, which are still plain CSS in globals.css. */
const CSS_DARK_VARS = varsInBlock(".dark");

/**
 * The dark half of the token layer. It is emitted into the stylesheet by the
 * plugin in tailwind.config.js rather than written out in globals.css, so it is
 * read from the same module the plugin reads.
 */
const DARK_VARS: Record<string, Rgb> = Object.fromEntries(
  Object.entries(buildTokens({ bioaf: BRAND.bioaf }).dark).map(([name, rgb]) => [
    name.replace(/^--/, ""),
    rgb as Rgb,
  ])
);

/** What a token paints under `.dark`: its override if there is one, else its light value. */
function darkValue(t: Token): Rgb {
  return DARK_VARS[t.name] ?? t.light;
}

function tokenAt(scale: string, color: string, step: string): Token | undefined {
  return ALL_TOKENS.find((t) => t.path === `${scale}.${color}.${step}`);
}

// --------------------------------------------------------------------------
// reading the source tree
// --------------------------------------------------------------------------

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next" || entry.name === "__tests__") {
        continue;
      }
      out.push(...sourceFiles(path));
    } else if (/\.tsx?$/.test(entry.name) && !entry.name.includes(".test.")) {
      out.push(path);
    }
  }
  return out;
}

const SOURCE = sourceFiles(SRC).map((f) => readFileSync(f, "utf8"));

/** Class-ish runs between quotes/backticks, which is where a bg and its text pair up. */
function classRuns(text: string): string[] {
  return text.match(/[^"'`]+/g) ?? [];
}

// A state variant (hover:, disabled:, odd:) repaints the SAME box, so its
// background really does sit under that element's text. A pseudo-element variant
// paints a DIFFERENT box with its own text: `text-gray-500 file:bg-bioaf-50` is a
// grey label next to a tinted file-picker button, not grey text on a tint. An
// earlier version of this scan reported that pair and it was not real.
const PSEUDO_ELEMENT = /^(file|before|after|placeholder|marker|selection|first-line|first-letter|backdrop):/;

const TINT_BG = /(?:^|\s)((?:[a-z-]+:)*)bg-([a-z]+)-(50|100|200)(?:\/\d+)?(?=\s|$)/;
const COLOR_TEXT = /(?:^|\s)((?:[a-z-]+:)*)text-([a-z]+)-(\d{2,3})(?=\s|$)/g;

/** Every (background, text) pairing that actually occurs in a single class string. */
function occurringPairs(): { bg: [string, string]; fg: [string, string]; sample: string }[] {
  const seen = new Map<string, { bg: [string, string]; fg: [string, string]; sample: string }>();
  for (const text of SOURCE) {
    for (const run of classRuns(text)) {
      const bg = TINT_BG.exec(run);
      if (!bg || PSEUDO_ELEMENT.test(bg[1])) continue;
      for (const fg of run.matchAll(COLOR_TEXT)) {
        if (PSEUDO_ELEMENT.test(fg[1])) continue;
        const key = `${bg[2]}-${bg[3]}|${fg[2]}-${fg[3]}`;
        if (!seen.has(key)) {
          seen.set(key, {
            bg: [bg[2], bg[3]],
            fg: [fg[2], fg[3]],
            sample: run.trim().slice(0, 80),
          });
        }
      }
    }
  }
  return [...seen.values()];
}

/** Colored/neutral utility families used in source, as `${scale}|${color}|${step}`. */
function usedUtilities(): Set<string> {
  const used = new Set<string>();
  const UTIL = /\b(bg|text|border|divide|ring|placeholder)-([a-z]+)-(\d{2,3})(?:\/\d+)?\b/g;
  const SCALE_OF: Record<string, string> = {
    bg: "backgroundColor",
    text: "textColor",
    border: "borderColor",
    divide: "borderColor",
    ring: "ringColor",
    placeholder: "placeholderColor",
  };
  for (const text of SOURCE) {
    for (const m of text.matchAll(UTIL)) {
      // `bg-opacity-50` is not a colour, it is Tailwind 2's opacity utility and
      // it parses as one under this pattern.
      if (m[2] === "opacity") continue;
      used.add(`${SCALE_OF[m[1]]}|${m[2]}|${m[3]}`);
    }
  }
  return used;
}

// --------------------------------------------------------------------------

describe("dark theme tokens", () => {
  it("computes known WCAG ratios correctly", () => {
    // Guard the guard: if these drift, every assertion below proves nothing.
    expect(contrast([0, 0, 0], [255, 255, 255])).toBeCloseTo(21, 1);
    expect(contrast([255, 255, 255], [255, 255, 255])).toBeCloseTo(1, 5);
    expect(hexToRgb("#ef4444")).toEqual([239, 68, 68]);
  });

  it("has a token layer at all", () => {
    // A floor, so a regression that empties the config fails loudly here rather
    // than silently passing every "for each token" assertion on zero tokens.
    expect(ALL_TOKENS.length).toBeGreaterThan(100);
    expect(Object.keys(DARK_VARS).length).toBeGreaterThan(100);
  });

  it("keeps the token formula's surfaces in step with globals.css", () => {
    // Tints are computed by laying a hue over the dark surface, so the formula
    // holds a copy of the surface values. globals.css is where they are actually
    // declared; if the two drift, every tint sits on a surface it was not mixed
    // against and the contrast numbers below stop describing the real page.
    const pairs: [string, number[]][] = [
      ["color-surface", surfaces.SURFACE],
      ["color-canvas", surfaces.CANVAS],
      ["color-elevated", surfaces.ELEVATED],
      ["color-ink", surfaces.INK],
      ["color-ink-muted", surfaces.INK_MUTED],
      ["color-ink-subtle", surfaces.INK_SUBTLE],
      ["color-hairline", surfaces.HAIRLINE],
    ];
    for (const [name, value] of pairs) {
      expect({ [name]: CSS_DARK_VARS[name] }).toEqual({ [name]: value });
    }
  });

  it("renders light mode byte-identically to Tailwind's own palette", () => {
    // This is the whole safety argument for touching 444 sites at once: the
    // light value is the variable's FALLBACK, so it is what light paints even if
    // globals.css disappears. Compare it against the shipped default.
    const palette = { ...(defaultColors as unknown as Record<string, unknown>), ...BRAND };

    const mismatched: string[] = [];
    for (const t of ALL_TOKENS) {
      const [, color, step] = t.path.split(".");
      const ramp = palette[color] as Record<string, string> | string | undefined;
      const hex = typeof ramp === "string" ? ramp : ramp?.[step];
      if (!hex || !hex.startsWith("#")) continue;
      const want = hexToRgb(hex);
      if (want.join(" ") !== t.light.join(" ")) {
        mismatched.push(`${t.path}: light is ${t.light.join(" ")}, Tailwind ships ${want.join(" ")}`);
      }
    }
    expect(mismatched).toEqual([]);
  });

  it("gives every token a dark value", () => {
    // A token whose variable is never overridden paints its LIGHT value on a
    // black page. That is the original defect, one token at a time.
    const missing = ALL_TOKENS.filter((t) => !(t.name in DARK_VARS)).map((t) => t.path);
    expect(missing).toEqual([]);
  });

  it("never paints a background at its light value on a dark page", () => {
    // The measured failure was literal: painted pixels in dark were
    // byte-identical to light on 20 of 20 routes. A background that is merely
    // *nudged* would still pass an equality check, so this asserts the size of
    // the move, not just that one happened.
    const unmoved: string[] = [];
    for (const t of ALL_TOKENS) {
      if (!t.path.startsWith("backgroundColor")) continue;
      const light = luminance(t.light);
      const dark = luminance(darkValue(t));
      if (dark > light / 4) {
        unmoved.push(
          `${t.path}: dark luminance ${dark.toFixed(3)} against light ${light.toFixed(3)}`
        );
      }
    }
    expect(unmoved).toEqual([]);
  });

  it("clears AA in dark on every background/text pairing the app actually writes", () => {
    // Computed from the source tree rather than an allowlist, so a new pairing
    // is checked the day it is written instead of the day someone remembers to
    // add it here. `bg-blue-50 text-gray-600` on /infrastructure/backup measured
    // 1.90:1 and is exactly this class of defect.
    //
    // Dark only. Running the same scan over the LIGHT values surfaces seven
    // pre-existing light-mode failures (`bg-green-100` + `text-green-600` at
    // 3.00:1, among others) which this change neither caused nor fixes: the
    // light halves of these tokens are byte-identical to what shipped, as the
    // test above proves. They are reported as their own finding, and light-mode
    // contrast is owned by a11y-text-contrast.test.ts.
    const failures: string[] = [];
    for (const { bg, fg, sample } of occurringPairs()) {
      const bgToken = tokenAt("backgroundColor", bg[0], bg[1]);
      const fgToken = tokenAt("textColor", fg[0], fg[1]);
      if (!bgToken || !fgToken) continue;
      const ratio = contrast(darkValue(bgToken), darkValue(fgToken));
      if (ratio < 4.5) {
        failures.push(
          `bg-${bg.join("-")} + text-${fg.join("-")} = ${ratio.toFixed(2)}:1  (${sample})`
        );
      }
    }
    expect(failures).toEqual([]);
  });

  it("clears AA for body and muted text on every tint panel", () => {
    // The worst page in the app was this pairing, not a same-hue one: the old
    // override flipped the TEXT light via the gray shim and left the tint
    // BACKGROUND light. 20 rows at 1.13:1 on /notifications.
    const ink = CSS_DARK_VARS["color-ink"];
    const inkMuted = CSS_DARK_VARS["color-ink-muted"];
    expect(ink).toBeDefined();
    expect(inkMuted).toBeDefined();

    const failures: string[] = [];
    for (const t of ALL_TOKENS) {
      const [scale, , step] = t.path.split(".");
      if (scale !== "backgroundColor" || !["50", "100", "200"].includes(step)) continue;
      for (const [label, fg] of [["ink", ink], ["ink-muted", inkMuted]] as const) {
        const ratio = contrast(darkValue(t), fg);
        if (ratio < 4.5) failures.push(`${t.path} + ${label} = ${ratio.toFixed(2)}:1`);
      }
    }
    expect(failures).toEqual([]);
  });

  it("leaves solid action shades alone so their white text survives", () => {
    // `bg-red-600` is a destructive BUTTON with white text; `text-red-600` is
    // semantic text that must brighten on a dark page. They are the same step of
    // the same ramp, and only the per-scale split keeps them apart. If the
    // background scale ever starts flipping here, 120 buttons invert.
    const inverted: string[] = [];
    for (const t of ALL_TOKENS) {
      const [scale, , step] = t.path.split(".");
      if (scale !== "backgroundColor") continue;
      if (!["500", "600", "700", "800", "900"].includes(step)) continue;
      inverted.push(t.path);
    }
    expect(inverted).toEqual([]);

    for (const hue of ["red", "green", "blue", "amber"]) {
      const solid = (resolved.backgroundColor[hue] as Record<string, string>)["600"];
      expect(solid).toMatch(/^#/);
    }
  });

  it("covers every colored utility family the source actually uses", () => {
    // The one way this design can still develop a hole: someone writes a hue
    // that has no token (bg-pink-50), and it paints light again. Steps that need
    // no flip (solid 500-900 backgrounds, white text) are excluded by the same
    // rules the token layer uses.
    const NEEDS_FLIP: Record<string, string[]> = {
      // Backgrounds: tint steps only. -300/-400 on a hue are saturated status
      // dots and progress fills, and gray-400 is the neutral dot; those must
      // keep their light-mode value or they disappear into the surface.
      backgroundColor: ["50", "100", "200"],
      textColor: ["600", "700", "800", "900"],
      borderColor: ["100", "200", "300", "400"],
      ringColor: ["200", "300"],
      placeholderColor: ["400", "500"],
    };
    // `bg-gray-300` IS a surface (toggle track, button hover, connector rule),
    // unlike the coloured -300s, so it is covered even though -300 is not in the
    // background list above.
    const ALSO_FLIP = new Set(["backgroundColor|gray|300"]);
    // Permanently-dark surfaces: the sidebar and the boot splash are dark in
    // BOTH themes, so their grays are already correct and must not flip.
    const NEVER_FLIP = new Set(["textColor|gray|300", "textColor|gray|200", "textColor|gray|100"]);

    const uncovered: string[] = [];
    for (const key of usedUtilities()) {
      const [scale, color, step] = key.split("|");
      if (!NEEDS_FLIP[scale]?.includes(step) && !ALSO_FLIP.has(key)) continue;
      if (NEVER_FLIP.has(key)) continue;
      if (color === "gray" && scale === "textColor" && Number(step) < 400) continue;
      if (!tokenAt(scale, color, step)) uncovered.push(`${scale}: ${color}-${step}`);
    }
    expect(uncovered).toEqual([]);
  });

  it("does not reintroduce the per-utility override layer", () => {
    // The finding was not "dark mode is wrong", it was "dark mode needs a new
    // rule for every family, every variant and every gray step, forever". Adding
    // `.dark .bg-amber-50 {}` here is how that comes back.
    const overrides = [
      ...GLOBALS.matchAll(/^\s*\.dark\s+\.(?!prose)([a-z0-9\\:_-]+)\s*[,{]/gim),
    ].map((m) => m[1]);
    expect(overrides).toEqual([]);
  });
});
