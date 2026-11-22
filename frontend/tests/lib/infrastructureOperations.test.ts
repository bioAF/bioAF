/**
 * Copy for infrastructure operations (storage lifecycle 11D), signed off in mockups M2-M10.
 */

import {
  bannerText,
  blockedReason,
  operationTitle,
  stepStateText,
  footerHint,
  conflictCopy,
  type OperationView,
} from "@/lib/infrastructureOperations";

function op(extra: Partial<OperationView> = {}): OperationView {
  return {
    id: 57,
    action: "compute_teardown",
    label: "Compute teardown",
    status: "running",
    phase: null,
    started_by: "Admin A",
    accepted_at: "2026-11-21T14:02:00Z",
    started_at: "2026-11-21T14:02:01Z",
    finished_at: null,
    steps: [],
    message: null,
    error_category: null,
    partial_effects: null,
    cancel_requested_at: null,
    cancel_requested_by: null,
    cancel_deadline: null,
    recovered_at: null,
    outcome_seen_at: null,
    resources: null,
    ...extra,
  };
}

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

describe("banner", () => {
  test("admins see the running action, who started it and when (M3)", () => {
    expect(bannerText({ busy: true, state: "running", operation: op(), interrupted: [] }, true)).toBe(
      `Compute teardown in progress, started by Admin A at ${time("2026-11-21T14:02:00Z")}. Other infrastructure actions are unavailable until it finishes.`,
    );
  });

  test("the image build variant names the build (M3)", () => {
    const build = op({ action: "notebook_image_build", label: "Notebook image build" });
    expect(bannerText({ busy: true, state: "running", operation: build, interrupted: [] }, true)).toMatch(
      /^Notebook image build in progress, started by Admin A at /,
    );
  });

  test("users without infrastructure actions see only maintenance (M4)", () => {
    expect(bannerText({ busy: true, state: "running", operation: null, interrupted: [] }, false)).toBe(
      "Infrastructure maintenance in progress. Pipeline runs, notebooks, and uploads may be unavailable until it finishes.",
    );
    expect(bannerText({ busy: true, state: "recovery_required", operation: null, interrupted: [] }, false)).toBe(
      "Infrastructure maintenance is paused. Pipeline runs, notebooks, and uploads may be unavailable until an administrator finishes it.",
    );
  });

  test("recovery required pauses actions (M10)", () => {
    const paused = op({ status: "recovery_required", action: "storage_destroy", label: "Storage destroy", id: 61 });
    expect(bannerText({ busy: true, state: "recovery_required", operation: paused, interrupted: [] }, true)).toBe(
      "Infrastructure actions are paused. Storage destroy (Operation #61) needs recovery before anything else can run.",
    );
  });

  test("an unseen interruption is announced once (M9)", () => {
    const interrupted = op({
      status: "interrupted",
      action: "compute_deploy",
      label: "Compute deployment",
      finished_at: "2026-11-21T13:52:00Z",
    });
    expect(bannerText({ busy: false, state: null, operation: null, interrupted: [interrupted] }, true)).toBe(
      `Compute deployment was interrupted by a server restart at ${time("2026-11-21T13:52:00Z")}.`,
    );
  });

  test("nothing running and nothing to announce is no banner", () => {
    expect(bannerText({ busy: false, state: null, operation: null, interrupted: [] }, true)).toBeNull();
  });

  test("an unreadable status keeps actions unavailable (M3)", () => {
    expect(bannerText(null, true)).toBe(
      "bioAF cannot confirm whether an infrastructure action is running. Actions stay unavailable until it can. bioAF keeps checking.",
    );
  });
});

describe("disabled control tooltip (M3)", () => {
  test("names the running operation", () => {
    expect(blockedReason({ busy: true, state: "running", operation: op(), interrupted: [] })).toBe(
      "Unavailable while Compute teardown (Operation #57) runs.",
    );
  });
  test("an unconfirmed status blocks", () => {
    expect(blockedReason(null)).toBe("Unavailable until bioAF can confirm no other infrastructure action is running.");
  });
  test("nothing running blocks nothing", () => {
    expect(blockedReason({ busy: false, state: null, operation: null, interrupted: [] })).toBeNull();
  });
});

describe("status popup", () => {
  test("titles follow the outcome (M2, M7, M9, M10)", () => {
    expect(operationTitle(op())).toBe("Compute teardown");
    expect(operationTitle(op({ status: "failed", label: "Storage destroy" }))).toBe("Storage destroy failed");
    expect(operationTitle(op({ status: "interrupted", label: "Compute deployment" }))).toBe(
      "Compute deployment interrupted",
    );
    expect(operationTitle(op({ status: "recovery_required", label: "Storage destroy" }))).toBe(
      "Storage destroy needs recovery",
    );
    expect(operationTitle(op({ status: "succeeded", action: "storage_destroy", label: "Storage destroy" }))).toBe(
      "Storage destroyed",
    );
    expect(operationTitle(op({ status: "cancelled", label: "Storage destroy" }))).toBe("Storage destroy cancelled");
  });

  test("step states read as signed off (M2, M8)", () => {
    expect(stepStateText({ key: "a", label: "A", state: "done" })).toBe("Done");
    expect(stepStateText({ key: "a", label: "A", state: "waiting" })).toBe("Waiting");
    expect(stepStateText({ key: "a", label: "A", state: "running", completed: 12, total: 31 })).toBe("12 of 31");
    expect(stepStateText({ key: "a", label: "A", state: "running" })).toBe("Running");
    expect(stepStateText({ key: "a", label: "A", state: "failed" })).toBe("Failed");
    expect(stepStateText({ key: "a", label: "A", state: "skipped" })).toBe("Skipped");
    expect(stepStateText({ key: "a", label: "A", state: "already_removed" })).toBe("Already removed");
    expect(stepStateText({ key: "a", label: "A", state: "interrupted" })).toBe("Interrupted");
    expect(stepStateText({ key: "a", label: "A", state: "not_started" })).toBe("Not started");
    expect(stepStateText({ key: "a", label: "A", state: "stopped" })).toBe("Stopped");
  });

  test("the footer says closing does not stop it (M2, M5)", () => {
    expect(footerHint(op(), { openedFromBanner: false })).toBe("Closing this window does not stop the teardown.");
    expect(footerHint(op({ label: "Storage destroy", action: "storage_destroy" }), { openedFromBanner: false })).toBe(
      "Closing this window does not stop the storage destroy.",
    );
    expect(footerHint(op(), { openedFromBanner: true })).toBe(
      "Opened from the banner. Started by another administrator.",
    );
    expect(footerHint(op({ status: "succeeded" }), { openedFromBanner: false })).toBeNull();
  });
});

describe("conflict dialog (M6)", () => {
  test("names the operation that started first and the request that did not start", () => {
    expect(conflictCopy(op(), "check for infrastructure updates")).toEqual({
      title: "Another infrastructure action started first",
      body: `Compute teardown (Operation #57) was started by Admin A at ${time("2026-11-21T14:02:00Z")}. Your check for infrastructure updates was not started.`,
      secondary: "You can follow the teardown's progress and try again after it finishes.",
      view: "View teardown status",
    });
  });
});
