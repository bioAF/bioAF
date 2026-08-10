"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

type ToastTone = "error" | "success" | "info";

interface Toast {
  id: number;
  tone: ToastTone;
  message: string;
}

interface ToastApi {
  /** A failure the user needs to know about. Persists until dismissed. */
  error: (message: string) => void;
  /** Confirmation that something worked. Auto-dismisses. */
  success: (message: string) => void;
  /** Neutral notice. Auto-dismisses. */
  info: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** Success and info clear themselves; errors never do. */
const AUTO_DISMISS_MS = 5000;

/**
 * The app's single notification surface.
 *
 * It exists because roughly 168 catch blocks across the frontend were empty, so a
 * failed mutation produced no message at all: the button simply returned to its
 * idle label and the user clicked it again. There was no toast layer to route an
 * error to, and comments claiming errors were "handled by api client" were wrong
 * (lib/api.ts only throws).
 *
 * Errors are deliberately sticky. An auto-dismissing error is the same bug in
 * slower motion: if the user was looking elsewhere, the failure is still silent.
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((tone: ToastTone, message: string) => {
    setToasts((prev) => {
      // Collapse a repeat of the same message rather than stacking duplicates:
      // a retry loop should not bury the screen.
      if (prev.some((t) => t.tone === tone && t.message === message)) return prev;
      return [...prev, { id: nextId.current++, tone, message }];
    });
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      error: (m) => push("error", m),
      success: (m) => push("success", m),
      info: (m) => push("info", m),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/* Two regions, both always mounted and both starting empty.
          A live region has to exist BEFORE content is put into it: a screen
          reader watches the node it already knows about, so inserting the
          region and the message together can be seen as a new node rather than
          a change, and announce nothing. Splitting by urgency lets an error
          interrupt while a confirmation waits its turn, which one shared region
          could not do. */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2">
        <div aria-live="assertive" className="flex flex-col gap-2 empty:hidden">
          {toasts
            .filter((t) => t.tone === "error")
            .map((t) => (
              <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
            ))}
        </div>
        <div aria-live="polite" className="flex flex-col gap-2 empty:hidden">
          {toasts
            .filter((t) => t.tone !== "error")
            .map((t) => (
              <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
            ))}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  useEffect(() => {
    if (toast.tone === "error") return;
    const t = setTimeout(() => onDismiss(toast.id), AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [toast.id, toast.tone, onDismiss]);

  const tone = {
    error: "bg-red-50 border-red-200 text-red-800",
    success: "bg-green-50 border-green-200 text-green-800",
    info: "bg-blue-50 border-blue-200 text-blue-800",
  }[toast.tone];

  return (
    <div
      // The urgency now lives on the enclosing region, which is mounted before
      // anything is put in it. The role stays here so the item is still exposed
      // as an alert/status in its own right.
      role={toast.tone === "error" ? "alert" : "status"}
      className={`pointer-events-auto flex items-start gap-3 rounded-md border px-4 py-3 text-sm shadow-lg ${tone}`}
    >
      <span className="flex-1">{toast.message}</span>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss notification"
        className="shrink-0 rounded px-1 leading-none opacity-70 hover:opacity-100"
      >
        &times;
      </button>
    </div>
  );
}

/**
 * Throws outside a provider on purpose. A no-op fallback would recreate the very
 * failure this primitive exists to fix: an error message that goes nowhere.
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside a ToastProvider");
  return ctx;
}
