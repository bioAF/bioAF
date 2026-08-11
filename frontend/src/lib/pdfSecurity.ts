/**
 * Load-time options applied to every pdf.js document this app opens.
 *
 * Deliberately free of any `pdfjs-dist` import. pdf.js touches browser globals
 * at module scope, so importing it server-side breaks the first render; the
 * viewers are behind `next/dynamic({ ssr: false })` for that reason and
 * `browser-only-modules.test.ts` holds the line. Keeping this a plain object
 * lets any module read the policy without dragging pdf.js along.
 */
export const HARDENED_PDF_OPTIONS = {
  /**
   * pdf.js runs JavaScript embedded in a PDF and defaults this to true. Every
   * PDF here is untrusted (external publishers, user uploads), so the default
   * hands a hostile file arbitrary script execution in our origin against a
   * logged-in session (GHSA-hq66-cqwq-w95j). PDF JavaScript only drives
   * interactive AcroForm behaviour, which these viewers do not render: they
   * paint page canvases and page through them.
   */
  enableScripting: false,

  /**
   * Stops pdf.js building functions from PDF-supplied strings to speed up font
   * and pattern decoding. That path evaluates attacker-controlled input, and
   * removing it also lets the app run under a CSP with no `unsafe-eval`. The
   * fallback interpreter renders the same pixels.
   */
  isEvalSupported: false,
} as const;
