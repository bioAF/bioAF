"use client";

import { useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/auth";

interface TerraformEvent {
  event_type: string;
  message: string;
  resource_address?: string;
  resources_completed?: number;
  resources_total?: number;
  log_line?: string;
}

interface TerraformProgressModalProps {
  title: string;
  sseUrl: string;
  onComplete: () => void;
  onClose: () => void;
}

type ModalStatus = "connecting" | "running" | "complete" | "error";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function TerraformProgressModal({
  title,
  sseUrl,
  onComplete,
  onClose,
}: TerraformProgressModalProps) {
  const [status, setStatus] = useState<ModalStatus>("connecting");
  const [events, setEvents] = useState<TerraformEvent[]>([]);
  const [resourcesCompleted, setResourcesCompleted] = useState(0);
  const [resourcesTotal, setResourcesTotal] = useState(0);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [showLog, setShowLog] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;

    async function streamEvents() {
      const token = getToken();
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
      };
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }

      let response: Response;
      try {
        response = await fetch(`${API_URL}${sseUrl}`, {
          method: "POST",
          headers,
          signal: controller.signal,
        });
      } catch {
        if (!controller.signal.aborted) {
          setStatus("error");
          setErrorMessage("Connection to server lost");
        }
        return;
      }

      if (!response.ok) {
        const body = await response.text().catch(() => "");
        let detail = `Server error (${response.status})`;
        try {
          const parsed = JSON.parse(body);
          if (parsed.detail) detail = parsed.detail;
        } catch {
          // use default detail
        }
        setStatus("error");
        setErrorMessage(detail);
        return;
      }

      setStatus("running");

      const reader = response.body?.getReader();
      if (!reader) {
        setStatus("error");
        setErrorMessage("No response stream available");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          // Keep the last incomplete line in the buffer
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const json = line.slice(6);
            let event: TerraformEvent;
            try {
              event = JSON.parse(json);
            } catch {
              continue;
            }

            setEvents((prev) => [...prev, event]);

            if (event.resources_total) {
              setResourcesTotal(event.resources_total);
            }
            if (event.resources_completed !== undefined) {
              setResourcesCompleted(event.resources_completed);
            }
            if (event.log_line) {
              setLogLines((prev) => [...prev, event.log_line!]);
            }

            if (
              event.event_type === "apply_complete" ||
              event.event_type === "stack_complete"
            ) {
              setStatus("complete");
              return;
            } else if (
              event.event_type === "apply_error" ||
              event.event_type === "stack_error"
            ) {
              setStatus("error");
              setErrorMessage(event.message);
              return;
            }
          }
        }
        // Stream ended without a terminal event -- treat as error
        setStatus((prev) => {
          if (prev === "running" || prev === "connecting") {
            setErrorMessage("Stream ended unexpectedly");
            return "error";
          }
          return prev;
        });
      } catch {
        if (!controller.signal.aborted) {
          setStatus("error");
          setErrorMessage("Connection to server lost");
        }
      }
    }

    streamEvents();

    return () => {
      controller.abort();
    };
  }, [sseUrl]);

  const progressPct =
    resourcesTotal > 0 ? Math.round((resourcesCompleted / resourcesTotal) * 100) : 0;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 p-6">
        <h2 className="text-lg font-semibold mb-4">{title}</h2>

        <div data-testid="tf-modal-status" className="mb-4">
          {status === "connecting" && (
            <p className="text-sm text-gray-500">Connecting...</p>
          )}
          {status === "running" && (
            <p className="text-sm text-blue-600 flex items-center gap-2">
              <span className="inline-block h-3 w-3 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
              Running...
            </p>
          )}
          {status === "complete" && (
            <p className="text-sm text-green-600 font-medium">Complete</p>
          )}
          {status === "error" && (
            <p data-testid="tf-modal-error" className="text-sm text-red-600 font-medium">
              Error: {errorMessage}
            </p>
          )}
        </div>

        {resourcesTotal > 0 && (
          <div data-testid="tf-progress-bar" className="mb-4">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>
                {resourcesCompleted} / {resourcesTotal} resources
              </span>
              <span>{progressPct}%</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>
        )}

        {events.length > 0 && (
          <ul className="space-y-1 mb-4 max-h-40 overflow-y-auto">
            {events
              .filter(
                (e) =>
                  e.event_type === "resource_complete" ||
                  e.event_type === "progress"
              )
              .map((e, i) => (
                <li key={i} className="text-sm flex items-center gap-2">
                  {e.event_type === "resource_complete" ? (
                    <span className="text-green-500">&#10003;</span>
                  ) : (
                    <span className="text-blue-500">&#9654;</span>
                  )}
                  <span className="text-gray-700">{e.message}</span>
                </li>
              ))}
          </ul>
        )}

        {logLines.length > 0 && (
          <div className="mb-4">
            <button
              onClick={() => setShowLog((v) => !v)}
              className="text-xs text-gray-500 underline"
            >
              {showLog ? "Hide" : "Show"} log
            </button>
            {showLog && (
              <pre className="mt-2 text-xs bg-gray-50 rounded p-2 max-h-32 overflow-y-auto">
                {logLines.join("\n")}
              </pre>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          {status === "complete" && (
            <button
              data-testid="tf-modal-done-btn"
              onClick={() => {
                onComplete();
                onClose();
              }}
              className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700"
            >
              Done
            </button>
          )}
          {status === "error" && (
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
            >
              Close
            </button>
          )}
          {(status === "connecting" || status === "running") && (
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
