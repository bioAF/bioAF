"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { PlanConfirmCard } from "@/components/assistant/PlanConfirmCard";
import { api, ApiError } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import type {
  AssistantAvailability,
  AssistantConfirmResponse,
  AssistantConversationListResponse,
  AssistantConversationResponse,
  AssistantConversationSummary,
  AssistantConversationTranscript,
  AssistantMessageResponse,
  AssistantPlanStep,
} from "@/lib/types";

type EntityLink = { label: string; href: string };

type ChatEntry =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; text: string }
  | { id: string; kind: "system"; text: string }
  | { id: string; kind: "result"; text: string; links: EntityLink[] }
  | {
      id: string;
      kind: "plan";
      planId: number;
      steps: AssistantPlanStep[];
      resolved?: "approved" | "cancelled";
    };

/**
 * Turn a confirm result into clickable links to what the assistant just created or touched, so the
 * user is handed straight to the experiment/run instead of being left to find it. Uses the same
 * detail routes the rest of the app navigates to. (Rendered with next/link so following one is a
 * client navigation that keeps the floating bubble - and the conversation - mounted.)
 */
function entityLinks(resp: AssistantConfirmResponse): EntityLink[] {
  const r = resp.result ?? {};
  const links: EntityLink[] = [];
  const runId = resp.run_id ?? (typeof r.run_id === "number" ? r.run_id : null);
  if (typeof runId === "number") links.push({ label: `View run #${runId}`, href: `/pipelines/runs/${runId}` });
  if (typeof r.experiment_id === "number") {
    links.push({ label: "Open experiment", href: `/experiments/${r.experiment_id}` });
  }
  return links;
}

/** A plain-language summary of a confirmed action, derived from the recognizable result fields. */
function summarizeResult(resp: AssistantConfirmResponse): string {
  const r = resp.result ?? {};
  if (resp.run_id) return `Run #${resp.run_id} started.`;
  if (typeof r.code === "string" && typeof r.name === "string") {
    return `Created experiment "${r.name}" (${r.code}).`;
  }
  if (typeof r.sample_id === "number") {
    return `Added sample ${typeof r.external_id === "string" ? r.external_id : `#${r.sample_id}`}.`;
  }
  if (typeof r.pipeline_key === "string" && typeof r.version === "string") {
    return `Installed ${r.pipeline_key} (${r.version}).`;
  }
  if (resp.executed) return "Done.";
  if (resp.result) return "Plan approved. Live launch is off, so the run was not started.";
  return "Plan approved.";
}

/**
 * The assistant conversation surface: transcript, composer, plan-confirm cards, and a history
 * list to revisit/resume past chats, plus the permission/availability gates. Extracted from the
 * former full-page `/assistant` so it can be hosted inside the global FloatingAssistant bubble (and
 * stay mounted across navigation, which is what keeps the session alive as the user moves between
 * pages). It does NOT own the auth redirect or page chrome; the host decides when to render it.
 */
export function AssistantChat() {
  const { canAccess, loading: permLoading } = usePermissions();
  const canUse = canAccess("assistant", "use");

  const [enabled, setEnabled] = useState<boolean | undefined>(undefined);
  const [availabilityReason, setAvailabilityReason] = useState<string | null>(null);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [confirmingPlanId, setConfirmingPlanId] = useState<number | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [conversations, setConversations] = useState<AssistantConversationSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

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

  async function handleConfirm(planId: number) {
    setConfirmingPlanId(planId);
    try {
      const resp = await api.post<AssistantConfirmResponse>(
        `/api/assistant/action-plans/${planId}/confirm`,
      );
      resolvePlan(planId, "approved");
      appendEntry({
        id: makeId(),
        kind: "result",
        text: summarizeResult(resp),
        links: entityLinks(resp),
      });
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

  function startNewChat() {
    setEntries([]);
    conversationIdRef.current = null;
    setShowHistory(false);
  }

  async function toggleHistory() {
    const next = !showHistory;
    setShowHistory(next);
    if (!next) return;
    setHistoryLoading(true);
    try {
      const data = await api.get<AssistantConversationListResponse>("/api/assistant/conversations");
      setConversations(data.conversations);
    } catch {
      setConversations([]);
    } finally {
      setHistoryLoading(false);
    }
  }

  async function resumeConversation(id: number) {
    try {
      const t = await api.get<AssistantConversationTranscript>(`/api/assistant/conversations/${id}/messages`);
      // Re-render the transcript by interleaving messages and plan cards in time order. Tool-role
      // messages and content-less assistant turns are internal (a plan card stands in for them).
      const items: { ts: string; seq: number; entry: ChatEntry }[] = [];
      for (const m of t.messages) {
        if (m.role === "user" && m.content) {
          items.push({ ts: m.created_at, seq: m.id, entry: { id: makeId(), kind: "user", text: m.content } });
        } else if (m.role === "assistant" && m.content) {
          items.push({ ts: m.created_at, seq: m.id, entry: { id: makeId(), kind: "assistant", text: m.content } });
        }
      }
      for (const p of t.plans) {
        items.push({
          ts: p.created_at,
          seq: p.id,
          // A still-proposed plan stays confirmable on resume; anything else is shown as resolved.
          entry: {
            id: makeId(),
            kind: "plan",
            planId: p.id,
            steps: p.steps ?? [],
            resolved: p.status === "proposed" ? undefined : "approved",
          },
        });
      }
      items.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : a.seq - b.seq));
      setEntries(items.map((i) => i.entry));
      conversationIdRef.current = id;
      setShowHistory(false);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Could not load that conversation.";
      appendEntry({ id: makeId(), kind: "system", text: message });
      setShowHistory(false);
    }
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

  // A plain element (not a nested component) so it is not remounted on every keystroke.
  const chatBody = (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0" data-testid="assistant-transcript">
        {entries.length === 0 && (
          <div className="text-center text-gray-500 mt-6 text-sm">
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
          if (entry.kind === "result") {
            return (
              <div key={entry.id} className="flex justify-center">
                <div className="bg-gray-100 text-gray-700 rounded px-3 py-2 text-xs max-w-[90%] text-center space-y-1.5">
                  <div className="whitespace-pre-wrap break-words">{entry.text}</div>
                  {entry.links.length > 0 && (
                    <div className="flex flex-wrap gap-3 justify-center">
                      {entry.links.map((l) => (
                        <Link
                          key={l.href}
                          href={l.href}
                          className="text-bioaf-700 hover:text-bioaf-800 underline font-medium"
                        >
                          {l.label}
                        </Link>
                      ))}
                    </div>
                  )}
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

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-3 py-2 border-b border-gray-200 bg-white flex items-center gap-2">
        <button
          type="button"
          onClick={toggleHistory}
          className={`text-xs px-2 py-1 rounded border ${
            showHistory
              ? "bg-bioaf-50 border-bioaf-300 text-bioaf-700"
              : "border-gray-200 text-gray-600 hover:bg-gray-50"
          }`}
        >
          History
        </button>
        <button
          type="button"
          onClick={startNewChat}
          className="text-xs px-2 py-1 rounded border border-gray-200 text-gray-600 hover:bg-gray-50"
        >
          New chat
        </button>
      </div>

      {showHistory ? (
        <div className="flex-1 overflow-y-auto p-3 space-y-2 min-h-0" data-testid="assistant-history">
          {historyLoading && (
            <div className="flex justify-center pt-6">
              <LoadingSpinner size="md" />
            </div>
          )}
          {!historyLoading && conversations.length === 0 && (
            <div className="text-center text-gray-500 text-sm mt-6">No past conversations yet.</div>
          )}
          {conversations.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => resumeConversation(c.id)}
              className="w-full text-left bg-white border border-gray-200 rounded px-3 py-2 hover:bg-gray-50"
            >
              <div className="text-sm font-medium text-gray-900 truncate">
                {c.title ?? c.preview ?? "New conversation"}
              </div>
              <div className="text-xs text-gray-500">
                {c.message_count} message{c.message_count === 1 ? "" : "s"}
              </div>
            </button>
          ))}
        </div>
      ) : (
        chatBody
      )}
    </div>
  );
}
