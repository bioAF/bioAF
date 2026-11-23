"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { LoginForm } from "@/components/auth/LoginForm";
import { api } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";
import { startSession } from "@/lib/session";
import { setupDestination } from "@/lib/setupRoute";
import type { LoginResponse, BootstrapStatus } from "@/lib/types";

/** The status, or null when it cannot be read (the sign-in form is then still offered). */
async function readStatus(): Promise<BootstrapStatus | null> {
  try {
    return await api.get<BootstrapStatus>("/api/bootstrap/status");
  } catch {
    return null; // Backend may not be available
  }
}

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function checkSetup() {
      const status = await readStatus();
      if (isAuthenticated()) {
        // Already signed in: to an unfinished setup if there is one, otherwise the app.
        router.push(status && !status.setup_complete ? "/setup" : "/");
        return;
      }
      // Only an installation with no administrator starts in the first-run wizard; one whose
      // administrator exists signs in here even while setup is unfinished.
      if (status && setupDestination(status, false, null) === "setup-initial") {
        router.push("/setup");
        return;
      }
      setLoading(false);
    }
    checkSetup();
  }, [router]);

  const handleLogin = async (email: string, password: string) => {
    setError("");
    try {
      const response = await api.post<LoginResponse>("/api/auth/login", {
        email,
        password,
      });
      startSession(response.access_token);
      const status = await readStatus();
      router.push(status && !status.setup_complete ? "/setup" : "/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    }
  };

  if (loading) return null;

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-bioaf-700">bioAF</h1>
          <p className="text-gray-500 mt-2">Computational Biology Platform</p>
        </div>
        <LoginForm onSubmit={handleLogin} error={error} />
      </div>
    </div>
  );
}
