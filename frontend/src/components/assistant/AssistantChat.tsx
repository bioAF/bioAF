"use client";

import { useEffect, useRef, useState } from "react";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { PlanConfirmCard } from "@/components/assistant/PlanConfirmCard";
import { AssistantLaunchToggle } from "@/components/assistant/AssistantLaunchToggle";
import { api, ApiError } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import type {
  AssistantAvailability,
  AssistantConfirmResponse,
  AssistantConversationResponse,
  AssistantMessageResponse,
  AssistantPlanStep,
  AssistantSettings,
} from "@/lib/types";

type ChatEntry =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; text: string }
  | { id: string; kind: "system"; text: string }
  | {
      id: string;
      kind: "plan";
      planId: number;
      steps: AssistantPlanStep[];
      resolved?: "approved" | "cancelled";
    };

/**
 * The assistant conversation surface: transcript, composer, and plan-confirm cards, plus the
 * permission/availability gates. Extracted from the former full-page `/assistant` so it can be
 * hosted inside the global FloatingAssistant bubble (and stay mounted across navigation, which is
 * what keeps the session alive as the user moves between pages). It does NOT own the auth redirect
 * or page chrome; the host decides when to render it.
 */
export function AssistantChat() {
  const { canAccess, loading: permLoading } = usePermissions();
  const canUse = canAccess("assistant", "use");
  const canConfigure = canAccess("settings", "configure");

  const [enabled, setEnabled] = useState<boolean | undefined>(undefined);
  const [availabilityReason, setAvailabilityReason] = useState<string | null>(null);
  const [launchEnabled, setLaunchEnabled] = useState<boolean>(false);
  const [launchSaving, setLaunchSaving] = useState<boolean>(false);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [confirmingPlanId, setConfirmingPlanId] = useState<number | null>(null);

  const conversationIdRef = useRef<number | null>(null);
  const idCounter = useRef(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  function makeId(): string {
    idCounter.current += 1;
    return String(idCounter.current);
  }

  function appendEntry(entry: ChatEntry) {
    setEntries((prev) => [...prev, entry]);
  }

  useEffect(() => {
    if (permLoading || !canUse) return;
    api
      .get<AssistantAvailability>("/api/assistant/availability")
      .then((data) => {
        setEnabled(data.enabled);
        setAvailabilityReason(data.reason ?? null);
      })
      .catch((err) => {
        setEnabled(false);
        setAvailabilityReason(err instanceof Error ? err.message : "Could not check availability.");
      });
  }, [permLoading, canUse]);

  useEffect(() => {
    if (permLoading || !canUse) return;
    // Non-fatal: if the settings read fails, leave the toggle showing its default (off).
    api
      .get<AssistantSettings>("/api/assistant/settings")
      .then((s) => setLaunchEnabled(s.launch_enabled))
      .catch(() => {});
  }, [permLoading, canUse]);

  useEffect(() => {
    // Optional-chain the method: jsdom (and some embedded webviews) don't implement it.
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [entries, sending]);

  async function ensureConversation(): Promise<number> {
    if (conversationIdRef.current !== null) return conversationIdRef.current;
    const conv = await api.post<AssistantConversationResponse>("/api/assistant/conversations", {});
    conversationIdRef.current = conv.id;
    return conv.id;
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    appendEntry({ id: makeId(), kind: "user", text });
    setInput("");
    setSending(true);
    try {
      const convId = await ensureConversation();
      const resp = await api.post<AssistantMessageResponse>(
        `/api/assistant/conversations/${convId}/messages`,
        { text },
      );
      if (resp.status === "answered") {
        appendEntry({ id: makeId(), kind: "assistant", text: resp.text ?? "" });
      } else if (resp.status === "awaiting_confirmation" && resp.action_plan_id !== null) {
        appendEntry({
          id: makeId(),
          kind: "plan",
          planId: resp.action_plan_id,
          steps: resp.plan_steps ?? [],
        });
      } else if (resp.status === "step_cap_exceeded") {
        appendEntry({
          id: makeId(),
          kind: "system",
          text: "The assistant reached its step limit for this turn. Try rephrasing your request.",
        });
      } else if (resp.status === "unavailable") {
        appendEntry({
          id: makeId(),
          kind: "system",
          text: resp.reason ?? "The assistant is currently unavailable.",
        });
      }
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
      appendEntry({ id: makeId(), kind: "system", text: message });
    } finally {
      setSending(false);
    }
  }

  function resolvePlan(planId: number, resolved: "approved" | "cancelled") {
    setEntries((prev) =>
      prev.map((e) => (e.kind === "plan" && e.planId === planId ? { ...e, resolved } : e)),
    );
  }

  async function handleToggleLaunch(next: boolean) {
    setLaunchSaving(true);
    try {
      const s = await api.put<AssistantSettings>("/api/assistant/settings", { launch_enabled: next });
      setLaunchEnabled(s.launch_enabled);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Could not update the launch setting.";
      appendEntry({ id: makeId(), kind: "system", text: message });
    } finally {
      setLaunchSaving(false);
    }
  }

  async function handleConfirm(planId: number) {
    setConfirmingPlanId(planId);
    try {
      const resp = await api.post<AssistantConfirmResponse>(
        `/api/assistant/action-plans/${planId}/confirm`,
      );
      resolvePlan(planId, "approved");
      let summary: string;
      if (resp.run_id) {
        summary = `Run #${resp.run_id} started.`;
      } else if (resp.executed) {
        summary = `Done.${resp.result ? ` ${JSON.stringify(resp.result)}` : ""}`;
      } else if (resp.result) {
        summary = `Plan approved. Live launch is off, so the run was not started. Prepared launch request: ${JSON.stringify(resp.result)}`;
      } else {
        summary = "Plan approved.";
      }
      appendEntry({ id: makeId(), kind: "system", text: summary });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Could not confirm the plan.";
      appendEntry({ id: makeId(), kind: "system", text: message });
    } finally {
      setConfirmingPlanId(null);
    }
  }

  function handleCancel(planId: number) {
    resolvePlan(planId, "cancelled");
    appendEntry({ id: makeId(), kind: "system", text: "Plan cancelled. Nothing was run." });
  }

  // ---- Gates ----

  if (permLoading || (canUse && enabled === undefined)) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!canUse) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 text-center">
        <p className="text-gray-600 text-sm">You don&apos;t have permission to use the Assistant.</p>
      </div>
    );
  }

  if (enabled === false) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 text-center">
        <div>
          <p className="text-gray-700 font-medium mb-1">Assistant unavailable</p>
          <p className="text-gray-500 text-sm">
            {availabilityReason ?? "Your organization has no tool-capable LLM provider configured."}
          </p>
        </div>
      </div>
    );
  }

  // ---- Chat ----

  return (
    <div className="flex flex-col h-full min-h-0">
      {canConfigure && (
        <div className="px-4 py-2 border-b border-gray-200 bg-white flex justify-end">
          <AssistantLaunchToggle
            enabled={launchEnabled}
            canConfigure={canConfigure}
            saving={launchSaving}
            onChange={handleToggleLaunch}
          />
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0" data-testid="assistant-transcript">
        {entries.length === 0 && (
          <div className="text-center text-gray-400 mt-6 text-sm">
            Describe your samples and goal, e.g. &quot;recommend a pipeline for my mouse RNA
            experiment.&quot;
          </div>
        )}

        {entries.map((entry) => {
          if (entry.kind === "user") {
            return (
              <div key={entry.id} className="flex justify-end">
                <div className="bg-bioaf-600 text-white rounded-lg px-3 py-2 max-w-[85%] whitespace-pre-wrap text-sm">
                  {entry.text}
                </div>
              </div>
            );
          }
          if (entry.kind === "assistant") {
            return (
              <div key={entry.id} className="flex justify-start">
                <div className="bg-white border border-gray-200 rounded-lg px-3 py-2 max-w-[85%] whitespace-pre-wrap text-sm">
                  {entry.text}
                </div>
              </div>
            );
          }
          if (entry.kind === "system") {
            return (
              <div key={entry.id} className="flex justify-center">
                <div className="bg-gray-100 text-gray-600 rounded px-3 py-1.5 text-xs max-w-[90%] whitespace-pre-wrap break-words">
                  {entry.text}
                </div>
              </div>
            );
          }
          // plan
          return (
            <div key={entry.id} className="flex justify-start">
              <div className="max-w-[95%] w-full">
                <PlanConfirmCard
                  steps={entry.steps}
                  busy={confirmingPlanId === entry.planId}
                  resolved={entry.resolved}
                  onConfirm={() => handleConfirm(entry.planId)}
                  onCancel={() => handleCancel(entry.planId)}
                />
              </div>
            </div>
          );
        })}

        {sending && (
          <div className="flex justify-start" data-testid="assistant-thinking">
            <div className="bg-white border border-gray-200 rounded-lg px-3 py-2 text-gray-500 text-sm">
              Thinking...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-gray-200 bg-white p-3">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            handleSend();
          }}
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={sending}
            placeholder="Describe what you have and what you want..."
            aria-label="Message"
            className="flex-1 px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-bioaf-500 disabled:bg-gray-50 text-sm"
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            className="bg-bioaf-600 text-white px-4 py-2 rounded hover:bg-bioaf-700 disabled:opacity-50 text-sm"
          >
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
