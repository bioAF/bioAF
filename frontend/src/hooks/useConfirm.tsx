"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";

/**
 * A promise-returning replacement for `window.confirm()`.
 *
 * The point of the promise is that it is a DROP-IN. Every destructive handler in
 * this app was written as a synchronous guard:
 *
 *     if (!confirm("Delete this?")) return;
 *     ...body...
 *
 * and every one of those handlers is already `async`, so the conversion is:
 *
 *     if (!(await confirm({ title: "Delete this?" }))) return;
 *     ...body unchanged...
 *
 * The body does not move into a callback, which is what makes this safe: there
 * is no restructuring for a refactor to get wrong, no captured argument to go
 * stale, and no half-migrated control flow.
 *
 * Why replace the native call at all: a browser lets the user suppress further
 * dialogs from a page. After that `confirm()` returns false without asking, so
 * every destructive button silently does nothing and the user gets no
 * explanation. A native confirm also cannot carry context, so it can ask
 * "Delete 12 files?" but never say which.
 */

export interface ConfirmOptions {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "default";
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

interface Pending extends ConfirmOptions {
  resolve: (value: boolean) => void;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  // Held in a ref as well so settle() can never read a stale closure when two
  // confirms land in the same tick.
  const pendingRef = useRef<Pending | null>(null);

  const settle = useCallback((value: boolean) => {
    const current = pendingRef.current;
    pendingRef.current = null;
    setPending(null);
    current?.resolve(value);
  }, []);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      // If something is already open, resolve it false rather than dropping it.
      // A dropped promise never settles, which would hang the calling handler
      // and strand whatever busy flag it set.
      pendingRef.current?.resolve(false);
      const next: Pending = { ...options, resolve };
      pendingRef.current = next;
      setPending(next);
    });
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <ConfirmDialog
        open={pending !== null}
        title={pending?.title ?? ""}
        message={pending?.message ?? ""}
        confirmLabel={pending?.confirmLabel}
        cancelLabel={pending?.cancelLabel}
        variant={pending?.variant}
        onConfirm={() => settle(true)}
        onCancel={() => settle(false)}
      />
    </ConfirmContext.Provider>
  );
}

/**
 * Throws outside a provider on purpose. A no-op fallback that returned true
 * would let a destructive action run with no gate at all, and one that returned
 * false would make the button look broken. Both are worse than failing loudly.
 */
export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used inside a ConfirmProvider");
  return ctx;
}
