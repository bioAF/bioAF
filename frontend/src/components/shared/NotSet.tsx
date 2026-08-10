import { NOT_SET } from "@/lib/placeholders";

/**
 * The placeholder for a value that has not been set.
 *
 * The glyph is the owner's ruling (2026-08-07), reversing the words "NOT SET"
 * that shipped before it. What the words were solving is still solved here, by
 * the component rather than by the copy:
 *
 *   - The 69 original dashes were scattered literals. This one comes from a
 *     single constant, so a dash on screen always means "not set" and never
 *     means "some other file wrote a dash".
 *   - They were usually drawn in `gray-300`, which is 1.47:1 on white and close
 *     to invisible. `gray-600` is 6.87:1, and carries a dark token, so it holds
 *     in both themes.
 *   - A dash is not a word, so it reads as nothing to a screen reader. The
 *     `aria-label` says which field is unset when the caller names it.
 */
export function NotSet({ label }: { label?: string }) {
  return (
    <span
      className="text-gray-600"
      aria-label={label ? `${label} not set` : undefined}
      role={label ? undefined : "presentation"}
    >
      {NOT_SET}
    </span>
  );
}
