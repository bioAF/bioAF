import { NOT_SET } from "@/lib/placeholders";

/**
 * The placeholder for a value that has not been set.
 *
 * 69 places rendered an em-dash for this. A dash is not a word: it cannot be
 * told apart from "failed to load", from a rendering bug, or from a value that
 * happens to be a dash, and at the `gray-300` it was usually drawn in it sat at
 * 1.47:1, which is close to invisible. The owner's ruling was a text
 * placeholder, in caps.
 *
 * Caps, quiet grey and small: it reads as metadata rather than as content, so a
 * column of them does not shout, but a person can still tell what it means.
 */
export function NotSet({ label }: { label?: string }) {
  return (
    <span
      className="text-xs font-medium tracking-wide text-gray-600"
      aria-label={label ? `${label} not set` : undefined}
    >
      {NOT_SET}
    </span>
  );
}
