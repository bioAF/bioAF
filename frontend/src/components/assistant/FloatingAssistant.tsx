"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { AssistantChat } from "@/components/assistant/AssistantChat";
import { assistantUiStore, useAssistantOpen } from "@/components/assistant/assistantUiStore";

/**
 * Host for the assistant chat PANEL. Mounted once in the root layout so it persists across page
 * navigation (the root layout does not unmount on client navigation), which keeps the conversation
 * alive as the user moves between screens. It has no launcher of its own: the launcher lives in the
 * Header (AssistantLauncher) and toggles this panel through the shared assistantUiStore. When closed
 * the panel is display:none, so nothing floats over page content; the old floating bubble is gone.
 *
 * Self-gating: renders nothing on the login screen, when unauthenticated, or for users without
 * assistant:use. Availability (no tool-capable provider) is handled inside the panel by AssistantChat.
 */
export function FloatingAssistant() {
  const pathname = usePathname();
  // Outer guard runs NO permission hook, so mounting the panel globally never triggers
  // usePermissions' /api/auth/me fetch or its login redirect on public/auth pages.
  if (pathname === "/login" || pathname === "/register") return null;
  if (!isAuthenticated()) return null;
  return <FloatingAssistantInner />;
}

function FloatingAssistantInner() {
  const { canAccess, loading: permLoading } = usePermissions();
  const open = useAssistantOpen();
  const [hasOpened, setHasOpened] = useState(false);

  useEffect(() => {
    if (open) setHasOpened(true);
  }, [open]);

  if (permLoading || !canAccess("assistant", "use")) return null;

  // Mount the chat on first open and keep it mounted (hidden via CSS when collapsed) so the
  // transcript and open conversation survive close/reopen + navigation for the whole session.
  if (!hasOpened) return null;

  return (
    <div
      data-testid="assistant-panel"
      className={`fixed bottom-6 right-6 z-50 w-[26rem] max-w-[calc(100vw-3rem)] h-[34rem] max-h-[calc(100vh-8rem)] bg-gray-50 rounded-xl shadow-2xl border border-gray-200 flex flex-col overflow-hidden ${
        open ? "" : "hidden"
      }`}
    >
      <div className="px-4 py-3 border-b border-gray-200 bg-white flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-900">Assistant</h2>
        <button
          type="button"
          onClick={() => assistantUiStore.close()}
          aria-label="Minimize assistant"
          className="text-gray-500 hover:text-gray-600 rounded p-1"
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
  );
}
