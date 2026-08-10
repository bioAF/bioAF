/**
 * What the UI renders where a value is absent.
 *
 * A plain module, not the component, so non-React code (formatters, table
 * helpers) can say the same thing without pulling JSX into its graph.
 *
 * This is the ONE place in the project an em-dash is permitted. The ban holds
 * everywhere else: prose, comments, commit messages and every other piece of UI
 * copy. It is a single constant rather than a permitted pattern precisely so the
 * exemption cannot spread, and so this can be changed again in one edit: the
 * placeholder has now been a dash, then the words "NOT SET", then a dash again
 * (owner's ruling, 2026-08-07), and all 22 call sites import this constant.
 *
 * Held by src/__tests__/a11y-text-contrast.test.ts.
 */
export const NOT_SET = "—";
