"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { AssistantChat } from "@/components/assistant/AssistantChat";

/**
 * Global floating assistant. Mounted once in the root layout, so it persists across page
 * navigation: the conversation stays alive as the user moves between screens (the root layout does
 * not unmount on client navigation). A lower-right bubble toggles a chat panel. AssistantChat is
 * mounted on first open and then kept mounted (hidden via CSS when collapsed) so the transcript and
 * open conversation survive close/reopen for the whole session.
 *
 * Self-gating: renders nothing on the login screen, when unauthenticated, or for users without
 * assistant:use. Availability (no tool-capable provider) is handled inside the panel by AssistantChat.
 */
export function FloatingAssistant() {
  const pathname = usePathname();
  // Outer guard runs NO permission hook, so mounting the bubble globally never triggers
  // usePermissions' /api/auth/me fetch or its login redirect on public/auth pages. The inner
  // component (which does use usePermissions) only mounts once the user is authenticated and
  // off the auth screens.
  if (pathname === "/login" || pathname === "/register") return null;
  if (!isAuthenticated()) return null;
  return <FloatingAssistantInner />;
}

function FloatingAssistantInner() {
  const { canAccess, loading: permLoading } = usePermissions();
  const [open, setOpen] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);

  if (permLoading || !canAccess("assistant", "use")) return null;

  function toggle() {
    setOpen((prev) => !prev);
    setHasOpened(true);
  }

  return (
    <>
      {hasOpened && (
        <div
          data-testid="assistant-panel"
          className={`fixed bottom-24 right-6 z-50 w-[26rem] max-w-[calc(100vw-3rem)] h-[34rem] max-h-[calc(100vh-8rem)] bg-gray-50 rounded-xl shadow-2xl border border-gray-200 flex flex-col overflow-hidden ${
            open ? "" : "hidden"
          }`}
        >
          <div className="px-4 py-3 border-b border-gray-200 bg-white flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-900">Assistant</h2>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Minimize assistant"
              className="text-gray-400 hover:text-gray-600 rounded p-1"
            >
              <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <div className="flex-1 min-h-0 flex flex-col">
            <AssistantChat />
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={toggle}
        aria-label={open ? "Close assistant" : "Open assistant"}
        aria-expanded={open}
        className="fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full bg-bioaf-600 text-white shadow-xl ring-1 ring-black/5 hover:bg-bioaf-700 hover:scale-105 focus:outline-none focus-visible:ring-2 focus-visible:ring-bioaf-400 flex items-center justify-center transition-all"
      >
        {open ? (
          <svg width="22" height="22" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        ) : (
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v8a1.5 1.5 0 01-1.5 1.5H9l-4 4v-4H5.5A1.5 1.5 0 014 13.5v-8z"
              fill="currentColor"
            />
          </svg>
        )}
      </button>
    </>
  );
}
