"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const POLL_INTERVAL_MS = 2000;
const SESSION_KEY = "bioaf_backend_ready";

/**
 * Ensures the backend is healthy before dismissing the loading screen.
 *
 * - If the backend responds on the first check, sets ready immediately
 *   (normal app load with a running backend).
 * - If the first check fails, polls every 2s and reloads the page once
 *   the backend becomes available so all hooks start fresh.
 * - Caches readiness in sessionStorage so subsequent navigations within
 *   the same tab never re-trigger the loading screen.
 */
export function useBackendReady() {
  const alreadyConfirmed =
    typeof window !== "undefined" &&
    sessionStorage.getItem(SESSION_KEY) === "true";

  const [ready, setReady] = useState(alreadyConfirmed);

  useEffect(() => {
    if (alreadyConfirmed) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let isFirstAttempt = true;

    async function check() {
      try {
        const res = await fetch(`${API_URL}/api/health/ready`, {
          cache: "no-store",
        });
        if (!cancelled && res.ok) {
          const body = await res.json();
          if (body.status === "ok") {
            sessionStorage.setItem(SESSION_KEY, "true");
            if (isFirstAttempt) {
              // Backend was already up -- no reload needed
              setReady(true);
            } else {
              // Backend just came up after we waited -- reload so hooks
              // that already failed can start fresh
              window.location.reload();
            }
            return;
          }
        }
      } catch {
        // Backend not reachable yet
      }
      isFirstAttempt = false;
      if (!cancelled) {
        timer = setTimeout(check, POLL_INTERVAL_MS);
      }
    }

    check();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [alreadyConfirmed]);

  return { ready };
}
