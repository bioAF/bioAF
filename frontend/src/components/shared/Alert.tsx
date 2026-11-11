import type { ReactNode } from "react";

type Tone = "error" | "warning" | "info" | "success";

/**
 * An inline message about something the user just did or needs to know here:
 * a failed upload, a warning beside a form, a completed action.
 *
 * Not for a failed page load (ErrorState), a failed background refresh
 * (StaleNotice) or an empty picker (PickerNotice).
 *
 * An error interrupts a screen reader (`role="alert"`) because it is new and
 * needs action; the other tones are polite status updates.
 */
const TONES: Record<Tone, string> = {
  error: "bg-red-50 border-red-200 text-red-800",
  warning: "bg-amber-50 border-amber-200 text-amber-800",
  info: "bg-blue-50 border-blue-200 text-blue-800",
  success: "bg-green-50 border-green-200 text-green-800",
};

export function Alert({
  tone,
  title,
  action,
  className = "",
  children,
}: {
  tone: Tone;
  title?: ReactNode;
  action?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`flex items-start justify-between gap-3 rounded-md border p-3 text-sm ${TONES[tone]} ${className}`.trim()}
    >
      <div className="min-w-0">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={title ? "mt-1" : ""}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
