"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { SetupWizard } from "@/components/auth/SetupWizard";
import { api } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import { endSession, sessionGeneration } from "@/lib/session";
import { logError } from "@/lib/errorReporting";
import { SETUP_PERMISSION, setupDestination, type SetupDestination } from "@/lib/setupRoute";
import type { BootstrapStatus } from "@/lib/types";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Button } from "@/components/ui/Button";

interface MeResponse {
  permissions: { resource: string; action: string }[];
}

type View = { kind: "loading" } | { kind: "failed" } | { kind: "show"; destination: SetupDestination };

export default function SetupPage() {
  const router = useRouter();
  const [view, setView] = useState<View>({ kind: "loading" });

  const decide = useCallback(async () => {
    setView({ kind: "loading" });
    const generation = sessionGeneration();
    try {
      const status = await api.get<BootstrapStatus>("/api/bootstrap/status");
      const signedIn = isAuthenticated();
      let canConfigure: boolean | null = null;
      if (signedIn && !status.setup_complete && status.has_admin) {
        // Effective permission, not just a token: a key or role without it gets the notice.
        const me = await api.get<MeResponse>("/api/auth/me");
        canConfigure = me.permissions.some(
          (p) => p.resource === SETUP_PERMISSION.resource && p.action === SETUP_PERMISSION.action,
        );
      }
      if (generation !== sessionGeneration()) return; // the session ended meanwhile
      const destination = setupDestination(status, signedIn, canConfigure);
      if (destination === "login") {
        router.replace("/login");
        return;
      }
      if (destination === "app") {
        router.replace("/");
        return;
      }
      setView({ kind: "show", destination });
    } catch (e) {
      // An expired session was already ended by the API client, which returns to sign-in.
      if (generation !== sessionGeneration()) return;
      logError("loading the saved setup", e);
      setView({ kind: "failed" });
    }
  }, [router]);

  useEffect(() => {
    void decide();
  }, [decide]);

  const signOut = () => {
    endSession();
    router.replace("/login");
  };

  if (view.kind === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  let body: React.ReactNode;
  if (view.kind === "failed") {
    body = (
      <div className="bg-white shadow rounded-lg p-8 space-y-4">
        <p className="text-sm text-gray-700">Your saved setup couldn&apos;t be loaded.</p>
        <div className="flex items-center gap-4">
          <Button onClick={() => void decide()}>Try again</Button>
          <button type="button" onClick={signOut} className="text-sm text-gray-500 hover:text-gray-700">
            Sign out
          </button>
        </div>
      </div>
    );
  } else if (view.destination === "setup-unauthorized") {
    body = (
      <div className="bg-white shadow rounded-lg p-8 space-y-4">
        <h2 className="text-xl font-semibold">Setup isn&apos;t finished yet</h2>
        <p className="text-sm text-gray-700">
          An administrator needs to finish setting up bioAF before you can use it.
        </p>
        <Button onClick={signOut}>Sign out</Button>
      </div>
    );
  } else {
    body = (
      <SetupWizard
        mode={view.destination === "setup-resume" ? "resume" : "initial"}
        onComplete={() => router.push("/")}
      />
    );
  }

  return (
    <div className="min-h-screen bg-canvas">
      <div className="max-w-3xl mx-auto py-12 px-4">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-bioaf-700">bioAF Setup</h1>
          <p className="text-gray-500 mt-2">Configure your platform</p>
        </div>
        {body}
      </div>
    </div>
  );
}
