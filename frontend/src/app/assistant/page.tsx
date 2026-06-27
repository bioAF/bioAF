"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { PlanConfirmCard } from "@/components/assistant/PlanConfirmCard";
import { AssistantLaunchToggle } from "@/components/assistant/AssistantLaunchToggle";
import { isAuthenticated } from "@/lib/auth";
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

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        {children}
      </div>
    </div>
  );
}

export default function AssistantPage() {
  const router = useRouter();
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
    if (!isAuthenticated()) {
      router.push("/login");
    }
  }, [router]);

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

  // ---- Render gates ----

  if (permLoading || (canUse && enabled === undefined)) {
    return (
      <Shell>
        <main className="flex-1 flex items-center justify-center">
          <LoadingSpinner size="lg" />
        </main>
      </Shell>
    );
  }

  if (!canUse) {
    return (
      <Shell>
        <main className="flex-1 flex items-center justify-center p-6">
          <div className="bg-white rounded-lg shadow p-10 text-center max-w-xl">
            <h2 className="text-lg font-semibold mb-2">Assistant</h2>
            <p className="text-gray-600">You don&apos;t have permission to use the Assistant.</p>
          </div>
        </main>
      </Shell>
    );
  }

  if (enabled === false) {
    return (
      <Shell>
        <main className="flex-1 flex items-center justify-center p-6">
          <div className="bg-white rounded-lg shadow p-10 text-center max-w-xl">
            <h2 className="text-lg font-semibold mb-2">Assistant unavailable</h2>
            <p className="text-gray-600">
              {availabilityReason ??
                "Your organization has no tool-capable LLM provider configured."}
            </p>
            <p className="text-gray-500 text-sm mt-3">
              Set a tool-capable provider in Settings &gt; Integrations &gt; LLMs to enable the
              Assistant.
            </p>
          </div>
        </main>
      </Shell>
    );
  }

  // ---- Chat ----

  return (
    <Shell>
      <main className="flex-1 flex flex-col overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200 bg-white flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">Assistant</h1>
            <p className="text-sm text-gray-500">
              Describe what you have and what you want. The assistant proposes a pipeline and asks
              you to confirm before anything runs.
            </p>
          </div>
          <AssistantLaunchToggle
            enabled={launchEnabled}
            canConfigure={canConfigure}
            saving={launchSaving}
            onChange={handleToggleLaunch}
          />
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-4" data-testid="assistant-transcript">
          {entries.length === 0 && (
            <div className="text-center text-gray-400 mt-10 text-sm">
              Start by describing your samples and goal, e.g. &quot;recommend a pipeline for my mouse
              RNA experiment.&quot;
            </div>
          )}

          {entries.map((entry) => {
            if (entry.kind === "user") {
              return (
                <div key={entry.id} className="flex justify-end">
                  <div className="bg-bioaf-600 text-white rounded-lg px-4 py-2 max-w-2xl whitespace-pre-wrap">
                    {entry.text}
                  </div>
                </div>
              );
            }
            if (entry.kind === "assistant") {
              return (
                <div key={entry.id} className="flex justify-start">
                  <div className="bg-white border border-gray-200 rounded-lg px-4 py-2 max-w-2xl whitespace-pre-wrap">
                    {entry.text}
                  </div>
                </div>
              );
            }
            if (entry.kind === "system") {
              return (
                <div key={entry.id} className="flex justify-center">
                  <div className="bg-gray-100 text-gray-600 rounded px-3 py-1.5 text-sm max-w-2xl whitespace-pre-wrap break-words">
                    {entry.text}
                  </div>
                </div>
              );
            }
            // plan
            return (
              <div key={entry.id} className="flex justify-start">
                <div className="max-w-2xl w-full">
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
              <div className="bg-white border border-gray-200 rounded-lg px-4 py-2 text-gray-500 text-sm">
                Thinking...
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="border-t border-gray-200 bg-white p-4">
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
              className="flex-1 px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-bioaf-500 disabled:bg-gray-50"
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="bg-bioaf-600 text-white px-5 py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </div>
      </main>
    </Shell>
  );
}
