"use client";

import { usePermissions } from "@/hooks/usePermissions";
import { assistantUiStore } from "@/components/assistant/assistantUiStore";

/**
 * The assistant launcher, mounted in the Header alongside the other universal items (search,
 * quick-create, notifications). Clicking it toggles the root-mounted chat panel via the shared UI
 * store. It replaces the old floating bottom-right bubble, which obscured page content in that
 * corner. Self-gates on assistant:use so users without it see no launcher.
 */
export function AssistantLauncher() {
  const { canAccess, loading } = usePermissions();
  if (loading || !canAccess("assistant", "use")) return null;

  return (
    <button
      type="button"
      onClick={() => assistantUiStore.toggle()}
      aria-label="Open assistant"
      title="Assistant"
      className="relative p-2 text-gray-500 hover:text-gray-700 focus:outline-none"
    >
      <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v8a1.5 1.5 0 01-1.5 1.5H9l-4 4v-4H5.5A1.5 1.5 0 014 13.5v-8z"
          fill="currentColor"
        />
      </svg>
    </button>
  );
}
