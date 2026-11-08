"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { logError } from "@/lib/errorReporting";
import {
  BACKEND_READY_KEY,
  clearMaintenance,
  getMaintenance,
  isMaintenanceBody,
  reportMaintenance,
  subscribeMaintenance,
  type MaintenanceInfo,
} from "@/lib/maintenance";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const POLL_INTERVAL_MS = 2000;
const SESSION_KEY = BACKEND_READY_KEY;

/**
 * How many consecutive failures before we tell the user anything is wrong.
 * A cold container or a slow first response should not flash an alarming
 * message, so the first few seconds stay quiet and look like an ordinary boot.
 */
const GRACE_ATTEMPTS = 3;

/**
 * Ensures the backend is healthy before dismissing the loading screen.
 *
 * - If the backend responds on the first check, sets ready immediately
 *   (normal app load with a running backend).
 * - If the first check fails, polls every 2s and reloads the page once
 *   the backend becomes available so all hooks start fresh.
 * - Caches readiness in sessionStorage so subsequent navigations within
 *   the same tab never re-trigger the loading screen.
 *
 * `unreachable` is why this hook returns more than a boolean. Measured on the
 * deployed app 2026-08-07: a 500 on `/api/health/ready`, with every other
 * endpoint healthy, held the full-screen "Loading bioAF..." splash forever. It
 * probed 15 times in 30 seconds and the entire user-visible UI was that one
 * string: no message, no retry, no live region, and zero focusable elements.
 * The polling was never the bug. The bug was that nothing could tell the shell
 * that waiting had turned into failing, so the shell had nothing to say.
 *
 * It keeps polling after that: the backend usually does come back, and giving up
 * would be the opposite defect. `unreachable` only changes what the user is told.
 *
 * `maintenance` is a third state (plan 9): the backend answered, with 503 and
 * `{"status": "maintenance"}`, because it is finishing a database upgrade or one
 * failed. That is not "unreachable", at boot or later. In a tab that was already
 * working, the API client reports it (lib/maintenance) and this hook polls until
 * the backend is ready, then clears it without reloading, so unsaved work stays.
 */
export function useBackendReady() {
  // Read once, at mount. Reporting maintenance later drops the cached flag; re-reading it on that
  // render would restart the boot probe in a working tab, and the probe's recovery reloads the page.
  const [alreadyConfirmed] = useState(
    () =>
      typeof window !== "undefined" &&
      sessionStorage.getItem(SESSION_KEY) === "true",
  );

  const [ready, setReady] = useState(alreadyConfirmed);
  const [unreachable, setUnreachable] = useState(false);
  const [maintenance, setMaintenance] = useState<MaintenanceInfo | null>(
    getMaintenance(),
  );

  // Lets `retryNow` run the same check the timer runs, without duplicating it.
  const checkRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (alreadyConfirmed) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let isFirstAttempt = true;
    let failures = 0;
    // The probe repeats every 2s. Logging each failure would bury the console in
    // one repeating line and hide the first, most useful occurrence.
    let logged = false;

    async function check() {
      clearTimeout(timer);
      try {
        const res = await fetch(`${API_URL}/api/health/ready`, {
          cache: "no-store",
        });
        if (!cancelled && res.status === 503) {
          const body = await res.json().catch(() => null);
          if (isMaintenanceBody(body)) {
            // Answered, just not yet serving: say so, never "unreachable", and keep waiting.
            reportMaintenance(body);
            setMaintenance(body);
            setUnreachable(false);
            isFirstAttempt = false;
            timer = setTimeout(check, POLL_INTERVAL_MS);
            return;
          }
        }
        if (!cancelled && res.ok) {
          const body = await res.json();
          if (body.status === "ok") {
            sessionStorage.setItem(SESSION_KEY, "true");
            if (isFirstAttempt) {
              // Backend was already up -- no reload needed
              setReady(true);
            } else {
              // Backend just came up after we waited -- reload so hooks
              // that already failed can start fresh. Safe: the splash covers
              // the whole app while this can fire, so nothing is discarded.
              window.location.reload();
            }
            return;
          }
        }
        if (!cancelled && !res.ok) {
          if (!logged) {
            logError(
              "checking whether the backend is ready",
              new Error(`probe returned ${res.status}`),
            );
            logged = true;
          }
        }
      } catch (err) {
        if (!logged) {
          logError("checking whether the backend is ready", err);
          logged = true;
        }
      }
      isFirstAttempt = false;
      failures += 1;
      if (!cancelled) {
        if (failures >= GRACE_ATTEMPTS) setUnreachable(true);
        timer = setTimeout(check, POLL_INTERVAL_MS);
      }
    }

    checkRef.current = () => {
      void check();
    };
    check();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [alreadyConfirmed]);

  // A tab that was already working: the API client reports maintenance; poll until the backend is
  // ready again and then clear it in place. No reload: the page and anything typed into it stay.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function recheck() {
      try {
        const res = await fetch(`${API_URL}/api/health/ready`, {
          cache: "no-store",
        });
        if (!cancelled && res.ok) {
          const body = await res.json();
          if (body.status === "ok") {
            sessionStorage.setItem(SESSION_KEY, "true");
            clearMaintenance();
            return;
          }
        }
      } catch {
        // Still down; keep waiting.
      }
      if (!cancelled) timer = setTimeout(recheck, POLL_INTERVAL_MS);
    }

    const unsubscribe = subscribeMaintenance((info) => {
      setMaintenance(info);
      if (info && !timer && !cancelled) void recheck();
      if (!info) {
        clearTimeout(timer);
        timer = undefined;
      }
    });
    return () => {
      cancelled = true;
      clearTimeout(timer);
      unsubscribe();
    };
  }, []);

  /** Check now instead of waiting out the interval. Wired to the splash's retry. */
  const retryNow = useCallback(() => {
    checkRef.current();
  }, []);

  return { ready, unreachable, maintenance, retryNow };
}
