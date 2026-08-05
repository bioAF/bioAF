export function LoadingSpinner({
  size = "md",
  label = "Loading",
}: {
  size?: "sm" | "md" | "lg";
  label?: string;
}) {
  const sizeClasses = {
    sm: "w-4 h-4",
    md: "w-8 h-8",
    lg: "w-12 h-12",
  };

  return (
    // The app's only shared loading state, so this wrapper is what makes ~63
    // "we are fetching" moments perceivable without sight. `role="status"`
    // announces politely (it waits for a pause rather than interrupting), which
    // is what a background fetch warrants; `aria-live="polite"` is implied by
    // the role but stated for assistive tech that does not map it.
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-center"
    >
      <div
        aria-hidden="true"
        className={`${sizeClasses[size]} border-2 border-gray-200 border-t-bioaf-500 rounded-full animate-spin motion-reduce:animate-none`}
      />
      {/* Visually hidden: the ring carries the meaning for sighted users and
          this carries it for everyone else. Without it the live region is
          empty and announces nothing. */}
      <span className="sr-only">{label}</span>
    </div>
  );
}
