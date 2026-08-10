/**
 * Dark-mode colour tokens.
 *
 * WHY THIS FILE EXISTS
 *
 * Dark mode used to be an override layer in globals.css: a list of
 * `.dark .bg-gray-50 { ... }` rules that remapped the light-neutral utilities to
 * theme tokens. It could not keep up, and the audit measured how far behind it
 * had fallen: 444 colored `bg-*-50` / `bg-*-100` sites in source, none remapped,
 * with painted pixels in dark byte-identical to light on 20 of 20 routes.
 *
 * The reason is structural, not clerical. An override matches the BARE utility
 * class, so every variant needs its own duplicate rule: `disabled:` appeared
 * zero times in globals.css against 27 sites; gray coverage stopped at -200
 * against 43 `bg-gray-300`/`-400` sites; and `bg-blue-50/50` on the notification
 * row could never match `.dark .bg-blue-50` at all, because that is a different
 * class name. It rendered 20 rows of body text at 1.13:1.
 *
 * So the flip moved into the utility's own VALUE. Each affected step resolves to
 * `rgb(var(--token, <light rgb>) / <alpha-value>)` and `.dark` overrides the
 * variable. `hover:`, `disabled:`, `odd:`, `group-hover:` and the `/50` opacity
 * modifier all read that same variable, so no rule has to be written per variant
 * ever again, and no call site had to change.
 *
 * Two properties make that safe to do to 444 sites at once:
 *
 *   - The light value is the variable's FALLBACK and comes straight from
 *     Tailwind's shipped palette below, so light mode is unchanged by
 *     construction rather than by careful transcription.
 *   - Tailwind resolves backgroundColor, textColor and borderColor as
 *     independent scales. `text-red-700` can brighten on a dark page while the
 *     120 solid `bg-red-600` buttons keep their white text, even though both are
 *     the same step of the same ramp.
 *
 * Held by src/__tests__/dark-theme-tokens.test.ts.
 */

const palette = require("tailwindcss/colors");

// The semantic surfaces, duplicated from the `:root` / `.dark` block in
// globals.css. The test asserts these still agree with that file; if they drift,
// tints stop sitting correctly on the surfaces they are laid over.
const SURFACE = [22, 27, 34];
const CANVAS = [13, 17, 23];
const SURFACE_MUTED = [28, 33, 40];
const ELEVATED = [33, 38, 46];
const INK = [230, 233, 238];
const INK_MUTED = [173, 181, 192];
const INK_SUBTLE = [138, 146, 158];
const HAIRLINE = [48, 54, 63];

/** Colour families the app actually uses. `usedUtilities` in the test guards this list. */
const HUES = [
  "red",
  "green",
  "blue",
  "amber",
  "yellow",
  "orange",
  "purple",
  "indigo",
  "emerald",
  "teal",
  "violet",
  "bioaf",
];

/**
 * A dark tint is the hue's own 500 laid over the dark surface. The alpha rises
 * with the step so the ramp keeps its ordering, and stays low enough that body
 * text (`--color-ink`, `--color-ink-muted`) clears 4.5:1 on every hue: yellow is
 * the binding constraint, because yellow-500 is the brightest 500 in the set.
 *
 * STOPS AT 200 ON PURPOSE. A coloured background at -300 or -400 is not a tint
 * panel in this app, it is a saturated INDICATOR: `bg-green-400` and
 * `bg-red-400` are the Service Health dots in lib/statusStyles.ts, and
 * `bg-red-400` is a progress-bar fill. Those must stay saturated, because a dot
 * that sinks to the surface it sits on is a dot nobody can see. The first
 * version of this file flipped them and the contrast guard caught it.
 */
const BG_ALPHA = { 50: 0.14, 100: 0.2, 200: 0.24 };

/** A border has to read as an edge without becoming a fill, so it sits higher. */
const BORDER_ALPHA = { 100: 0.32, 200: 0.4, 300: 0.5, 400: 0.6 };

/**
 * Coloured text inverts around the ramp: the emphatic step in light (800, the
 * darkest) becomes the emphatic step in dark (200, the lightest). Emphasis
 * ordering is preserved, so `text-red-800` still reads as louder than
 * `text-red-600` after the flip.
 */
const TEXT_STEP = { 600: 400, 700: 300, 800: 200, 900: 200 };

/**
 * Neutral backgrounds map onto the named surfaces rather than onto a hue.
 *
 * Stops at 300 for the same reason BG_ALPHA stops at 200: `bg-gray-400` is the
 * neutral status dot (NEUTRAL_DOT in lib/statusStyles.ts) and the inactive
 * stepper dot, and #9ca3af already reads correctly against a dark canvas.
 * `bg-gray-300` is a surface - the off-state toggle track, the hover partner for
 * `bg-gray-200` buttons, and a connector rule - and painted rgb(209,213,219) on
 * a black page because the old override layer stopped at -200.
 */
const GRAY_BG = {
  // Not CANVAS, which is what the old override layer used for the bare class
  // while giving `hover:` and `odd:` a lighter value. A token has one value per
  // utility, so it takes the one the 285 hover and zebra sites need: a subtle
  // lift off the card, which is also the right reading for a table head or an
  // inset panel on a dark page. The six places that genuinely meant "the page
  // background" now say `bg-canvas`, which is what that token is for.
  50: SURFACE_MUTED,
  100: ELEVATED,
  200: ELEVATED,
  // No named surface exists above -200, so this continues the ramp by lifting
  // off the surface toward white.
  300: mix([255, 255, 255], SURFACE, 0.14),
};

/** Neutral text collapses onto the ink scale, two light steps per ink step. */
const GRAY_FG = { 900: INK, 800: INK, 700: INK_MUTED, 600: INK_MUTED, 500: INK_SUBTLE, 400: INK_SUBTLE };

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

/** Composite `fg` at `alpha` over `bg`, both opaque sRGB triples. */
function mix(fg, bg, alpha) {
  return fg.map((c, i) => Math.round(bg[i] + alpha * (c - bg[i])));
}

/**
 * Build the token layer.
 *
 * @param brand the bioaf ramp from tailwind.config.js, which is not in Tailwind's palette
 * @returns `{ scales, dark }` - colour scales to spread into `theme.extend`, and
 *          the `--token: r g b` map to emit under `.dark`.
 */
function buildTokens(brand) {
  const ramp = (hue) => (hue === "bioaf" ? brand.bioaf : palette[hue]);

  const scales = {
    backgroundColor: {},
    textColor: {},
    borderColor: {},
    ringColor: {},
    placeholderColor: {},
  };
  const dark = {};

  /** Register one token: light value from the shipped palette, dark value computed. */
  const put = (scale, color, step, prefix, darkRgb, lightRgb) => {
    const name = `--${prefix}-${color}-${step}`;
    (scales[scale][color] ??= {})[step] = `rgb(var(${name}, ${lightRgb.join(" ")}) / <alpha-value>)`;
    dark[name] = darkRgb;
  };

  // Surfaces. `bg-white` is 334 sites of card, header and input; `text-white`
  // stays white, which is only possible because the scales are separate.
  scales.backgroundColor.white = `rgb(var(--bg-white, 255 255 255) / <alpha-value>)`;
  dark["--bg-white"] = SURFACE;

  for (const [step, value] of Object.entries(GRAY_BG)) {
    put("backgroundColor", "gray", step, "bg", value, hexToRgb(palette.gray[step]));
  }
  for (const [step, value] of Object.entries(GRAY_FG)) {
    put("textColor", "gray", step, "fg", value, hexToRgb(palette.gray[step]));
  }
  for (const step of [100, 200, 300, 400]) {
    put("borderColor", "gray", step, "bd", HAIRLINE, hexToRgb(palette.gray[step]));
  }

  for (const hue of HUES) {
    const five = hexToRgb(ramp(hue)[500]);
    for (const [step, alpha] of Object.entries(BG_ALPHA)) {
      put("backgroundColor", hue, step, "bg", mix(five, SURFACE, alpha), hexToRgb(ramp(hue)[step]));
    }
    for (const [step, source] of Object.entries(TEXT_STEP)) {
      put("textColor", hue, step, "fg", hexToRgb(ramp(hue)[source]), hexToRgb(ramp(hue)[step]));
    }
    for (const [step, alpha] of Object.entries(BORDER_ALPHA)) {
      put("borderColor", hue, step, "bd", mix(five, SURFACE, alpha), hexToRgb(ramp(hue)[step]));
    }
  }

  // `divideColor` derives from `borderColor`, so it inherits the above. `ringColor`
  // and `placeholderColor` derive from `colors` and do not, so they are pointed at
  // the same variables by hand rather than given tokens of their own.
  scales.ringColor.gray = {
    200: scales.borderColor.gray[200],
    300: scales.borderColor.gray[300],
  };
  scales.placeholderColor.gray = {
    400: scales.textColor.gray[400],
    500: scales.textColor.gray[500],
  };

  return { scales, dark };
}

module.exports = {
  buildTokens,
  hexToRgb,
  mix,
  surfaces: { SURFACE, CANVAS, ELEVATED, INK, INK_MUTED, INK_SUBTLE, HAIRLINE },
  HUES,
  BG_ALPHA,
  BORDER_ALPHA,
  TEXT_STEP,
};
