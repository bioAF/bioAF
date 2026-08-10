import tailwindConfig from "../../tailwind.config.js";

// The brand ramp carries text, so its darker half is a contrast surface and not
// just a palette. Two things went wrong before this guard existed:
//
//   1. bioaf-600 was #0284c7, which is 4.10:1 on white. It was the action shade
//      for 226 buttons (white text) and 138 links (blue text on a light
//      surface), all of them below the 4.5:1 that WCAG AA wants for normal
//      text. Nobody noticed because the HOVER shade, bioaf-700, passed: every
//      button became compliant while the pointer was on it and failed again
//      when it left.
//   2. Fixing that by hand invites a collision. Moving 600 down onto 700's old
//      value without moving the rest leaves two tokens holding the same hex,
//      which silently kills the hover step on every one of those buttons.
//
// So this file asserts the two properties that actually matter, rather than
// pinning the hex values (which would just be a change-detector test):
// the action shade clears AA, and the ramp never stops getting darker.

type Ramp = Record<string, string>;

const ramp = (
  tailwindConfig as unknown as {
    theme: { extend: { colors: { bioaf: Ramp } } };
  }
).theme.extend.colors.bioaf;

/** WCAG 2.1 relative luminance. */
function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const channel = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const r = channel(parseInt(h.slice(0, 2), 16));
  const g = channel(parseInt(h.slice(2, 4), 16));
  const b = channel(parseInt(h.slice(4, 6), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const WHITE = "#ffffff";

/** The shade used for primary actions: white text sits on it, and it is also
 * used as link text on white. Both directions are the same ratio. */
const ACTION = "600";
/** The hover partner for ACTION. */
const ACTION_HOVER = "700";

describe("brand ramp contrast", () => {
  it("computes known WCAG ratios correctly", () => {
    // Guard the guard: if these drift, the assertions below prove nothing.
    expect(contrast("#000000", WHITE)).toBeCloseTo(21, 1);
    expect(contrast(WHITE, WHITE)).toBeCloseTo(1, 5);
    expect(contrast("#0284c7", WHITE)).toBeCloseTo(4.1, 1);
  });

  it("carries white text on the action shade at AA", () => {
    expect(contrast(ramp[ACTION], WHITE)).toBeGreaterThanOrEqual(4.5);
  });

  it("carries the action shade as text on a white surface at AA", () => {
    // Contrast is symmetric, so this is the same number. It is asserted
    // separately because it is a different 138-site failure mode, and someone
    // reading this file should not have to know that to trust it.
    expect(contrast(WHITE, ramp[ACTION])).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the hover shade darker than the action shade by a visible step", () => {
    const rest = luminance(ramp[ACTION]);
    const hover = luminance(ramp[ACTION_HOVER]);
    expect(hover).toBeLessThan(rest);
    // A hover that is only marginally darker reads as no hover at all. The
    // pre-existing 600 -> 700 step was a factor of ~1.7 in luminance; require
    // a real step rather than a hairline one.
    expect(rest / hover).toBeGreaterThan(1.25);
  });

  it("never stops getting darker as the shade number rises", () => {
    const shades = Object.keys(ramp)
      .map(Number)
      .sort((a, b) => a - b)
      .map(String);

    for (let i = 1; i < shades.length; i++) {
      const lighter = luminance(ramp[shades[i - 1]]);
      const darker = luminance(ramp[shades[i]]);
      expect(darker).toBeLessThan(lighter);
    }
  });

  it("holds no duplicate values", () => {
    // Two tokens on the same hex is the specific way a hover step dies.
    const values = Object.values(ramp).map((v) => v.toLowerCase());
    expect(new Set(values).size).toBe(values.length);
  });
});
