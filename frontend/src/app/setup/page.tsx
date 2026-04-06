"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { SetupWizard } from "@/components/auth/SetupWizard";
import { api } from "@/lib/api";
import type { BootstrapStatus } from "@/lib/types";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

export default function SetupPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function checkStatus() {
      try {
        const status = await api.get<BootstrapStatus>("/api/bootstrap/status");
        if (status.has_admin) {
          // Admin exists -- redirect to login (setup was either completed
          // or interrupted after admin creation)
          router.push("/login");
          return;
        }
      } catch {
        // Continue with setup
      }
      setLoading(false);
    }
    checkStatus();
  }, [router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-2xl mx-auto py-12 px-4">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-bioaf-700">bioAF Setup</h1>
          <p className="text-gray-500 mt-2">Configure your platform</p>
        </div>
        <SetupWizard onComplete={() => router.push("/")} />
      </div>
    </div>
  );
}
