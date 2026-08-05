"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";

interface SavedPrompt {
  id: number;
  name: string;
  body: string;
  created_by_user_id: number;
  created_by_user_label: string;
  created_at: string;
}

interface Props {
  body: string;
  onClose: () => void;
  onRunWithCustomBody: (body: string) => Promise<void> | void;
  onSavedAndRun: (saved: SavedPrompt) => Promise<void> | void;
}

export function AssembledPromptModal({
  body,
  onClose,
  onRunWithCustomBody,
  onSavedAndRun,
}: Props) {
  useDismissOnEscape(true, () => onClose());
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(body);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function saveAndRun() {
    if (!name.trim()) {
      setError("Give the prompt a name before saving.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await api.post<SavedPrompt>("/api/agent_reviews/prompts", {
        name: name.trim(),
        body: draft,
      });
      await onSavedAndRun(saved);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function runOnce() {
    setBusy(true);
    setError(null);
    try {
      await onRunWithCustomBody(draft);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const modified = draft !== body;

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl max-h-[85vh] overflow-y-auto p-6">
        <div className="flex items-start justify-between">
          <h3 className="text-lg font-semibold">Assembled prompt</h3>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <p className="text-sm text-gray-600 mt-1">
          This is what will be sent to the active LLM. You can customize it for
          this run, or name it and save for future use.
        </p>

        {editing ? (
          <textarea aria-label="Draft"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="mt-4 w-full font-mono text-xs border border-gray-300 rounded p-3 h-80"
          />
        ) : (
          <pre className="mt-4 bg-gray-50 border border-gray-200 rounded p-3 text-xs whitespace-pre-wrap max-h-80 overflow-y-auto">
            {draft}
          </pre>
        )}

        {editing && (
          <div className="mt-3 flex items-center gap-2">
            <label htmlFor="save-as" className="text-sm text-gray-700">Save as:</label>
            <input id="save-as"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="prompt name (leave blank to use once without saving)"
              className="flex-1 border border-gray-300 rounded px-3 py-1.5 text-sm"
            />
          </div>
        )}

        {error && (
          <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
            {error}
          </div>
        )}

        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded"
          >
            Cancel
          </button>
          {!editing ? (
            <button
              onClick={() => setEditing(true)}
              className="px-3 py-1.5 text-sm border border-bioaf-600 text-bioaf-700 rounded"
            >
              Customize
            </button>
          ) : (
            <>
              <button
                onClick={runOnce}
                disabled={busy || !modified}
                title={modified ? "" : "No changes; close and run from the section builder."}
                className="px-3 py-1.5 text-sm border border-bioaf-600 text-bioaf-700 rounded disabled:opacity-50"
              >
                {busy ? "…" : "Use this once"}
              </button>
              <button
                onClick={saveAndRun}
                disabled={busy || !name.trim()}
                className="px-3 py-1.5 text-sm bg-bioaf-600 text-white rounded disabled:bg-gray-300"
              >
                {busy ? "Saving…" : "Save and run"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
