"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * The app's button.
 *
 * There were 797 raw `<button>` elements and no primitive, so the same control
 * was re-decided every time it was written: eight spellings of the primary
 * action alone, and a scatter of paddings, radii and disabled treatments. Two
 * defects came free with that. A bare `<button>` inside a `<form>` defaults to
 * `type="submit"`, so a button that only opened a picker could post the form;
 * and "busy" was usually spelled by swapping the label, which left the control
 * clickable and let a slow save fire twice.
 *
 * Colours are the brand ramp, not tokens: `bioaf-600` and `red-600` mean the
 * same thing in both themes. Surfaces around them are the semantic tokens.
 */

type Variant = "primary" | "secondary" | "danger" | "ghost";
type Size = "sm" | "md" | "lg";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  variant?: Variant;
  size?: Size;
  /** Renders the busy label, announces `aria-busy`, and refuses further clicks. */
  busy?: boolean;
  /** What to say while busy. Falls back to the children. */
  busyLabel?: ReactNode;
  children?: ReactNode;
}

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 " +
  "focus-visible:ring-bioaf-600 disabled:opacity-50 disabled:cursor-not-allowed";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-bioaf-600 text-white hover:bg-bioaf-700",
  secondary: "border border-hairline bg-surface text-ink-muted hover:bg-surface-muted",
  danger: "bg-red-600 text-white hover:bg-red-700",
  ghost: "text-bioaf-700 hover:bg-surface-muted",
};

const SIZES: Record<Size, string> = {
  sm: "px-3 py-1.5 text-sm",
  md: "px-4 py-2 text-sm",
  /** The work node launch footer's existing target. */
  lg: "px-6 py-2.5 text-sm",
};

/**
 * The button's classes, for a link that should look like an action. Navigation
 * stays a link (href, middle-click, open in new tab); only the appearance is
 * shared, so there is still one style authority.
 */
export function buttonClass({ variant = "primary", size = "md", className = "" }: {
  variant?: Variant;
  size?: Size;
  className?: string;
} = {}): string {
  return `${BASE} ${VARIANTS[variant]} ${SIZES[size]} ${className}`.trim();
}

export function Button({
  variant = "primary",
  size = "md",
  busy = false,
  busyLabel,
  disabled,
  className = "",
  type = "button",
  children,
  onClick,
  ...rest
}: ButtonProps) {
  const blocked = disabled || busy;
  return (
    <button
      // Explicit, because the HTML default is "submit" and most of these are not.
      type={type}
      disabled={blocked}
      aria-busy={busy || undefined}
      onClick={blocked ? undefined : onClick}
      className={buttonClass({ variant, size, className })}
      {...rest}
    >
      {busy ? busyLabel ?? children : children}
    </button>
  );
}
