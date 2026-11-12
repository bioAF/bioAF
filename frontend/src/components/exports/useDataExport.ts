"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { isAbortError } from "@/lib/abort";
import { onSessionReset } from "@/lib/session";
import { useSubmitGuard } from "@/hooks/useSubmitGuard";
import {
  DEFAULT_EXPORT_OPTIONS,
  parseSignedLink,
  type ExportEstimate,
  type ExportOptions,
  type ExportTarget,
  type SignedLink,
} from "./exportTargets";

export type ExportPhase<E extends ExportEstimate> =
  | { name: "options"; error?: string }
  | { name: "estimating" }
  | { name: "estimate"; estimate: E; inputs: ExportOptions }
  | { name: "exporting"; estimate: E; inputs: ExportOptions }
  | { name: "downloaded" }
  | { name: "link"; link: SignedLink }
  | { name: "failed"; message: string; estimate: E; inputs: ExportOptions };

export type CopyState = "idle" | "copied" | "unavailable";

const COPIED_MS = 2000;

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/**
 * One export dialog's lifecycle, for any `ExportTarget`.
 *
 * Each opening owns its work. Closing, a different entity, a new session, or unmounting aborts
 * the opening's requests, releases its submit guard, and ignores anything that settles later:
 * no result, error, copied flag, or saved file reaches a later opening. Server work already
 * accepted is not cancelled; the dialog just stops listening.
 *
 * Options survive an ordinary close of the same entity in the same session; anything else starts
 * from the defaults. Changing an option discards an estimate for the old options. Exports use the
 * options the estimate was made with, so an estimate never describes a different export.
 */
export function useDataExport<E extends ExportEstimate>(target: ExportTarget<E>, isOpen: boolean) {
  const [session, setSession] = useState(0);
  useEffect(() => onSessionReset(() => setSession((n) => n + 1)), []);
  const owner = `${target.key}#${session}`;

  const [options, setOptionsState] = useState<ExportOptions>(DEFAULT_EXPORT_OPTIONS);
  const optionsOwner = useRef(owner);
  const [phase, setPhase] = useState<ExportPhase<E>>({ name: "options" });
  const [copy, setCopy] = useState<CopyState>("idle");
  const guard = useSubmitGuard();
  const { reset: resetGuard } = guard;

  // The current action's controller; replacing or aborting it disowns the action.
  const action = useRef<AbortController | null>(null);
  const opening = useRef<object | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearCopy = useCallback(() => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = null;
    setCopy("idle");
  }, []);

  const abandon = useCallback(() => {
    action.current?.abort();
    action.current = null;
    resetGuard();
  }, [resetGuard]);

  useEffect(() => {
    if (!isOpen) return;
    const thisOpening = {};
    opening.current = thisOpening;
    if (optionsOwner.current !== owner) {
      optionsOwner.current = owner;
      setOptionsState(DEFAULT_EXPORT_OPTIONS);
    }
    setPhase({ name: "options" });
    clearCopy();
    return () => {
      if (opening.current === thisOpening) opening.current = null;
      abandon();
      clearCopy();
    };
  }, [isOpen, owner, abandon, clearCopy]);

  /** Starts an action; returns its signal and a check that it is still the one in charge. */
  function begin() {
    action.current?.abort();
    const controller = new AbortController();
    action.current = controller;
    return {
      signal: controller.signal,
      current: () => action.current === controller && !controller.signal.aborted,
    };
  }

  function setOption(name: keyof ExportOptions, value: boolean) {
    abandon();
    setOptionsState((prev) => ({ ...prev, [name]: value }));
    setPhase({ name: "options" });
  }

  const calculate = () =>
    guard.run(async () => {
      const inputs = options;
      const { signal, current } = begin();
      setPhase({ name: "estimating" });
      try {
        const raw = await api.get<unknown>(
          `${target.basePath}/estimate?include_fastq=${inputs.includeFastq}`,
          { signal },
        );
        if (!current()) return;
        const estimate = target.parseEstimate(raw);
        setPhase(
          estimate
            ? { name: "estimate", estimate, inputs }
            : { name: "options", error: "The size estimate could not be read." },
        );
      } catch (err) {
        if (!current() || isAbortError(err)) return;
        setPhase({ name: "options", error: messageOf(err, "Failed to calculate size") });
      }
    });

  function payload(inputs: ExportOptions, method: "direct" | "gcs") {
    return {
      delivery_method: method,
      include_fastq: inputs.includeFastq,
      include_provenance: inputs.includeProvenance,
    };
  }

  const deliver = (method: "direct" | "gcs") => {
    if (phase.name !== "estimate") return Promise.resolve();
    const { estimate, inputs } = phase;
    return guard.run(async () => {
      const { signal, current } = begin();
      setPhase({ name: "exporting", estimate, inputs });
      try {
        if (method === "direct") {
          await api.download(`${target.basePath}/data`, "POST", payload(inputs, "direct"), {
            filename: target.fallbackFilename,
            signal,
          });
          if (current()) setPhase({ name: "downloaded" });
          return;
        }
        const link = parseSignedLink(await api.post<unknown>(`${target.basePath}/data`, payload(inputs, "gcs"), { signal }));
        if (!current()) return;
        setPhase(
          link
            ? { name: "link", link }
            : { name: "failed", message: "The download link could not be read.", estimate, inputs },
        );
      } catch (err) {
        if (!current() || isAbortError(err)) return;
        const fallback = method === "direct" ? "Download failed" : "Failed to generate download link";
        setPhase({ name: "failed", message: messageOf(err, fallback), estimate, inputs });
      }
    });
  };

  const downloadDirect = () => deliver("direct");

  /** `supported` is the storage's signed-link capability; an unsupported request is never sent. */
  const requestLink = (supported: boolean) => (supported ? deliver("gcs") : Promise.resolve());

  function back() {
    abandon();
    if (phase.name === "failed") setPhase({ name: "estimate", estimate: phase.estimate, inputs: phase.inputs });
    else setPhase({ name: "options" });
  }

  async function copyLink() {
    if (phase.name !== "link") return;
    const thisOpening = opening.current;
    const writeText = typeof navigator !== "undefined" ? navigator.clipboard?.writeText : undefined;
    if (!writeText) {
      setCopy("unavailable");
      return;
    }
    try {
      await writeText.call(navigator.clipboard, phase.link.url);
    } catch {
      if (opening.current === thisOpening) setCopy("unavailable");
      return;
    }
    if (opening.current !== thisOpening) return;
    if (copyTimer.current) clearTimeout(copyTimer.current);
    setCopy("copied");
    copyTimer.current = setTimeout(() => {
      copyTimer.current = null;
      setCopy("idle");
    }, COPIED_MS);
  }

  return {
    options,
    setOption,
    phase,
    copy,
    calculate,
    downloadDirect,
    requestLink,
    back,
    copyLink,
  };
}

export type DataExportController<E extends ExportEstimate> = ReturnType<typeof useDataExport<E>>;
